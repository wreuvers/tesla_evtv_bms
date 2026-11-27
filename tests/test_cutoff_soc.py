"""Offline tests for v1.3.11 cutoff SoC and hours-to-empty math. No network."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "tesla_evtv_bms"))
from cutoff import estimate_cutoff_soc, tesla_96s_vmin_soc, nmc_soc


def check(name, cond, detail=""):
    if not cond:
        raise SystemExit(f"FAIL {name} {detail}")
    print(f"PASS {name}")


def near(a, b, tol=0.6):
    return abs(a - b) <= tol


def test_nov25_trip():
    # 2026-11-25 14:42 EST: lowest cell hit the 3.20 V EVTV setting at 8.5% SoC.
    # Average cell was still 3.345 V; load was light (~-577 W).
    s = estimate_cutoff_soc(
        v_min=3.2, v_avg=3.345, soc=8.5, v_cut=3.2, cells_in_series=96,
    )
    check("nov25 at cutoff returns live SoC", near(s, 8.5, 0.05), s)


def test_mid_range_projects_to_6_10():
    # Earlier the same evening: ~3.69 V min, ~3.74 V avg, 42.5% BMS SoC.
    s = estimate_cutoff_soc(
        v_min=3.688, v_avg=3.742, soc=42.5, v_cut=3.2, cells_in_series=96,
    )
    check("mid-range cutoff in 6-10%", 6.0 <= s <= 10.5, s)
    check("mid-range near 8.5%", near(s, 8.5, 1.5), s)


def test_30day_3v70():
    # 30-day median: vmin 3.70 V → SoC ~41%.
    s = estimate_cutoff_soc(
        v_min=3.70, v_avg=3.75, soc=41.0, v_cut=3.2, cells_in_series=96,
    )
    check("3.70V median projects ~8.5%", near(s, 8.5, 1.5), s)


def test_override():
    s = estimate_cutoff_soc(
        v_min=3.69, v_avg=3.74, soc=42.5, v_cut=3.2, override=12.0, cells_in_series=96,
    )
    check("override wins", s == 12.0, s)


def test_12s_uses_nmc_fallback_not_96s_table():
    # A 12S pack near a 3.6 V cutoff should not inherit the 96S 8.5% floor.
    s = estimate_cutoff_soc(
        v_min=3.622, v_avg=3.69, soc=67.0, v_cut=3.6, cells_in_series=12,
    )
    check("12S cutoff well above 8.5%", s > 20, s)
    check("12S cutoff below current SoC", s <= 67.0, s)


def test_hours_formula():
    pack_size = 75.0
    soc = 42.5
    cutoff = 8.5
    pwr_w = 1435.1
    available = pack_size * soc / 100.0
    usable = max(available - pack_size * cutoff / 100.0, 0.0)
    hours = round(usable / (pwr_w / 1000.0), 2)
    check("available 31.88", near(available, 31.875, 0.01), available)
    check("usable 25.5", near(usable, 25.5, 0.01), usable)
    check("hours ~17.8", near(hours, 17.77, 0.05), hours)


def test_curve_anchor():
    check("table 3.20 V is 8.5%", tesla_96s_vmin_soc(3.20) == 8.5)
    check("nmc 3.20 V is 0% (rest OCV, not this pack)", nmc_soc(3.20) == 0)


if __name__ == "__main__":
    test_nov25_trip()
    test_mid_range_projects_to_6_10()
    test_30day_3v70()
    test_override()
    test_12s_uses_nmc_fallback_not_96s_table()
    test_hours_formula()
    test_curve_anchor()
    print("ALL PASS")
