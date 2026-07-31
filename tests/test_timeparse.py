from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from alarmclock.timeparse import (
    TimeParseError,
    next_occurrence,
    parse_clock_time,
    parse_duration,
    parse_repeat,
)

NY = ZoneInfo("America/New_York")


class TestParseClockTime:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("7:00", time(7, 0)),
            ("07:00", time(7, 0)),
            ("19:30", time(19, 30)),
            ("7am", time(7, 0)),
            ("7:00am", time(7, 0)),
            ("7:30pm", time(19, 30)),
            ("12:00am", time(0, 0)),  # midnight
            ("12:00pm", time(12, 0)),  # noon
            ("12:30 AM", time(0, 30)),
        ],
    )
    def test_valid(self, text, expected):
        assert parse_clock_time(text) == expected

    @pytest.mark.parametrize("text", ["25:00", "7:60", "abc", "13pm", "0:00am", "", "7:00 xm"])
    def test_invalid(self, text):
        with pytest.raises(TimeParseError):
            parse_clock_time(text)


class TestParseDuration:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("+10m", timedelta(minutes=10)),
            ("+1h30m", timedelta(hours=1, minutes=30)),
            ("+45s", timedelta(seconds=45)),
            ("+2h", timedelta(hours=2)),
        ],
    )
    def test_valid(self, text, expected):
        assert parse_duration(text) == expected

    @pytest.mark.parametrize("text", ["10m", "+0m", "+0h0m0s", "+abc", "-10m", ""])
    def test_invalid(self, text):
        with pytest.raises(TimeParseError):
            parse_duration(text)


class TestParseRepeat:
    def test_empty_means_one_off(self):
        assert parse_repeat("") == []
        assert parse_repeat(None) == []

    def test_daily(self):
        assert parse_repeat("daily") == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

    def test_weekdays(self):
        assert parse_repeat("weekdays") == ["mon", "tue", "wed", "thu", "fri"]

    def test_weekends(self):
        assert parse_repeat("weekends") == ["sat", "sun"]

    def test_explicit_list(self):
        assert parse_repeat("mon,wed,fri") == ["mon", "wed", "fri"]

    def test_full_names_and_spacing(self):
        assert parse_repeat("Monday, Tuesday") == ["mon", "tue"]

    def test_dedupes(self):
        assert parse_repeat("mon,mon,tue") == ["mon", "tue"]

    def test_invalid_day(self):
        with pytest.raises(TimeParseError):
            parse_repeat("mon,someday")


class TestNextOccurrence:
    def test_one_off_later_today(self):
        now = datetime(2026, 7, 31, 6, 0, tzinfo=NY)  # a Friday
        result = next_occurrence(time(7, 0), [], now)
        assert result.trigger_at == datetime(2026, 7, 31, 7, 0, tzinfo=NY)
        assert result.rolled_to_tomorrow is False

    def test_one_off_already_passed_rolls_to_tomorrow(self):
        now = datetime(2026, 7, 31, 8, 0, tzinfo=NY)
        result = next_occurrence(time(7, 0), [], now)
        assert result.trigger_at == datetime(2026, 8, 1, 7, 0, tzinfo=NY)
        assert result.rolled_to_tomorrow is True

    def test_one_off_exact_now_counts_as_passed(self):
        now = datetime(2026, 7, 31, 7, 0, tzinfo=NY)
        result = next_occurrence(time(7, 0), [], now)
        assert result.trigger_at == datetime(2026, 8, 1, 7, 0, tzinfo=NY)

    def test_recurring_later_today_matches_today(self):
        now = datetime(2026, 7, 31, 6, 0, tzinfo=NY)  # Friday
        result = next_occurrence(time(7, 0), ["fri"], now)
        assert result.trigger_at.date() == now.date()

    def test_recurring_time_passed_today_rolls_to_next_week(self):
        now = datetime(2026, 7, 31, 20, 0, tzinfo=NY)  # Friday night, alarm was 7am
        result = next_occurrence(time(7, 0), ["fri"], now)
        assert result.trigger_at == datetime(2026, 8, 7, 7, 0, tzinfo=NY)

    def test_recurring_wraps_week_boundary(self):
        # Friday evening, the only repeat day is Monday -> next Monday, not this week.
        now = datetime(2026, 7, 31, 21, 0, tzinfo=NY)
        result = next_occurrence(time(7, 0), ["mon"], now)
        assert result.trigger_at.strftime("%A") == "Monday"
        assert result.trigger_at.date() > now.date()
        assert (result.trigger_at.date() - now.date()).days <= 7

    def test_dst_spring_forward_preserves_wall_clock_time(self):
        # 2024-03-10 in America/New_York: clocks jump 2:00am -> 3:00am.
        now = datetime(2024, 3, 9, 22, 0, tzinfo=NY)  # the day before
        result = next_occurrence(time(7, 30), [], now)
        assert result.trigger_at == datetime(2024, 3, 10, 7, 30, tzinfo=NY)
        # still 7:30 local wall-clock, not shifted by the DST jump
        assert result.trigger_at.hour == 7

    def test_dst_fall_back_preserves_wall_clock_time(self):
        # 2024-11-03 in America/New_York: clocks fall back 2:00am -> 1:00am.
        now = datetime(2024, 11, 2, 22, 0, tzinfo=NY)
        result = next_occurrence(time(7, 30), [], now)
        assert result.trigger_at == datetime(2024, 11, 3, 7, 30, tzinfo=NY)
        assert result.trigger_at.hour == 7
