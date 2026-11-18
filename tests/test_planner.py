"""Planner tests. Expected values are what the author's live controller wrote
to the inverter (verified 2026-11-17: regs 152-157 = 00:00,07:00,11:00,17:00,21:00,23:59)."""
from datetime import date

import pytest

from custom_components.eg4_tou.modbus import pack_time, unpack_time
from custom_components.eg4_tou.planner import (
    BGE_RL, Settings, ac_first_windows, build_plan,
)


def times(plan, regs):
    return [unpack_time(plan.registers[r]) for r in regs]


def test_winter_weekday_matches_live_controller():
    p = build_plan(date(2026, 11, 18), BGE_RL, Settings(), offpeak_day=False)
    assert p.profile == "winter"
    assert times(p, range(152, 158)) == ["00:00", "07:00", "11:00", "17:00", "21:00", "23:59"]
    assert times(p, (68, 69)) == ["01:00", "05:00"]
    assert (p.registers[160], p.registers[161], p.registers[66]) == (65, 70, 50)


def test_summer_weekday():
    p = build_plan(date(2026, 7, 15), BGE_RL, Settings(), offpeak_day=False)
    assert p.profile == "summer"
    assert times(p, range(152, 158)) == ["00:00", "10:00", "20:00", "23:59", "00:00", "00:00"]
    assert (p.registers[160], p.registers[161]) == (75, 80)


def test_weekend_holiday_holds_all_day():
    p = build_plan(date(2026, 11, 21), BGE_RL, Settings(), offpeak_day=True)
    assert p.profile == "offpeak"
    assert times(p, range(152, 158)) == ["00:00", "23:59", "00:00", "00:00", "00:00", "00:00"]
    assert p.registers[161] == 50


def test_tariff_shift_week_moves_winter_peaks_one_hour():
    # Last Sunday of Oct 2026 is Oct 25 -> shift period until Nov 1
    p = build_plan(date(2026, 10, 28), BGE_RL, Settings(), offpeak_day=False)
    assert times(p, range(152, 158)) == ["00:00", "08:00", "12:00", "18:00", "22:00", "23:59"]
    # Nov 2 is outside the shift
    p = build_plan(date(2026, 11, 2), BGE_RL, Settings(), offpeak_day=False)
    assert times(p, (152, 153)) == ["00:00", "07:00"]


def test_summer_is_never_shifted():
    p = build_plan(date(2026, 6, 10), BGE_RL, Settings(), offpeak_day=False)
    assert times(p, (153,)) == ["10:00"]


def test_diff_only_lists_changes_and_orders_soc_safely():
    p = build_plan(date(2026, 11, 18), BGE_RL, Settings(), offpeak_day=False, current_end_soc=80)
    current = dict(p.registers)
    assert p.diff(current) == []
    current[161], current[160] = 80, 75
    changes = [c[0] for c in p.diff(current)]
    assert changes == [160, 161]  # lowering: start first
    p = build_plan(date(2026, 11, 18), BGE_RL, Settings(end_soc={"winter": 90}), offpeak_day=False, current_end_soc=70)
    current = {r: v for r, v in p.registers.items()}
    current[161], current[160] = 70, 65
    assert [c[0] for c in p.diff(current)] == [161, 160]  # raising: end first


def test_more_than_two_peaks_rejected():
    with pytest.raises(ValueError):
        ac_first_windows((("01:00", "02:00"), ("05:00", "06:00"), ("09:00", "10:00")))


def test_pack_roundtrip():
    assert unpack_time(pack_time("17:45")) == "17:45"
