# v1.3.5 (2026-09-30): power is ALWAYS computed here as volts x current.
#   volts   : 0x150 only (never derived from 0x151; P/I volts caused HA swing).
#   current : fine 0x151 current when fresh (<5 s on that UDP port), else the
#             coarse 0x150 fallback current (pre-1.3.2 65535-raw formula).
#   The controller's 0x151 power field is ignored entirely (PP0 reports ~2x).
#   Sign: + = charging (sensor.py convention). Per-pack invert_current flips
#   the current sign for controllers that report it the other way (PP0).
# v1.3.4 (2026-09-17): 0x151 preferred I/P with 0x150 fallback when stale.
import logging
import time

_LOGGER = logging.getLogger(__name__)

_151_STALE_SEC = 5.0

# Per-UDP-port state (one port per pack / config entry).
_LAST_151_MONO = {}      # port -> monotonic time of last 0x151
_LAST_151_CURRENT = {}   # port -> last fine current (A, HA sign, invert applied)
_LAST_VOLTS = {}         # port -> last 0x150 pack volts


def _fine_current_fresh(port):
    last = _LAST_151_MONO.get(port)
    return last is not None and (time.monotonic() - last) <= _151_STALE_SEC


def _power(volts, current):
    return round(volts * current)


def parse_udp_packet(payload: bytes, port: int, invert_current: bool = False) -> dict:
    _LOGGER.debug(f"[tesla_evtv_bms] Received UDP payload on port {port}: {payload.hex()} (length={len(payload)})")

    if len(payload) < 12:
        _LOGGER.warning(f"[tesla_evtv_bms] Ignored short packet on port {port} (length={len(payload)})")
        return None

    can_id = payload[8] + (payload[9] << 8) + (payload[10] << 16) + (payload[11] << 24)
    _LOGGER.debug(f"[tesla_evtv_bms] Parsed CAN ID: {hex(can_id)}")

    if can_id not in [0x150, 0x151, 0x650, 0x651, 0x683]:
        _LOGGER.debug(f"[tesla_evtv_bms] Ignored unrecognized CAN ID: {hex(can_id)}")
        return None

    def u16(b0, b1): return b0 + (b1 << 8)
    def s32(b): return int.from_bytes(b, byteorder="little", signed=True)

    sign = -1 if invert_current else 1
    result = {}

    if can_id == 0x650:  # State of Charge
        result["state_of_charge"] = payload[0] / 2

    elif can_id == 0x651:
        result["lowest_cell"] = u16(payload[0], payload[1]) / 1000
        result["highest_cell"] = u16(payload[2], payload[3]) / 1000
        result["average_cell"] = u16(payload[4], payload[5]) / 1000
        result["max_cells"] = payload[6]
        result["active_cells"] = payload[7]

    elif can_id == 0x151:
        # Fine-grained current (centi-amps). Bytes 4-7 (controller power) are
        # deliberately NOT used. Volts are NOT taken from this frame.
        current = s32(payload[0:4]) / 100.0 * -1 * sign
        _LAST_151_MONO[port] = time.monotonic()
        _LAST_151_CURRENT[port] = current
        result["current"] = round(current, 2)
        volts = _LAST_VOLTS.get(port)
        if volts is not None:
            result["power"] = _power(volts, current)

    elif can_id == 0x683:
        result["freq_shift_volts"] = u16(payload[2], payload[3]) / 100
        result["tcch_amps"] = u16(payload[4], payload[5]) / 10

    elif can_id == 0x150:
        raw_current = u16(payload[0], payload[1])
        volts = round(u16(payload[2], payload[3]) / 10.0, 1)
        _LAST_VOLTS[port] = volts
        result.update({
            "volts": volts,
            "raw_current": raw_current,
        })
        if _fine_current_fresh(port):
            # Fine 0x151 current is current; refresh power with new volts.
            result["power"] = _power(volts, _LAST_151_CURRENT[port])
        else:
            # Fallback for packs with no recent 0x151.
            if raw_current > 32768:
                current = 65535 - raw_current      # charging (uint16 wrap)
            else:
                current = -raw_current             # discharging
            current *= sign
            result.update({
                "current": round(current, 2),
                "power": _power(volts, current),
            })

    return result
