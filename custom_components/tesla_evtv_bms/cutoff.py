"""Hours-to-empty cutoff SoC from the EVTV min-cell setting.

The controller's configured low-cell limit is not on the UDP CAN frames
this integration parses (0x150 / 0x151 / 0x650 / 0x651 / 0x683). It is the
integration's min_cell_volts setting, entered to match the EVTV controller.

When the lowest cell reaches that voltage, pack BMS SoC is usually not 0%
because of cell imbalance. This module estimates that remaining SoC.

Primary method (96S Tesla NMC modules): a SoC-vs-lowest-cell reference
curve from loaded pack data, anchored to the live (min cell, BMS SoC)
point. Validated on Little Hog Cay Powerpack2:

  - 2026-11-25 14:42 EST: lowest cell hit 3.20 V at 8.5% SoC (light load,
    average cell still 3.345 V).
  - 30-day medians: 3.20–3.24 V occurred at about 6–10% SoC.

Fallback (any pack): Li-NMC rest-OCV curve with the live min-vs-average
offset, scaled to the pack's current BMS SoC.

An optional cutoff_soc_override skips estimation.
"""
from __future__ import annotations

# Standard Li-NMC / Tesla module rest OCV → SoC (%), piecewise-linear.
NMC_OCV = (
    (3.00, 0), (3.20, 0), (3.30, 2), (3.35, 4), (3.40, 7), (3.45, 11),
    (3.50, 16), (3.55, 22), (3.60, 30), (3.62, 35), (3.65, 40), (3.70, 50),
    (3.75, 55), (3.80, 60), (3.85, 65), (3.90, 70), (3.95, 75), (4.00, 80),
    (4.05, 85), (4.10, 90), (4.15, 95), (4.20, 100),
)

# Loaded Tesla 96S pack, SoC vs *lowest* cell. Anchored at the 2026-11-25
# 3.20 V / 8.5% trip; other points are 30-day medians from the same pack.
TESLA_96S_VMIN_SOC = (
    (3.00, 0.0),
    (3.10, 4.0),
    (3.20, 8.5),
    (3.26, 10.5),
    (3.30, 11.0),
    (3.36, 12.0),
    (3.40, 12.5),
    (3.44, 13.8),
    (3.46, 15.0),
    (3.50, 22.0),
    (3.52, 26.0),
    (3.54, 27.0),
    (3.56, 31.5),
    (3.60, 32.5),
    (3.62, 33.5),
    (3.64, 35.0),
    (3.66, 37.5),
    (3.68, 39.0),
    (3.70, 41.0),
    (3.72, 41.5),
    (3.74, 45.5),
    (3.76, 47.5),
    (3.78, 49.5),
    (3.80, 51.5),
    (3.82, 54.0),
    (4.10, 90.0),
    (4.20, 100.0),
)


def _interp(v: float, table: tuple[tuple[float, float], ...]) -> float:
    if v <= table[0][0]:
        return table[0][1]
    if v >= table[-1][0]:
        return table[-1][1]
    for i in range(len(table) - 1):
        v0, s0 = table[i]
        v1, s1 = table[i + 1]
        if v0 <= v <= v1:
            if v1 == v0:
                return s0
            return s0 + (s1 - s0) * (v - v0) / (v1 - v0)
    return table[-1][1]


def nmc_soc(v: float) -> float:
    """Rest OCV → SoC (%) for a typical Li-NMC / Tesla module."""
    return _interp(v, NMC_OCV)


def tesla_96s_vmin_soc(v: float) -> float:
    """Loaded 96S pack SoC (%) vs lowest cell voltage (validated table)."""
    return _interp(v, TESLA_96S_VMIN_SOC)


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _nmc_imbalance_cutoff(
    v_min: float | None,
    v_avg: float | None,
    soc: float,
    v_cut: float,
) -> float:
    """Scale the NMC curve through the live point; keep min-vs-avg offset."""
    v_ref = v_avg if v_avg is not None else v_min
    if v_ref is None:
        return 0.0
    ref = nmc_soc(v_ref)
    if ref <= 0:
        return 0.0
    scale = soc / ref
    if v_min is not None and v_avg is not None and v_avg > v_min:
        raw = scale * (nmc_soc(v_cut) + nmc_soc(v_avg) - nmc_soc(v_min))
    elif v_min is not None:
        raw = soc - scale * (nmc_soc(v_min) - nmc_soc(v_cut))
    else:
        raw = scale * nmc_soc(v_cut)
    return _clamp(raw, 0.0, soc)


def _anchored_curve_cutoff(v_min: float, soc: float, v_cut: float) -> float:
    """Shift/scale the 96S vmin curve so it passes through (v_min, soc)."""
    now = tesla_96s_vmin_soc(v_min)
    cut = tesla_96s_vmin_soc(v_cut)
    if now <= 0:
        return _clamp(soc if v_min <= v_cut else 0.0, 0.0, soc)
    return _clamp(soc * (cut / now), 0.0, soc)


def estimate_cutoff_soc(
    *,
    v_min: float | None,
    v_avg: float | None,
    soc: float | None,
    v_cut: float | None,
    override: float | None = None,
    cells_in_series: int | None = None,
) -> float:
    """Return pack BMS SoC (%) expected when the lowest cell hits v_cut.

    override, if set, wins. If the lowest cell is already at or below v_cut,
    the current SoC is returned (the pack is at the cutoff now).
    """
    if override is not None:
        try:
            return _clamp(float(override), 0.0, 100.0)
        except (TypeError, ValueError):
            pass
    if soc is None or v_cut is None:
        return 0.0
    try:
        soc = float(soc)
        v_cut = float(v_cut)
    except (TypeError, ValueError):
        return 0.0
    if v_min is not None:
        try:
            v_min = float(v_min)
        except (TypeError, ValueError):
            v_min = None
    if v_avg is not None:
        try:
            v_avg = float(v_avg)
        except (TypeError, ValueError):
            v_avg = None

    if v_min is not None and v_min <= v_cut:
        return _clamp(soc, 0.0, 100.0)

    # Prefer the validated 96S vmin curve for Tesla 96S strings (the
    # integration default) and when series is unspecified. Other counts
    # (e.g. 12S SolArk) use the generic NMC + imbalance fallback only.
    use_96s = cells_in_series in (None, 96, 96.0)
    if v_min is not None and use_96s:
        return _anchored_curve_cutoff(v_min, soc, v_cut)

    return _nmc_imbalance_cutoff(v_min, v_avg, soc, v_cut)
