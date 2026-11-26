"""Pure guards for EVTV Gigavac contactor open/close (no I/O).

Used by the HA switch platform and open/close-both services. PP0 is excluded
by CONTACTOR_SWITCH_PACKS; these helpers also refuse it.
"""
from __future__ import annotations

from .const import (
    CONTACTOR_SWITCH_PACKS,
    OTHER_PACK,
    PRECHARGE_SECONDS,
    SONOFF_CONNECT,
    SYNC_CELL_MAX_DIFF,
    SYNC_IN_SYNC,
)


TOGGLE_KEY = {"negative": b"1", "positive": b"2"}
COMMANDED = {"negative": "negative_commanded", "positive": "positive_commanded"}
REPORTED = {"negative": "negative_reported", "positive": "positive_reported"}


def pack_allows_switches(name_lower: str) -> bool:
    key = "".join(ch for ch in name_lower.lower() if ch.isalnum())
    return key in CONTACTOR_SWITCH_PACKS


def sonoff_on(states: dict, name_lower: str) -> bool | None:
    """True if the pack's Sonoff connect path is on. None if unknown."""
    eid = SONOFF_CONNECT.get(name_lower)
    if not eid:
        return None
    st = states.get(eid)
    if st in (None, "unknown", "unavailable", "none", ""):
        return None
    return st == "on"


def pack_contactors_closed(contactor: dict | None) -> bool | None:
    """Both poles commanded ON. None if we lack a main-screen reading."""
    if not contactor or contactor.get("available") is not True:
        return None
    n = contactor.get("negative_commanded")
    p = contactor.get("positive_commanded")
    if n is None or p is None:
        return None
    return bool(n) and bool(p)


def other_pack_on_bus(states: dict, name_lower: str, other_contactor: dict | None) -> bool:
    """The other pack is treated as on the inverter bus.

    Sonoff connect ON, and its EVTV contactors are closed (or unknown — we
    assume they may be closed so we do not skip the sync check).
    """
    other = OTHER_PACK.get(name_lower)
    if not other:
        return False
    conn = sonoff_on(states, other)
    if conn is not True:
        return False
    closed = pack_contactors_closed(other_contactor)
    # If we cannot read the other pack's contactors, be conservative.
    return closed is not False


def this_would_be_on_bus(states: dict, name_lower: str, after: dict) -> bool:
    """After the requested close, would this pack be on the bus?"""
    conn = sonoff_on(states, name_lower)
    if conn is not True:
        return False
    return pack_contactors_closed(after) is not False


def sync_ok(states: dict) -> tuple[bool, str]:
    """Reuse existing in-sync, last_good, and reconnect cell-average gates."""
    in_sync = states.get("binary_sensor.powerpacks_in_sync")
    if in_sync != "on":
        return False, "packs are not in voltage sync (binary_sensor.powerpacks_in_sync is off)"
    # v1.3.9: also require the fresh-voltage hysteresis sensor (stale or
    # unknown telemetry counts as NOT synced). Tighter than before, never looser.
    synced = states.get(SYNC_IN_SYNC)
    if synced != "on":
        return False, f"packs are not in fresh voltage sync ({SYNC_IN_SYNC} is {synced or 'missing'})"

    a = states.get("sensor.powerpack1_average_cell")
    b = states.get("sensor.powerpack2_average_cell")
    try:
        if a in (None, "unknown", "unavailable", "none", "") or b in (
            None, "unknown", "unavailable", "none", "",
        ):
            return False, "average cell voltages are unavailable for the reconnect sync gate"
        if abs(float(a) - float(b)) > SYNC_CELL_MAX_DIFF:
            return False, (
                f"average cell difference {abs(float(a)-float(b)):.3f} V "
                f"exceeds {SYNC_CELL_MAX_DIFF} V (reconnect gate)"
            )
    except (TypeError, ValueError):
        return False, "average cell voltages are not numeric"

    delta = states.get("sensor.powerpacks_volts_last_good_delta")
    max_diff = states.get("input_number.battery_sync_max_voltage_diff")
    if delta not in (None, "unknown", "unavailable", "none", "") and max_diff not in (
        None, "unknown", "unavailable", "none", "",
    ):
        try:
            if float(delta) > float(max_diff):
                return False, (
                    f"last-good voltage delta {float(delta):.1f} V exceeds "
                    f"max {float(max_diff):.1f} V"
                )
        except (TypeError, ValueError):
            pass
    return True, "ok"


def close_allowed(
    *,
    name_lower: str,
    pole: str,
    user_initiated: bool,
    states: dict,
    this_contactor: dict,
    other_contactor: dict | None,
) -> tuple[bool, str]:
    if not pack_allows_switches(name_lower):
        return False, f"{name_lower} has no EVTV contactor switches (read-only)"
    if not user_initiated:
        return False, "closing an EVTV contactor is only allowed from the dashboard / a user action"
    if this_contactor.get("screen") == "menu":
        return False, "controller is on the configuration menu; no key will be sent"
    if this_contactor.get("available") is not True:
        return False, "contactor state is not available from the main screen"
    if this_contactor.get("welded"):
        return False, "welded-contactor warning is set; will not toggle"

    # Closing positive requires negative already commanded + reported ON.
    if pole == "positive":
        if this_contactor.get("negative_commanded") is not True or this_contactor.get("negative_reported") is not True:
            return False, "close the negative contactor first (and wait for precharge) before the positive"

    # Sync only if this close would put both packs on the bus together.
    after = dict(this_contactor)
    after[COMMANDED[pole]] = True
    # Treat the pole as closing for the on-bus test even if the other pole is still open
    # (negative-only close with Sonoff on still applies precharge to the bus).
    if sonoff_on(states, name_lower) is True and other_pack_on_bus(states, name_lower, other_contactor):
        ok, reason = sync_ok(states)
        if not ok:
            return False, "close blocked: would put PP1 and PP2 on the bus together while " + reason
    return True, "ok"


def open_allowed(
    *,
    name_lower: str,
    states: dict,
    this_contactor: dict,
    current_amps: float | None,
    open_current_max: float,
) -> tuple[bool, str]:
    if not pack_allows_switches(name_lower):
        return False, f"{name_lower} has no EVTV contactor switches (read-only)"
    if this_contactor.get("screen") == "menu":
        return False, "controller is on the configuration menu; no key will be sent"
    if this_contactor.get("available") is not True:
        return False, "contactor state is not available from the main screen"
    if this_contactor.get("welded"):
        return False, "welded-contactor warning is set; will not toggle"

    # Sonoff already off: pack is isolated from the bus, opening is allowed
    # even if residual current is reported.
    if sonoff_on(states, name_lower) is False:
        return True, "ok"
    if current_amps is None:
        return False, "pack current is unavailable; open only when |current| is low or the Sonoff is off"
    if abs(current_amps) > float(open_current_max):
        return False, (
            f"pack current {current_amps:.2f} A exceeds the {open_current_max:g} A "
            "open gate (turn the Sonoff connect path off first, or wait for current to drop)"
        )
    return True, "ok"


def already_at_target(contactor: dict, pole: str, target_on: bool) -> bool:
    key = COMMANDED[pole]
    return contactor.get(key) is bool(target_on)


def verify_match(contactor: dict, pole: str, target_on: bool) -> tuple[bool, str]:
    cmd = contactor.get(COMMANDED[pole])
    rep = contactor.get(REPORTED[pole])
    if cmd is not bool(target_on):
        return False, f"{pole} commanded is {cmd!r}, expected {target_on}"
    if rep is not bool(target_on):
        if target_on is False and cmd is False and rep is True:
            return False, f"{pole} commanded OFF but reported ON (possible weld)"
        return False, f"{pole} reported is {rep!r}, expected {target_on}"
    return True, "ok"


def precharge_wait_s(negative_closed_mono: float | None, now_mono: float) -> float:
    """Seconds still needed after a recent negative close. 0 if already elapsed."""
    if negative_closed_mono is None:
        # Negative has been on long enough that we did not record a close; no wait.
        return 0.0
    elapsed = now_mono - float(negative_closed_mono)
    remain = PRECHARGE_SECONDS - elapsed
    return remain if remain > 0 else 0.0
