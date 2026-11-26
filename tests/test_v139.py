"""Offline tests for v1.3.9 screen extras, derived flags and stale sync gate. No network."""
import sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "tesla_evtv_bms"))
from telnet_status import parse_status_screen, SCREEN_EXTRA_KEYS

def check(name, cond, detail=""):
    if not cond:
        raise SystemExit(f"FAIL {name} {detail}")
    print(f"PASS {name}")

RAW = (Path(__file__).parent / "pp2_capture_20261126.bin").read_bytes()

def test_pp2_capture():
    r = parse_status_screen(RAW)
    check("pp2 main", r["screen"] == "main", r)
    check("pp2 no faults", r["pack_status"] == "No Faults" and r["faulted"] is False, r)
    check("pp2 runtime", r["runtime_s"] == 2*3600+5*60+33, r.get("runtime_s"))
    check("pp2 alarms off", r["temperature_alarm"] is False and r["voltage_alarm"] is False)
    check("pp2 alerts", "Limp Mode" in r["bms_alerts"] and "HV Chain Model Fault" in r["bms_alerts"] and len(r["bms_alerts"]) == 7, r["bms_alerts"])
    check("pp2 contactors on", r["negative_commanded"] and r["positive_reported"])

def test_pp1_faulted_like_0920():
    txt = RAW.decode("latin-1").split("\x0c")[-1]
    txt = txt.replace("PACK STATUS:No Faults", "PACK STATUS:FAULTED")
    txt = txt.replace("Negative Contactor:ON  Reported ON", "Negative Contactor:OFF  Reported OFF")
    txt = txt.replace("Positive Contactor:ON  Reported ON", "Positive Contactor:OFF  Reported OFF")
    txt = txt.replace("Runtime: 00 Days 02:05:33", "Runtime: 00 Days 01:43:37")
    r = parse_status_screen(txt)
    check("pp1 faulted", r["faulted"] is True and r["pack_status"] == "FAULTED", r.get("pack_status"))
    check("pp1 runtime", r["runtime_s"] == 6217, r.get("runtime_s"))
    check("pp1 open", r["negative_commanded"] is False and r["positive_reported"] is False)
    check("tesla internal still CLOSED text", r["internal_status"] == "allowed=1 status=4 CLOSED")

def test_partial_last_segment_uses_complete_screen():
    segs = RAW.decode("latin-1").split("\x0c")
    data = "\x0c".join(segs) + "\x0c" + segs[-1][:400]
    r = parse_status_screen(data)
    check("partial tail ignored", r["screen"] == "main" and "negative_commanded" in r and "pack_status" in r, r.keys())

def test_derived_contactors_open():
    # binary_sensor.py imports HA; test the pure helper by exec of its source tail.
    src = (Path(__file__).resolve().parents[1] / "custom_components" / "tesla_evtv_bms" / "binary_sensor.py").read_text()
    fn = src[src.index("def derived_value"):src.index("class EvtvDerivedBinarySensor")]
    ns = {}; exec(fn, ns); dv = ns["derived_value"]
    check("commanded-off but reported closed is NOT open", dv({"negative_commanded": True, "negative_reported": True, "positive_commanded": False, "positive_reported": True}, "contactors_open") is False)
    check("open when a pole REPORTED off", dv({"negative_commanded": True, "negative_reported": False, "positive_commanded": True, "positive_reported": True}, "contactors_open") is True)
    check("unknown when no reported", dv({"negative_commanded": False}, "contactors_open") is None)
    check("closed when all on", dv({"negative_commanded": True, "negative_reported": True, "positive_commanded": True, "positive_reported": True}, "contactors_open") is False)
    check("unknown when none", dv({}, "contactors_open") is None)
    check("faulted passthrough", dv({"faulted": True}, "faulted") is True)

def test_extra_keys_listed():
    check("extra keys", set(SCREEN_EXTRA_KEYS) == {"pack_status","faulted","runtime_s","temperature_alarm","voltage_alarm","bms_alerts"})

def test_no_new_send_paths():
    root = Path(__file__).resolve().parents[1] / "custom_components" / "tesla_evtv_bms"
    allowed = {"telnet_status.py"}
    for f in root.glob("*.py"):
        t = f.read_text()
        if ".send(" in t or "sendall(" in t:
            check(f"send only in telnet_status ({f.name})", f.name in allowed)
    ts = (root / "telnet_status.py").read_text()
    check("toggle keys only 1/2", 'ALLOWED_TOGGLE_KEYS = {b"1", b"2"}' in ts)
    for bad in ['b"C"', 'b"4"', 'b"5"', 'b"c"', 'b"z"', 'b"Z"', 'b"u"', 'b"R"', 'b"S"', 'b"d"', 'b"D"', 'b"X"', "NAME=", "UPDATE=1"]:
        for f in root.glob("*.py"):
            check(f"no {bad} in {f.name}", bad not in f.read_text())

if __name__ == "__main__":
    test_pp2_capture(); test_pp1_faulted_like_0920(); test_partial_last_segment_uses_complete_screen()
    test_derived_contactors_open(); test_extra_keys_listed(); test_no_new_send_paths()
    print("ALL TESTS PASSED")
