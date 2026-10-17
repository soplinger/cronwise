import datetime as dt

import pytest

from cronwise import CronError, DOM, DOW, MINUTE, Schedule, check_crontab, main, parse_field


def at(text):
    return dt.datetime.fromisoformat(text)


# --- parsing -----------------------------------------------------------------

def test_rejects_the_wrong_number_of_fields():
    with pytest.raises(CronError, match="expected 5 fields, found 4"):
        Schedule("* * * *")


def test_out_of_range_error_names_the_field():
    with pytest.raises(CronError, match="minute: 60 is out of range 0-59"):
        Schedule("60 * * * *")
    with pytest.raises(CronError, match="hour: 24 is out of range 0-23"):
        Schedule("0 24 * * *")


def test_backwards_range_is_an_error():
    with pytest.raises(CronError, match="runs backwards"):
        Schedule("0 10-5 * * *")


def test_zero_step_is_an_error():
    with pytest.raises(CronError, match="step '0'"):
        Schedule("*/0 * * * *")


def test_non_ascii_digits_are_rejected_cleanly():
    with pytest.raises(CronError, match="is not a number"):
        Schedule("² * * * *")
    with pytest.raises(CronError, match="must be a whole number"):
        Schedule("*/² * * * *")


def test_lists_ranges_and_steps():
    assert parse_field("5,10-12", MINUTE) == {5, 10, 11, 12}
    assert parse_field("*/15", MINUTE) == {0, 15, 30, 45}
    assert parse_field("1-10/3", DOM) == {1, 4, 7, 10}


def test_single_value_with_step_runs_to_the_top_of_the_range():
    assert parse_field("5/20", MINUTE) == {5, 25, 45}


def test_month_and_weekday_names_are_case_insensitive():
    sched = Schedule("0 9 * jan-MAR Mon-Fri")
    assert sched.months == {1, 2, 3}
    assert sched.dows == {1, 2, 3, 4, 5}


def test_weekday_seven_means_sunday():
    assert parse_field("0,7", DOW) == {0}
    assert parse_field("7", DOW) == {0}
    assert Schedule("0 9 * * 7").next_runs(at("2026-10-17 10:00"), 1) == [at("2026-10-18 09:00")]


def test_macros_expand_and_unknown_macros_fail():
    assert Schedule("@daily").raw == ["0", "0", "*", "*", "*"]
    assert Schedule("@hourly").next_runs(at("2026-10-17 10:20"), 1) == [at("2026-10-17 11:00")]
    with pytest.raises(CronError, match="unknown macro"):
        Schedule("@fortnightly")


# --- run-time logic ----------------------------------------------------------

def test_day_of_month_and_weekday_combine_with_or_when_both_are_set():
    # Fridays and the 13th, OR'd together: Oct 2, Oct 9, then Tue Oct 13.
    runs = Schedule("0 12 13 * FRI").next_runs(at("2026-10-01 00:00"), 3)
    assert runs == [at("2026-10-02 12:00"), at("2026-10-09 12:00"), at("2026-10-13 12:00")]


def test_day_of_month_and_weekday_combine_with_and_when_one_is_a_star():
    # */13 means days 1, 14, 27. The first Friday on one of those days is Nov 27.
    runs = Schedule("0 12 */13 * FRI").next_runs(at("2026-10-01 00:00"), 1)
    assert runs == [at("2026-11-27 12:00")]


def test_leap_day_is_found_years_ahead():
    assert Schedule("0 0 29 2 *").next_runs(at("2097-03-01 00:00"), 1) == [at("2104-02-29 00:00")]


def test_impossible_schedule_reports_it_instead_of_looping():
    with pytest.raises(CronError, match="may never fire"):
        Schedule("0 0 30 2 *").next_runs(at("2026-10-17 10:00"))


def test_next_runs_are_strictly_after_the_reference_time():
    assert Schedule("* * * * *").next_runs(at("2026-10-17 10:00:30"), 1) == [at("2026-10-17 10:01")]
    assert Schedule("0 * * * *").next_runs(at("2026-10-17 10:00"), 1) == [at("2026-10-17 11:00")]


def test_prev_runs_are_strictly_before_the_reference_time():
    assert Schedule("0 * * * *").prev_runs(at("2026-10-17 10:00"), 2) == [
        at("2026-10-17 09:00"), at("2026-10-17 08:00")]
    assert Schedule("0 * * * *").prev_runs(at("2026-10-17 10:30"), 1) == [at("2026-10-17 10:00")]


def test_window_is_inclusive_at_both_ends():
    runs = list(Schedule("*/30 * * * *").runs_between(at("2026-10-17 10:00"), at("2026-10-17 11:00")))
    assert runs == [at("2026-10-17 10:00"), at("2026-10-17 10:30"), at("2026-10-17 11:00")]


def test_overlapping_runs_are_found():
    hits = list(Schedule("0 * * * *").overlaps(
        Schedule("*/30 * * * *"), at("2026-10-17 10:00"), at("2026-10-17 12:00")))
    assert hits == [at("2026-10-17 10:00"), at("2026-10-17 11:00"), at("2026-10-17 12:00")]


def test_reboot_has_no_clock_times():
    assert Schedule("@reboot").explain() == "Runs once, at system startup."
    with pytest.raises(CronError, match="startup"):
        Schedule("@reboot").next_runs(at("2026-10-17 10:00"))


# --- explain and lint --------------------------------------------------------

def test_explain_in_plain_english():
    assert Schedule("30 9 * * 1-5").explain() == "At 09:30, on Mon, Tue, Wed, Thu, Fri."
    assert Schedule("*/15 * * * *").explain() == "Every 15 minutes, every day."
    assert Schedule("@daily").explain() == "At 00:00, every day. (@daily means '0 0 * * *')"


def test_lint_flags_schedules_that_never_fire():
    assert ("warning", "'0 0 30 2 *' never fires within 9 years") in Schedule("0 0 30 2 *").lint()


def test_lint_flags_days_skipped_by_short_months():
    notes = Schedule("0 0 31 1,2 *").lint()
    assert ("warning", "day 31 is skipped in Feb because those months are shorter") in notes


def test_lint_warns_about_uneven_minute_steps():
    assert [level for level, _ in Schedule("*/7 * * * *").lint()] == ["warning"]


def test_lint_is_quiet_for_a_clean_schedule():
    assert Schedule("15 8 * * 1-5").lint() == []


# --- crontab files -----------------------------------------------------------

CRONTAB = [
    "# nightly jobs",
    "",
    "MAILTO=ops@example.com",
    "0 9 * * 1-5 /usr/local/bin/backup.sh",
    "61 * * * * /bin/false",
    "@daily",
    "*/5 * * * * /bin/true",
]


def test_crontab_check_reports_line_numbers():
    results = check_crontab(CRONTAB, at("2026-10-17 10:00"))
    assert [(number, ok) for number, ok, _ in results] == [(4, True), (5, False), (6, False), (7, True)]
    assert "minute: 61 is out of range" in results[1][2]
    assert "missing command" in results[2][2]


def test_system_crontab_expects_a_user_column():
    lines = ["0 9 * * * root /bin/true", "0 9 * * * /bin/true"]
    results = check_crontab(lines, at("2026-10-17 10:00"), system=True)
    assert [ok for _, ok, _ in results] == [True, False]


# --- command line ------------------------------------------------------------

def test_next_command_prints_runs_after_a_fixed_time(capsys):
    assert main(["next", "0 9 * * *", "--from", "2026-10-17 10:00", "-n", "2"]) == 0
    assert capsys.readouterr().out.splitlines() == ["2026-10-18 09:00 Sun", "2026-10-19 09:00 Mon"]


def test_bad_expression_exits_nonzero_with_a_message(capsys):
    assert main(["next", "60 * * * *"]) == 1
    assert "minute: 60 is out of range 0-59" in capsys.readouterr().err


def test_lint_command_exits_nonzero_on_warnings(capsys):
    assert main(["lint", "0 0 30 2 *"]) == 1
    assert main(["lint", "15 8 * * 1-5"]) == 0
    assert "ok: no problems found" in capsys.readouterr().out


def test_compare_command_reports_overlaps(capsys):
    assert main(["compare", "0 * * * *", "*/30 * * * *", "--from", "2026-10-17 10:00", "--days", "1"]) == 0
    assert "more than 20 overlapping run(s)" in capsys.readouterr().out


def test_compare_rejects_a_bad_day_count(capsys):
    assert main(["compare", "0 * * * *", "0 * * * *", "--days", "0"]) == 1
    assert "--days must be between 1 and 366" in capsys.readouterr().err


def test_check_command_exit_code_follows_errors(tmp_path, capsys):
    good = tmp_path / "good.crontab"
    good.write_text("0 3 * * * /bin/true\n", encoding="utf-8")
    assert main(["check", str(good), "--from", "2026-10-17 10:00"]) == 0

    bad = tmp_path / "bad.crontab"
    bad.write_text("0 3 * * * /bin/true\n99 * * * * /bin/true\n", encoding="utf-8")
    assert main(["check", str(bad), "--from", "2026-10-17 10:00"]) == 1
    assert "line 2: ERROR" in capsys.readouterr().out


def test_window_command_respects_the_limit(capsys):
    assert main(["window", "* * * * *", "--start", "2026-10-17 10:00",
                 "--end", "2026-10-17 10:10", "--limit", "3"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:3] == ["2026-10-17 10:00 Sat", "2026-10-17 10:01 Sat", "2026-10-17 10:02 Sat"]
    assert out[3].startswith("... more runs follow")
