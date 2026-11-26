"""EVTV telnet status-screen parser, read-only listener, and guarded toggle.

CRITICAL SAFETY RULE: the status poll (read_telnet_screen) must never send
any bytes to the controller. No keystrokes, no CR/LF, no '?', no IAC
replies, no telnet negotiations. The proven method (Oct 2026 passive
captures) is: connect, recv for a few seconds while the main screen
redraws itself, then close the TCP socket.

If the controller happens to be on the configuration menu, we report
screen='menu' and skip contactor fields. We do not send '?' to leave it.
If the socket connects but no status screen arrives, we report
screen='no_screen' (the controller may only refresh after input; we still
send nothing).

The only write path is apply_contactor_toggle(), which may send a single
ASCII '1' or '2' after confirming the first screen is the main status
display. It must never send '?' or any other key.
"""
from __future__ import annotations

import logging
import re
import socket
import time
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# How long to listen after connect for a self-refreshing main screen.
DEFAULT_LISTEN_S = 2.5
DEFAULT_CONNECT_TIMEOUT_S = 5.0

# Telnet IAC
_IAC = 255
_WILL, _WONT, _DO, _DONT = 251, 252, 253, 254
_SB = 250
_SE = 240

_NEG_RE = re.compile(
    r"Negative\s+Contactor\s*:\s*(ON|OFF)\s+Reported\s+(ON|OFF)",
    re.IGNORECASE,
)
_POS_RE = re.compile(
    r"Positive\s+Contactor\s*:\s*(ON|OFF)\s+Reported\s+(ON|OFF)",
    re.IGNORECASE,
)
_INTERNAL_RE = re.compile(
    r"Allowed\s+to\s+close\s+internal\s+contactors\s*:\s*(\S+)\s+Status\s*:\s*(\S+(?:\s+\S+)?)",
    re.IGNORECASE,
)
_INTERNAL_ALT_RE = re.compile(
    r"Battery\s+Internal\s+Contactors\s*:\s*(\S+(?:\s+\S+)?)",
    re.IGNORECASE,
)
_WELDED_RE = re.compile(r"POSSIBLE\s+WELDED\s+CONTACTOR", re.IGNORECASE)
# v1.3.9: extra read-only status fields from the same main screen.
_PACK_STATUS_RE = re.compile(r"PACK\s+STATUS\s*:\s*([^\n\r]*)", re.IGNORECASE)
_RUNTIME_RE = re.compile(
    r"Runtime\s*:\s*(\d+)\s*Days?\s+(\d+):(\d{2}):(\d{2})", re.IGNORECASE
)
_TEMP_ALARM_RE = re.compile(r"Temperature\s+Alarm\s*:\s*(ON|OFF)", re.IGNORECASE)
_VOLT_ALARM_RE = re.compile(r"Voltage\s+Alarm\s*:\s*(ON|OFF)", re.IGNORECASE)
_ALERT_BLOCK_RE = re.compile(
    r"BMS\s+ALERT!.*?\n(.*?)\n[ \t]*ALERT!", re.IGNORECASE | re.DOTALL
)
# Banner of the config screen only — do not use a bare "Configuration Menu"
# match; the main screen says "Enter ? for Configuration Menu".
_MENU_BANNER_RE = re.compile(
    r"CONFIGURATION\s+MENU\s+Version",
    re.IGNORECASE,
)
_MAIN_HINTS = (
    "PACK STATUS",
    "Enter ? for Configuration Menu",
    "Negative Contactor",
    "Positive Contactor",
)


class _RecvOnlySocket:
    """Wrapper that refuses any send so a bug cannot emit a keystroke."""

    def __init__(self, sock: socket.socket):
        self._s = sock

    def recv(self, nbytes: int) -> bytes:
        return self._s.recv(nbytes)

    def settimeout(self, value) -> None:
        self._s.settimeout(value)

    def send(self, *args, **kwargs):  # pragma: no cover - safety rail
        raise RuntimeError("telnet read-only: send refused")

    def sendall(self, *args, **kwargs):  # pragma: no cover - safety rail
        raise RuntimeError("telnet read-only: send refused")

    def sendto(self, *args, **kwargs):  # pragma: no cover - safety rail
        raise RuntimeError("telnet read-only: send refused")

    def close(self) -> None:
        try:
            # SHUT_RD only would still be fine; SHUT_RDWR sends a TCP FIN,
            # which is not a telnet keystroke. Never call send() here.
            self._s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._s.close()


def strip_iac(data: bytes) -> bytes:
    """Remove telnet IAC option sequences from a received buffer. No replies."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b != _IAC:
            out.append(b)
            i += 1
            continue
        if i + 1 >= n:
            break
        cmd = data[i + 1]
        if cmd == _IAC:  # escaped 0xFF
            out.append(255)
            i += 2
        elif cmd in (_WILL, _WONT, _DO, _DONT):
            i += 3  # IAC cmd option
        elif cmd == _SB:
            # subnegotiation until IAC SE
            j = i + 2
            while j + 1 < n and not (data[j] == _IAC and data[j + 1] == _SE):
                j += 1
            i = j + 2
        else:
            i += 2  # IAC + simple command
    return bytes(out)


def _last_screen(text: str) -> str:
    """Return the most recent form-feed delimited screen, if any.

    v1.3.9: prefer the last *complete* main screen (one that reaches the
    "Enter ? for Configuration Menu" line) so a capture that ends mid-redraw
    still parses; fall back to the last non-empty segment.
    """
    if "\x0c" in text:
        parts = [p for p in text.split("\x0c") if p.strip()]
        if parts:
            for part in reversed(parts):
                if _MENU_BANNER_RE.search(part):
                    return part  # controller is on the config menu now
                if "Enter ? for Configuration Menu" in part and "PACK STATUS" in part.upper():
                    return part
            return parts[-1]
    return text


def _is_menu(text: str) -> bool:
    """True only for the configuration-menu banner, not the main-screen hint."""
    return bool(_MENU_BANNER_RE.search(text))


def parse_status_screen(payload) -> dict:
    """Parse a telnet payload (bytes or str) into a status dict.

    Always includes 'screen': 'main' | 'menu' | 'no_screen'.
    Contactor fields are present only when screen == 'main' and the
    corresponding lines were found.
    """
    if payload is None:
        return {"screen": "no_screen"}
    if isinstance(payload, bytes):
        cleaned = strip_iac(payload)
        text = cleaned.decode("latin-1", "replace")
    else:
        text = str(payload)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    screen_text = _last_screen(text)

    if not screen_text.strip():
        return {"screen": "no_screen"}

    if _is_menu(screen_text):
        return {"screen": "menu"}

    if not any(h in screen_text for h in _MAIN_HINTS):
        return {"screen": "no_screen"}

    result = {"screen": "main"}
    m = _NEG_RE.search(screen_text)
    if m:
        result["negative_commanded"] = m.group(1).upper() == "ON"
        result["negative_reported"] = m.group(2).upper() == "ON"
    m = _POS_RE.search(screen_text)
    if m:
        result["positive_commanded"] = m.group(1).upper() == "ON"
        result["positive_reported"] = m.group(2).upper() == "ON"

    welded_text = bool(_WELDED_RE.search(screen_text))
    welded = False
    if result.get("negative_commanded") is False and result.get("negative_reported") is True:
        welded = True
    if result.get("positive_commanded") is False and result.get("positive_reported") is True:
        welded = True
    if "negative_commanded" in result or "positive_commanded" in result:
        result["welded"] = welded or welded_text
    elif welded_text:
        result["welded"] = True

    im = _INTERNAL_RE.search(screen_text)
    if im:
        allowed = im.group(1).strip()
        status = im.group(2).strip()
        result["internal_status"] = f"allowed={allowed} status={status}"
        result["internal_allowed"] = allowed
        result["internal_state"] = status
    else:
        am = _INTERNAL_ALT_RE.search(screen_text)
        if am:
            status = am.group(1).strip()
            result["internal_status"] = status
            result["internal_state"] = status

    result.update(parse_screen_extras(screen_text))
    return result


def parse_screen_extras(screen_text: str) -> dict:
    """PACK STATUS, Runtime, alarms and the BMS ALERT list (v1.3.9). Read-only."""
    out: dict = {}
    m = _PACK_STATUS_RE.search(screen_text)
    if m:
        status = " ".join(m.group(1).split())
        out["pack_status"] = status
        up = status.upper()
        out["faulted"] = ("FAULT" in up) and not up.startswith("NO FAULT")
    m = _RUNTIME_RE.search(screen_text)
    if m:
        d, h, mi, se = (int(x) for x in m.groups())
        out["runtime_s"] = d * 86400 + h * 3600 + mi * 60 + se
    m = _TEMP_ALARM_RE.search(screen_text)
    if m:
        out["temperature_alarm"] = m.group(1).upper() == "ON"
    m = _VOLT_ALARM_RE.search(screen_text)
    if m:
        out["voltage_alarm"] = m.group(1).upper() == "ON"
    if "PACK STATUS" in screen_text.upper():
        alerts: list[str] = []
        blocks = _ALERT_BLOCK_RE.findall(screen_text)
        if blocks:
            for line in blocks[-1].splitlines():
                for item in re.split(r"\s{2,}", line.strip()):
                    item = item.strip()
                    if item and "ALERT!" not in item.upper():
                        alerts.append(item)
        out["bms_alerts"] = alerts
    return out


def read_telnet_screen(
    host: str,
    port: int = 23,
    listen_s: float = DEFAULT_LISTEN_S,
    connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
) -> bytes:
    """Connect, receive only, disconnect. Raises OSError on connect/read failure.

    This function must never write to the socket.
    """
    raw = socket.create_connection((host, port), timeout=connect_timeout_s)
    sock = _RecvOnlySocket(raw)
    buf = b""
    try:
        sock.settimeout(0.4)
        t0 = time.monotonic()
        while (time.monotonic() - t0) < listen_s:
            try:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
            except socket.timeout:
                continue
    finally:
        sock.close()
    return buf


# v1.3.9: extra main-screen fields copied into the per-pack contactor state.
SCREEN_EXTRA_KEYS = (
    "pack_status", "faulted", "runtime_s", "temperature_alarm", "voltage_alarm", "bms_alerts",
)

ALLOWED_TOGGLE_KEYS = {b"1", b"2"}


class _ToggleOnceSocket:
    """Receive freely; allow a single send of b'1' or b'2' and nothing else."""

    def __init__(self, sock: socket.socket):
        self._s = sock
        self._sent = None

    def recv(self, nbytes: int) -> bytes:
        return self._s.recv(nbytes)

    def settimeout(self, value) -> None:
        self._s.settimeout(value)

    def send_toggle_key(self, key: bytes) -> None:
        if key not in ALLOWED_TOGGLE_KEYS:
            raise RuntimeError(f"telnet: refused to send non-toggle key {key!r}")
        if self._sent is not None:
            raise RuntimeError("telnet: only one toggle key allowed per session")
        # Single ASCII key, no CR/LF, no IAC, no '?'.
        self._s.send(key)
        self._sent = key

    def send(self, *args, **kwargs):  # pragma: no cover - safety rail
        raise RuntimeError("telnet: generic send refused; use send_toggle_key")

    def sendall(self, *args, **kwargs):  # pragma: no cover - safety rail
        raise RuntimeError("telnet: generic send refused; use send_toggle_key")

    def close(self) -> None:
        try:
            self._s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._s.close()


def _recv_for(sock, listen_s: float) -> bytes:
    buf = b""
    sock.settimeout(0.4)
    t0 = time.monotonic()
    while (time.monotonic() - t0) < listen_s:
        try:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        except socket.timeout:
            continue
    return buf


def apply_contactor_toggle(
    host: str,
    port: int,
    key: bytes,
    *,
    listen_s: float = DEFAULT_LISTEN_S,
    connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
    verify_s: float | None = None,
) -> dict:
    """Read the status screen, optionally send a single '1' or '2', then re-read.

    NEVER sends '?' or any key other than b'1' / b'2'. If the first screen is
    not the main status display (config menu, empty, or unrecognised), no key
    is sent and the result has sent=False and error set.

    The caller must already have decided that a toggle is required and safe;
    this function will still refuse to send if the screen is not 'main'.
    """
    if key not in ALLOWED_TOGGLE_KEYS:
        raise RuntimeError(f"telnet: refused to send non-toggle key {key!r}")
    if verify_s is None:
        verify_s = listen_s
    raw = socket.create_connection((host, port), timeout=connect_timeout_s)
    sock = _ToggleOnceSocket(raw)
    before_buf = b""
    after_buf = b""
    sent = False
    error = None
    try:
        before_buf = _recv_for(sock, listen_s)
        before = parse_status_screen(before_buf)
        if before.get("screen") != "main":
            error = "menu" if before.get("screen") == "menu" else "no_screen"
            return {
                "sent": False,
                "key": None,
                "error": error,
                "before": before,
                "after": before,
            }
        sock.send_toggle_key(key)
        sent = True
        after_buf = _recv_for(sock, verify_s)
        after = parse_status_screen(after_buf) if after_buf.strip() else before
        # If the post-key window was empty, keep before as after but flag it.
        if not after_buf.strip():
            error = "no_verify_screen"
        elif after.get("screen") != "main":
            error = "verify_" + str(after.get("screen") or "unknown")
        return {
            "sent": True,
            "key": key.decode("ascii"),
            "error": error,
            "before": before,
            "after": after,
        }
    finally:
        sock.close()
