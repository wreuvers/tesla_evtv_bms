"""Offline tests for the read-only EVTV telnet status parser. No network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "tesla_evtv_bms"))
from telnet_status import parse_status_screen, strip_iac, _RecvOnlySocket

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


def check(name, cond, detail=""):
    if not cond:
        raise SystemExit(f"FAIL {name} {detail}")
    print(f"PASS {name}")


def test_main_screen():
    r = parse_status_screen(MAIN)
    check("main screen", r["screen"] == "main", r)
    check("neg cmd on", r["negative_commanded"] is True)
    check("internal", r["internal_status"] == "allowed=1 status=4 CLOSED", r.get("internal_status"))
    check("not menu despite hint", r["screen"] != "menu")


def test_menu_no_contactors():
    r = parse_status_screen(MENU)
    check("menu screen", r["screen"] == "menu", r)
    check("menu has no contactors", "negative_commanded" not in r, r)


def test_welded():
    text = MAIN.replace("Negative Contactor:ON  Reported ON", "Negative Contactor:OFF  Reported ON")
    r = parse_status_screen(text)
    check("welded from mismatch", r["welded"] is True and r["negative_commanded"] is False)


def test_form_feed_uses_last_screen():
    check("last main", parse_status_screen(MENU + "\x0c" + MAIN)["screen"] == "main")
    check("last menu", parse_status_screen(MAIN + "\x0c" + MENU)["screen"] == "menu")


def test_recv_only_refuses_send():
    class Dummy:
        def recv(self, n):
            return b""
        def settimeout(self, v):
            pass
        def shutdown(self, how):
            pass
        def close(self):
            pass
    s = _RecvOnlySocket(Dummy())
    try:
        s.send(b"1")
        raise SystemExit("FAIL send should have raised")
    except RuntimeError as e:
        check("send refused", "send refused" in str(e))


def test_iac_and_empty():
    payload = bytes([255, 251, 1]) + MAIN.encode("latin-1")
    check("parse after iac", parse_status_screen(payload)["screen"] == "main")
    check("empty", parse_status_screen(b"")["screen"] == "no_screen")


if __name__ == "__main__":
    test_main_screen()
    test_menu_no_contactors()
    test_welded()
    test_form_feed_uses_last_screen()
    test_recv_only_refuses_send()
    test_iac_and_empty()
    print("ALL TESTS PASSED")
