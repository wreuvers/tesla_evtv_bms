"""Offline tests for EVTV contactor guards and toggle refusal paths. No network."""
import socket
import sys
from pathlib import Path
from unittest.mock import patch

import types
root = Path(__file__).resolve().parents[1]
comp = None
for c in (root / "custom_components" / "tesla_evtv_bms", root / "tesla_evtv_bms"):
    if c.exists():
        comp = c
        break
if comp is None:
    raise SystemExit("tesla_evtv_bms package not found")
# Load submodules without executing the HA-dependent package __init__.
pkg = types.ModuleType("tesla_evtv_bms")
pkg.__path__ = [str(comp)]
pkg.__package__ = "tesla_evtv_bms"
sys.modules.setdefault("tesla_evtv_bms", pkg)

from tesla_evtv_bms.contactor_control import (
    already_at_target,
    close_allowed,
    open_allowed,
    pack_allows_switches,
    precharge_wait_s,
    sync_ok,
    verify_match,
)
from tesla_evtv_bms import telnet_status
from tesla_evtv_bms.telnet_status import apply_contactor_toggle, parse_status_screen, _ToggleOnceSocket

MAIN = """
PACK STATUS:No Faults
  Allowed to close internal contactors: 1  Status: 4 CLOSED
        Negative Contactor:ON  Reported ON
        Positive Contactor:ON  Reported ON
Enter ? for Configuration Menu......
"""
MENU = """
*******************  CONFIGURATION MENU Version 3.17   Runtime: 00 Days 00:00:00  ***************
   c = Clear all Module Faults
Enter ? to return to Main Display Screen......
"""

CLOSED = {
    "screen": "main",
    "available": True,
    "negative_commanded": True,
    "negative_reported": True,
    "positive_commanded": True,
    "positive_reported": True,
    "welded": False,
}
SYNC_OK = {
    "binary_sensor.powerpacks_in_sync": "on",
    "binary_sensor.powerpacks_volts_synced": "on",
    "sensor.powerpack1_average_cell": "3.300",
    "sensor.powerpack2_average_cell": "3.300",
    "sensor.powerpacks_volts_last_good_delta": "0.1",
    "input_number.battery_sync_max_voltage_diff": "0.5",
    "switch.powerpack1_connect_main_inverter": "on",
    "switch.powerpack2_connect_main_inverter": "on",
}


def check(name, cond, detail=""):
    if not cond:
        raise SystemExit(f"FAIL {name} {detail}")
    print(f"PASS {name}")


def test_pack_gate():
    check("pp1 ok", pack_allows_switches("powerpack1") is True)
    check("pp2 ok", pack_allows_switches("Powerpack2") is True)
    check("pp0 no", pack_allows_switches("powerpack0") is False)


def test_close_requires_user():
    ok, reason = close_allowed(
        name_lower="powerpack1", pole="negative", user_initiated=False,
        states=SYNC_OK, this_contactor=CLOSED, other_contactor=CLOSED,
    )
    check("auto close blocked", ok is False and "user" in reason.lower(), reason)


def test_close_blocked_when_out_of_sync_on_bus():
    states = dict(SYNC_OK)
    states["binary_sensor.powerpacks_in_sync"] = "off"
    ok, reason = close_allowed(
        name_lower="powerpack2", pole="negative", user_initiated=True,
        states=states, this_contactor=dict(CLOSED, negative_commanded=False, positive_commanded=False),
        other_contactor=CLOSED,
    )
    check("sync close blocked", ok is False and "sync" in reason.lower(), reason)


def test_close_allowed_if_own_sonoff_off():
    states = dict(SYNC_OK)
    states["binary_sensor.powerpacks_in_sync"] = "off"
    states["switch.powerpack2_connect_main_inverter"] = "off"
    ok, reason = close_allowed(
        name_lower="powerpack2", pole="negative", user_initiated=True,
        states=states, this_contactor=dict(CLOSED, available=True, negative_commanded=False, positive_commanded=False),
        other_contactor=CLOSED,
    )
    check("sonoff off close ok", ok is True, reason)


def test_positive_close_needs_negative():
    this = dict(CLOSED, negative_commanded=False, negative_reported=False)
    ok, reason = close_allowed(
        name_lower="powerpack1", pole="positive", user_initiated=True,
        states=SYNC_OK, this_contactor=this, other_contactor=CLOSED,
    )
    check("pos needs neg", ok is False and "negative" in reason.lower(), reason)


def test_close_menu_and_welded():
    ok, reason = close_allowed(
        name_lower="powerpack1", pole="negative", user_initiated=True,
        states=SYNC_OK, this_contactor=dict(CLOSED, screen="menu"), other_contactor=None,
    )
    check("menu blocks close", ok is False and "menu" in reason.lower(), reason)
    ok, reason = close_allowed(
        name_lower="powerpack1", pole="negative", user_initiated=True,
        states=SYNC_OK, this_contactor=dict(CLOSED, welded=True), other_contactor=None,
    )
    check("welded blocks close", ok is False and "weld" in reason.lower(), reason)


def test_open_current_gate_and_sonoff_bypass():
    this = dict(CLOSED)
    states_on = dict(SYNC_OK, **{"switch.powerpack1_connect_main_inverter": "on"})
    ok, reason = open_allowed(
        name_lower="powerpack1", states=states_on, this_contactor=this,
        current_amps=12.0, open_current_max=2.0,
    )
    check("high current blocks open", ok is False and "current" in reason.lower(), reason)
    states_off = dict(SYNC_OK, **{"switch.powerpack1_connect_main_inverter": "off"})
    ok, reason = open_allowed(
        name_lower="powerpack1", states=states_off, this_contactor=this,
        current_amps=12.0, open_current_max=2.0,
    )
    check("sonoff off bypasses current", ok is True, reason)
    ok, reason = open_allowed(
        name_lower="powerpack1", states=states_on, this_contactor=this,
        current_amps=0.4, open_current_max=2.0,
    )
    check("low current allows open", ok is True, reason)


def test_already_and_verify_and_precharge():
    check("already on", already_at_target(CLOSED, "negative", True) is True)
    check("not already off", already_at_target(CLOSED, "negative", False) is False)
    ok, _ = verify_match(CLOSED, "positive", True)
    check("verify closed", ok is True)
    welded = dict(CLOSED, negative_commanded=False, positive_commanded=False, positive_reported=True)
    ok, reason = verify_match(welded, "positive", False)
    check("verify weld", ok is False and "weld" in reason.lower(), reason)
    check("precharge remaining", abs(precharge_wait_s(100.0, 104.0) - 6.0) < 1e-6)
    check("precharge done", precharge_wait_s(100.0, 120.0) == 0.0)
    check("precharge unknown", precharge_wait_s(None, 1.0) == 0.0)
    ok, _ = sync_ok(SYNC_OK)
    check("sync ok", ok is True)


class _FakeSock:
    def __init__(self, first, second=b""):
        self._first = first
        self._second = second
        self._gave_first = False
        self._gave_second = False
        self.sent = []

    def recv(self, n):
        # Only yield the post-toggle screen after a key has been sent,
        # so the first listen window cannot swallow both payloads.
        if not self.sent:
            if self._gave_first:
                raise socket.timeout()
            self._gave_first = True
            return self._first
        if not self._gave_second:
            self._gave_second = True
            return self._second
        raise socket.timeout()

    def send(self, data):
        self.sent.append(data)
        return len(data)

    def settimeout(self, value):
        pass

    def shutdown(self, how):
        pass

    def close(self):
        pass


def test_toggle_refuses_bad_key():
    try:
        apply_contactor_toggle("127.0.0.1", 23, b"?")
        raise SystemExit("FAIL bad key should raise")
    except RuntimeError as e:
        check("refuses question mark", "refused" in str(e).lower(), e)
    try:
        apply_contactor_toggle("127.0.0.1", 23, b"C")
        raise SystemExit("FAIL C should raise")
    except RuntimeError as e:
        check("refuses C", "refused" in str(e).lower(), e)


def test_toggle_refuses_menu_and_empty():
    fake = _FakeSock(MENU.encode("latin-1"))
    with patch("tesla_evtv_bms.telnet_status.socket.create_connection", return_value=fake):
        r = apply_contactor_toggle("127.0.0.1", 23, b"1", listen_s=0.5, connect_timeout_s=1, verify_s=0.2)
    check("menu not sent", r["sent"] is False and r["error"] == "menu" and fake.sent == [], r)

    fake2 = _FakeSock(b"")
    with patch("tesla_evtv_bms.telnet_status.socket.create_connection", return_value=fake2):
        r = apply_contactor_toggle("127.0.0.1", 23, b"2", listen_s=0.5, connect_timeout_s=1, verify_s=0.2)
    check("empty not sent", r["sent"] is False and r["error"] == "no_screen" and fake2.sent == [], r)


def test_toggle_sends_only_on_main():
    after = MAIN.replace("Negative Contactor:ON  Reported ON", "Negative Contactor:OFF  Reported OFF")
    fake = _FakeSock(MAIN.encode("latin-1"), after.encode("latin-1"))
    with patch("tesla_evtv_bms.telnet_status.socket.create_connection", return_value=fake):
        r = apply_contactor_toggle("127.0.0.1", 23, b"1", listen_s=0.5, connect_timeout_s=1, verify_s=0.5)
    check("main sent once", r["sent"] is True and fake.sent == [b"1"], r)
    check("after parsed off", r["after"].get("negative_commanded") is False, r["after"])
    check("no extra keys", all(k == b"1" for k in fake.sent))


def test_toggle_socket_generic_send_refused():
    s = _ToggleOnceSocket(_FakeSock(b""))
    try:
        s.send(b"1")
        raise SystemExit("FAIL generic send")
    except RuntimeError as e:
        check("generic send refused", "generic send refused" in str(e))
    s.send_toggle_key(b"2")
    try:
        s.send_toggle_key(b"1")
        raise SystemExit("FAIL second key")
    except RuntimeError as e:
        check("second key refused", "only one toggle" in str(e))
    try:
        s.send_toggle_key(b"?")
        raise SystemExit("FAIL ? via send_toggle_key")
    except RuntimeError as e:
        check("toggle key gate", "refused" in str(e).lower(), e)



def test_close_blocked_when_volts_stale():
    states = dict(SYNC_OK)
    states["binary_sensor.powerpacks_volts_synced"] = "off"
    ok, reason = close_allowed(
        name_lower="powerpack2", pole="negative", user_initiated=True,
        states=states, this_contactor=dict(CLOSED, negative_commanded=False, positive_commanded=False),
        other_contactor=CLOSED,
    )
    check("stale sync close blocked", ok is False and "fresh voltage sync" in reason, reason)

if __name__ == "__main__":
    test_close_blocked_when_volts_stale()
    test_pack_gate()
    test_close_requires_user()
    test_close_blocked_when_out_of_sync_on_bus()
    test_close_allowed_if_own_sonoff_off()
    test_positive_close_needs_negative()
    test_close_menu_and_welded()
    test_open_current_gate_and_sonoff_bypass()
    test_already_and_verify_and_precharge()
    test_toggle_refuses_bad_key()
    test_toggle_refuses_menu_and_empty()
    test_toggle_sends_only_on_main()
    test_toggle_socket_generic_send_refused()
    print("ALL TESTS PASSED")
