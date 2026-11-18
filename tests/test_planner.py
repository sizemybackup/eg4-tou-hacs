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


def test_validate_peaks_and_winter_dates():
    from custom_components.eg4_tou.planner import validate_peaks, winter_dates, tariff_from_custom
    assert validate_peaks([("07:00", "11:00"), ("17:00", "21:00")]) is None
    assert validate_peaks([("07:00", "11:00"), ("10:00", "12:00")]) == "peak_overlap"
    assert validate_peaks([("11:00", "07:00")]) == "peak_order"
    assert validate_peaks([("01:00", "02:00"), ("03:00", "04:00"), ("05:00", "06:00")]) == "too_many_peaks"
    assert winter_dates((6, 1), (9, 30)) == ((10, 1), (5, 31))
    assert winter_dates((1, 1), (12, 31)) is None
    with pytest.raises(ValueError):
        tariff_from_custom({"seasons": {"winter": {"start": [1, 1], "end": [12, 31]}},
                            "peaks": {"winter": [["12:00", "11:00"]]}})


def test_custom_tariff_matches_bge_preset_output():
    from custom_components.eg4_tou.planner import tariff_from_custom
    t = tariff_from_custom({
        "seasons": {"summer": {"start": [6, 1], "end": [9, 30]}, "winter": {"start": [10, 1], "end": [5, 31]}},
        "peaks": {"summer": [["10:00", "20:00"]], "winter": [["07:00", "11:00"], ["17:00", "21:00"]]}})
    for d in (date(2026, 7, 15), date(2026, 12, 2)):
        assert build_plan(d, t, Settings(), offpeak_day=False).registers == \
               build_plan(d, BGE_RL, Settings(), offpeak_day=False).registers


def test_rules_table_plan_vs_inverter():
    from custom_components.eg4_tou.planner import rules_table
    p = build_plan(date(2026, 11, 18), BGE_RL, Settings(), offpeak_day=False)
    rows = rules_table(p, dict(p.registers), "07:00-11:00, 17:00-21:00")
    by = {r["rule"]: r for r in rows}
    assert by["On-peak hours (your tariff)"]["plan"] == "07:00-11:00, 17:00-21:00"
    assert by["Hold windows (AC First)"]["plan"] == "00:00-07:00, 11:00-17:00, 21:00-23:59"
    assert by["Overnight charge window"]["plan"] == "01:00-05:00"      # unused slots skipped
    assert by["Charge starts below / stops at"]["plan"] == "65% to 70%"
    assert by["AC charge power"]["plan"] == "5 kW"
    assert all(r["in_sync"] for r in rows if r["in_sync"] is not None)
    # inverter differs / unread
    cur = dict(p.registers); cur[161] = 80; del cur[66]
    by = {r["rule"]: r for r in rules_table(p, cur, "")}
    assert by["Charge starts below / stops at"]["now"] == "65% to 80%" and by["Charge starts below / stops at"]["in_sync"] is False
    assert by["AC charge power"]["now"] == "unknown" and by["AC charge power"]["in_sync"] is False


def test_timeline_structured_windows():
    from custom_components.eg4_tou.planner import timeline
    p = build_plan(date(2026, 11, 18), BGE_RL, Settings(), offpeak_day=False)
    tl = timeline(p, dict(p.registers), BGE_RL.peaks_for(date(2026, 11, 18)))
    assert tl["peaks"] == [["07:00", "11:00"], ["17:00", "21:00"]]
    assert tl["hold"]["plan"] == [["00:00", "07:00"], ["11:00", "17:00"], ["21:00", "23:59"]]
    assert tl["charge"]["plan"] == [["01:00", "05:00"]] and tl["charge"]["inverter"] == tl["charge"]["plan"]
    cur = dict(p.registers); del cur[152]
    assert timeline(p, cur, [])["hold"]["inverter"] is None      # unread -> None, not a wrong bar
    assert timeline(p, cur, [])["peaks"] == []
