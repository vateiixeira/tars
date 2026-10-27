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


def test_clock_marks_clamp_extrapolation_to_next_mark():
    m = ClockMarks(T0)
    m.mark(100.0, T0 + timedelta(seconds=100))
    m.mark(110.0, T0 + timedelta(seconds=3710))  # 1 h suspenso; marca 10 s após retomar
    # print 2 s após retomar: extrapolar de 100 daria 3702 s, além da marca seguinte
    assert m.to_rel(T0 + timedelta(seconds=3702)) == 110.0
    assert m.to_rel(T0 + timedelta(seconds=105)) == 105.0
    assert m.to_rel(T0 + timedelta(seconds=3712)) == 112.0


def test_clock_marks_never_go_below_previous_mark():
    m = ClockMarks(T0)
    m.mark(100.0, T0 + timedelta(seconds=100))
    m.mark(50.0, T0 + timedelta(seconds=200))  # relógio de parede voltou (ajuste de NTP)
    m.mark(150.0, T0 + timedelta(seconds=250))
    assert m.to_rel(T0 + timedelta(seconds=120)) == 100.0  # limitado entre 100 e 50: fica em 100
