#!/usr/bin/env python3
"""cronwise: explain, validate and preview cron schedules.

Standard library only. Times are naive wall-clock values, the same model
cron itself uses, so there is no time zone or DST handling.
"""

import argparse
import calendar
import datetime as dt
import re
import sys
from itertools import islice

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

MINUTE, HOUR, DOM, MONTH, DOW = range(5)

# (label, lowest, highest, name -> number) for each of the five fields.
FIELDS = [
    ("minute", 0, 59, {}),
    ("hour", 0, 23, {}),
    ("day of month", 1, 31, {}),
    ("month", 1, 12, {name.upper(): i for i, name in enumerate(MONTHS, 1)}),
    ("day of week", 0, 7, {name.upper(): i for i, name in enumerate(WEEKDAYS)}),
]

MACROS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
    "@reboot": None,
}

ALL_WEEKDAYS = frozenset(range(7))
# Feb 29 can be eight years apart (2096 to 2104), so look nine years ahead.
SEARCH_YEARS = 9
ONE_MINUTE = dt.timedelta(minutes=1)
ONE_DAY = dt.timedelta(days=1)
ENV_LINE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")
LIST_LIMIT = 20


class CronError(ValueError):
    """An invalid schedule. The message names the field that failed."""


def _is_number(text):
    """True for plain ASCII digits. str.isdigit() also accepts '²' and other digits int() rejects."""
    return text.isascii() and text.isdigit()


def _value(token, field):
    label, low, high, names = FIELDS[field]
    if token.upper() in names:
        return names[token.upper()]
    if not _is_number(token):
        hint = " (or a name such as JAN or MON)" if names else ""
        raise CronError(f"{label}: '{token}' is not a number{hint}")
    value = int(token)
    if not low <= value <= high:
        raise CronError(f"{label}: {value} is out of range {low}-{high}")
    return value


def parse_field(text, field):
    """The set of values one cron field matches. Weekday 7 is folded into 0."""
    label, low, high, _ = FIELDS[field]
    values = set()
    for item in text.split(","):
        if not item:
            raise CronError(f"{label}: empty entry in '{text}'")
        base, slash, step_text = item.partition("/")
        step = 1
        if slash:
            if not _is_number(step_text) or int(step_text) == 0:
                raise CronError(f"{label}: step '{step_text}' in '{item}' must be a whole number above 0")
            step = int(step_text)
        if base == "*":
            first, last = low, high
        elif "-" in base:
            left, _, right = base.partition("-")
            first, last = _value(left, field), _value(right, field)
            if first > last:
                raise CronError(f"{label}: range '{base}' runs backwards")
        else:
            first = _value(base, field)
            last = high if slash else first
        for value in range(first, last + 1, step):
            values.add(value % 7 if field == DOW else value)
    return frozenset(values)


def _step(text):
    """The N in a '*/N' field, or None."""
    if text.startswith("*/") and _is_number(text[2:]):
        return int(text[2:])
    return None


def _names(values, field):
    """Sorted values, written as month or weekday names where that reads better."""
    if field == MONTH:
        return ", ".join(MONTHS[v - 1] for v in sorted(values))
    if field == DOW:
        return ", ".join(WEEKDAYS[v] for v in sorted(values))
    return ", ".join(str(v) for v in sorted(values))


def _floor(when):
    return when.replace(second=0, microsecond=0)


class Schedule:
    """A parsed cron expression that can answer questions about run times."""

    def __init__(self, expr):
        self.expr = " ".join(expr.split())
        self.macro = None
        self.reboot = False
        if self.expr.startswith("@"):
            key = self.expr.lower()
            if key not in MACROS:
                raise CronError(f"unknown macro '{self.expr}'; use one of {', '.join(MACROS)}")
            self.macro = key
            self.reboot = key == "@reboot"
            text = "* * * * *" if self.reboot else MACROS[key]
        else:
            text = self.expr
        parts = text.split()
        if len(parts) != 5:
            raise CronError(f"expected 5 fields, found {len(parts)} in '{self.expr}'")
        self.raw = parts
        self.minutes = parse_field(parts[MINUTE], MINUTE)
        self.hours = parse_field(parts[HOUR], HOUR)
        self.doms = parse_field(parts[DOM], DOM)
        self.months = parse_field(parts[MONTH], MONTH)
        self.dows = parse_field(parts[DOW], DOW)
        self._minutes = sorted(self.minutes)
        self._hours = sorted(self.hours)
        # Vixie cron rule: a leading '*' in either day field means AND, otherwise OR.
        self._dom_star = parts[DOM].startswith("*")
        self._dow_star = parts[DOW].startswith("*")

    def day_matches(self, day):
        """True if the calendar date `day` is one the schedule runs on."""
        if day.month not in self.months:
            return False
        dom_ok = day.day in self.doms
        dow_ok = day.isoweekday() % 7 in self.dows
        if self._dom_star or self._dow_star:
            return dom_ok and dow_ok
        return dom_ok or dow_ok

    def matches(self, when):
        """True if the schedule fires during the minute that contains `when`."""
        return (not self.reboot and when.minute in self.minutes
                and when.hour in self.hours and self.day_matches(when.date()))

    def _forward(self, first):
        """Matching minutes at or after `first`, in order, within the search horizon."""
        if self.reboot:
            return
        horizon = dt.date(first.year + SEARCH_YEARS, 1, 1)
        day = first.date()
        while day < horizon:
            if self.day_matches(day):
                for hour in self._hours:
                    for minute in self._minutes:
                        when = dt.datetime.combine(day, dt.time(hour, minute))
                        if when >= first:
                            yield when
            day += ONE_DAY

    def _backward(self, before):
        """Matching minutes strictly before `before`, latest first, within the search horizon."""
        if self.reboot:
            return
        floor = dt.date(before.year - SEARCH_YEARS, 1, 1)
        day = before.date()
        while day >= floor:
            if self.day_matches(day):
                for hour in reversed(self._hours):
                    for minute in reversed(self._minutes):
                        when = dt.datetime.combine(day, dt.time(hour, minute))
                        if when < before:
                            yield when
            day -= ONE_DAY

    def next_runs(self, after, count=5):
        """Up to `count` run times strictly after `after`, earliest first."""
        if self.reboot:
            raise CronError("@reboot runs at startup, so it has no clock times")
        runs = list(islice(self._forward(_floor(after) + ONE_MINUTE), count))
        if not runs:
            raise CronError(f"no run within {SEARCH_YEARS} years of {after:%Y-%m-%d %H:%M}; "
                            "the schedule may never fire")
        return runs

    def prev_runs(self, before, count=5):
        """Up to `count` run times strictly before `before`, latest first."""
        if self.reboot:
            raise CronError("@reboot runs at startup, so it has no clock times")
        runs = list(islice(self._backward(before), count))
        if not runs:
            raise CronError(f"no run within {SEARCH_YEARS} years before {before:%Y-%m-%d %H:%M}; "
                            "the schedule may never fire")
        return runs

    def runs_between(self, start, end):
        """Every run time from `start` through `end`, inclusive, earliest first."""
        first = _floor(start)
        if first < start:
            first += ONE_MINUTE
        for when in self._forward(first):
            if when > end:
                return
            yield when

    def overlaps(self, other, start, end):
        """Run times in [start, end] at which both this schedule and `other` fire."""
        return (when for when in self.runs_between(start, end) if other.matches(when))

    def explain(self):
        """A one-sentence plain-English description of when the schedule fires."""
        if self.reboot:
            return "Runs once, at system startup."
        minute, hour, dom, month, dow = self.raw
        minute_step, hour_step = _step(minute), _step(hour)
        hours_text = f"every {hour_step} hours" if hour_step else f"hour(s) {_names(self.hours, HOUR)}"
        if hour == "*" and minute == "*":
            when = "Every minute"
        elif hour == "*" and minute_step:
            when = f"Every {minute_step} minutes"
        elif hour == "*":
            when = f"At minute(s) {_names(self.minutes, MINUTE)} of every hour"
        elif minute == "*":
            when = f"Every minute, during {hours_text}"
        elif minute_step:
            when = f"Every {minute_step} minutes, during {hours_text}"
        elif len(self.minutes) == 1 and len(self.hours) == 1 and not hour_step:
            when = f"At {min(self.hours):02d}:{min(self.minutes):02d}"
        else:
            when = f"At minute(s) {_names(self.minutes, MINUTE)} past {hours_text}"

        dom_text = None
        if dom.startswith("*/"):
            dom_text = f"every {_step(dom)} days of the month"
        elif dom != "*":
            dom_text = f"on day(s) {_names(self.doms, DOM)}"
        dow_text = None if dow == "*" else f"on {_names(self.dows, DOW)}"
        if dom_text and dow_text:
            joiner = " and " if self._dom_star or self._dow_star else " or "
            day = dom_text + joiner + dow_text
        else:
            day = dom_text or dow_text or "every day"
        if month != "*":
            day += f" in {_names(self.months, MONTH)}"

        text = f"{when}, {day}."
        if self.macro:
            text += f" ({self.macro} means '{MACROS[self.macro]}')"
        return text

    def lint(self):
        """(level, message) pairs for schedules that never run or are easy to misread."""
        if self.reboot:
            return []
        if next(self._forward(dt.datetime(2000, 1, 1)), None) is None:
            return [("warning", f"'{self.expr}' never fires within {SEARCH_YEARS} years")]
        notes = []
        minute_step, hour_step = _step(self.raw[MINUTE]), _step(self.raw[HOUR])
        if minute_step and 60 % minute_step:
            notes.append(("warning", f"*/{minute_step} gives uneven gaps across the hour; "
                                     "use a step that divides 60"))
        if hour_step and 24 % hour_step:
            notes.append(("warning", f"*/{hour_step} gives uneven gaps across the day; "
                                     "use a step that divides 24"))
        both_restricted = not self._dom_star and not self._dow_star
        if both_restricted:
            notes.append(("note", "day of month and day of week are both set, "
                                  "so cron runs when EITHER matches"))
            if self.dows == ALL_WEEKDAYS:
                notes.append(("warning", "day of week lists every day, so with the EITHER rule "
                                         "this runs every day"))
        elif not self._dom_star and self.dows == ALL_WEEKDAYS:
            for day in sorted(self.doms):
                short = [m for m in sorted(self.months) if day > calendar.monthrange(2000, m)[1]]
                if short:
                    notes.append(("warning", f"day {day} is skipped in {_names(short, MONTH)} "
                                             "because those months are shorter"))
            if 29 in self.doms and 2 in self.months:
                notes.append(("note", "day 29 of February only runs in leap years"))
        return notes


def check_crontab(lines, now, system=False):
    """Validate each job line of a crontab. Returns (line number, ok, text) tuples."""
    results = []
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#") or ENV_LINE.match(line):
            continue
        tokens = line.split()
        width = 1 if tokens[0].startswith("@") else 5
        if len(tokens) <= width + (1 if system else 0):
            missing = "user or command" if system else "command"
            results.append((number, False, f"missing {missing} after the schedule"))
            continue
        expr = " ".join(tokens[:width])
        try:
            schedule = Schedule(expr)
            if schedule.reboot:
                text = schedule.explain()
            else:
                text = f"{schedule.explain()} Next: {schedule.next_runs(now, 1)[0]:%Y-%m-%d %H:%M}."
        except CronError as err:
            results.append((number, False, str(err)))
            continue
        results.append((number, True, text))
    return results


def _fmt(when):
    return f"{when:%Y-%m-%d %H:%M} {when:%a}"


def _when(text):
    try:
        return dt.datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"not a date or time: '{text}' (try 2026-10-17 or '2026-10-17 10:00')")


def _positive(text):
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError(f"must be a whole number of at least 1, not '{text}'")
    return int(text)


def cmd_explain(args):
    print(Schedule(args.expr).explain())
    return 0


def cmd_lint(args):
    notes = Schedule(args.expr).lint()
    if not notes:
        print("ok: no problems found")
        return 0
    for level, message in notes:
        print(f"{level}: {message}")
    return 1 if any(level == "warning" for level, _ in notes) else 0


def cmd_next(args):
    now = args.when or dt.datetime.now()
    for when in Schedule(args.expr).next_runs(now, args.count):
        print(_fmt(when))
    return 0


def cmd_prev(args):
    now = args.when or dt.datetime.now()
    for when in Schedule(args.expr).prev_runs(now, args.count):
        print(_fmt(when))
    return 0


def cmd_window(args):
    if args.end < args.start:
        raise CronError("--end is before --start")
    runs = list(islice(Schedule(args.expr).runs_between(args.start, args.end), args.limit + 1))
    if not runs:
        print("No runs in that window.")
    for when in runs[:args.limit]:
        print(_fmt(when))
    if len(runs) > args.limit:
        print(f"... more runs follow; raise --limit (currently {args.limit})")
    return 0


def cmd_compare(args):
    if not 1 <= args.days <= 366:
        raise CronError("--days must be between 1 and 366")
    start = args.when or dt.datetime.now()
    end = start + dt.timedelta(days=args.days)
    first, second = Schedule(args.first), Schedule(args.second)
    hits = list(islice(first.overlaps(second, start, end), LIST_LIMIT + 1))
    if not hits:
        print(f"No overlapping runs in the next {args.days} day(s).")
        return 0
    count = "more than 20" if len(hits) > LIST_LIMIT else str(len(hits))
    print(f"{count} overlapping run(s) in the next {args.days} day(s):")
    for when in hits[:LIST_LIMIT]:
        print(f"  {_fmt(when)}")
    return 0


def cmd_check(args):
    if args.file == "-":
        lines = sys.stdin.read().splitlines()
    else:
        with open(args.file, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    now = args.when or dt.datetime.now()
    results = check_crontab(lines, now, args.system)
    errors = 0
    for number, ok, text in results:
        if not ok:
            errors += 1
        print(f"line {number}: {'ok' if ok else 'ERROR'}: {text}")
    print(f"{len(results)} job line(s), {errors} error(s).")
    return 1 if errors else 0


def build_parser():
    parser = argparse.ArgumentParser(prog="cronwise",
                                     description="Explain, validate and preview cron schedules.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add(name, help_text, handler):
        sub = commands.add_parser(name, help=help_text, description=help_text)
        sub.set_defaults(handler=handler)
        return sub

    def add_from(sub):
        sub.add_argument("--from", dest="when", type=_when, metavar="DATETIME",
                         help="reference time, e.g. '2026-10-17 10:00' (default: now)")

    sub = add("explain", "describe a schedule in plain English", cmd_explain)
    sub.add_argument("expr", help="five-field cron expression or @macro")

    sub = add("lint", "warn about schedules that never fire or look wrong", cmd_lint)
    sub.add_argument("expr")

    sub = add("next", "show the next run times after a moment", cmd_next)
    sub.add_argument("expr")
    sub.add_argument("-n", "--count", type=_positive, default=5, help="how many runs (default: 5)")
    add_from(sub)

    sub = add("prev", "show the run times before a moment", cmd_prev)
    sub.add_argument("expr")
    sub.add_argument("-n", "--count", type=_positive, default=5, help="how many runs (default: 5)")
    add_from(sub)

    sub = add("window", "list every run between two times", cmd_window)
    sub.add_argument("expr")
    sub.add_argument("--start", required=True, type=_when, metavar="DATETIME")
    sub.add_argument("--end", required=True, type=_when, metavar="DATETIME")
    sub.add_argument("--limit", type=_positive, default=100, help="stop printing after N runs")

    sub = add("compare", "find times where two schedules fire together", cmd_compare)
    sub.add_argument("first")
    sub.add_argument("second")
    sub.add_argument("--days", type=int, default=7, help="how far ahead to look (1-366, default: 7)")
    add_from(sub)

    sub = add("check", "validate every job line of a crontab file", cmd_check)
    sub.add_argument("file", help="crontab path, or - for stdin")
    sub.add_argument("--system", action="store_true",
                     help="the file is a system crontab with a user column (like /etc/crontab)")
    add_from(sub)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except CronError as err:
        print(f"cronwise: error: {err}", file=sys.stderr)
        return 1
    except OSError as err:
        print(f"cronwise: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
