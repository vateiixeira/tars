from datetime import datetime, timedelta

from whisper_ditado.prints.timing import ClockMarks

T0 = datetime(2026, 10, 19, 10, 0, 0)


def test_clock_marks_without_gap_is_wall_difference():
    m = ClockMarks(T0)
    assert m.to_rel(T0 + timedelta(seconds=75)) == 75.0
    assert m.to_rel(T0 - timedelta(seconds=3)) == 0.0


def test_clock_marks_follow_suspend_gap():
    m = ClockMarks(T0)
    m.mark(100.0, T0 + timedelta(seconds=100))
    m.mark(110.0, T0 + timedelta(seconds=410))  # 300 s suspenso entre as marcas
    assert m.to_rel(T0 + timedelta(seconds=420)) == 120.0
