# cronwise

**Explain, validate and preview cron schedules from your terminal.**

cronwise reads a cron expression and tells you, in plain English, when it runs. It lists the next runs, finds collisions between jobs, warns about schedules that never fire, and checks a whole crontab file line by line. It uses only the Python standard library, reads no network, and needs no account.

## Features

- Parses all five standard fields, with clear errors that name the field and the bad value
- Lists (`5,10`), ranges (`9-17`), and steps (`*/15`, `1-10/3`, `5/20`)
- Month and weekday names (`JAN`, `mon-fri`), case-insensitive
- Weekday `7` and `0` both mean Sunday
- Macros: `@hourly`, `@daily`, `@weekly`, `@monthly`, `@yearly`, `@reboot`
- Vixie-style day rules: when both day fields are set, a run happens if **either** matches
- Plain-English explanation of any schedule
- Next N run times after any moment, or previous N before it
- Every run inside a time window, with a `--limit`
- Lint warnings: schedules that never fire, days skipped by short months, uneven minute steps
- Collision finder: the times at which two schedules fire together
- Crontab checker: validates every job line and reports errors by line number
- System crontab support (`--system`) for files with a user column
- A search horizon that reports impossible schedules instead of looping forever
- Injectable reference time (`--from`), so output is reproducible in scripts and tests

## Quick start

```sh
python3 cronwise.py explain "30 9 * * 1-5"
```

```
At 09:30, on Mon, Tue, Wed, Thu, Fri.
```

```sh
python3 cronwise.py next "0 9 * * *" --from "2026-10-17 10:00" -n 2
```

```
2026-10-18 09:00 Sun
2026-10-19 09:00 Mon
```

```sh
python3 cronwise.py lint "0 0 30 2 *"
```

```
warning: '0 0 30 2 *' never fires within 9 years
```

Check a whole crontab file (the bundled sample has one deliberate error on line 14):

```sh
python3 cronwise.py check examples/sample.crontab --from "2026-10-17 10:00"
```

## Commands

| Command | What it does |
| --- | --- |
| `explain EXPR` | Describe the schedule in plain English |
| `lint EXPR` | Warn about schedules that never fire or look wrong (exit 1 on warnings) |
| `next EXPR [-n N] [--from DT]` | Show the next N runs strictly after a moment |
| `prev EXPR [-n N] [--from DT]` | Show the N runs strictly before a moment |
| `window EXPR --start DT --end DT [--limit N]` | List every run in a time range, inclusive |
| `compare A B [--days N] [--from DT]` | Find times when both schedules fire together |
| `check FILE [--system] [--from DT]` | Validate each job line of a crontab (`-` reads stdin) |

Date and time arguments accept `2026-10-17` or `2026-10-17 10:00`.

Exit codes: `0` on success, `1` for an invalid schedule, a lint warning, or a crontab with errors.

## How to run

Python 3.8 or newer. No install step:

```sh
python3 cronwise.py --help
```

## How to test

The tests use pytest, which is the only third-party dependency:

```sh
pip install pytest
python3 -m pytest
```

The tests cover parsing, the day-matching rules, leap years, impossible schedules, strict and inclusive time boundaries, lint, crontab line numbers, and the command-line exit codes.

## Notes

- Times are naive wall-clock values, the same model cron uses. There is no time zone or daylight-saving handling.
- The search horizon is nine years, enough to find a February 29 across a century year.
