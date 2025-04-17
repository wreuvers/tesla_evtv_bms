import logging
_LOGGER = logging.getLogger(__name__)

def parse_udp_packet(payload: bytes, port: int) -> dict:
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
        current = s32(payload[0:4]) / 100.0 * -1
        power = s32(payload[4:8]) / 100.0 * -1
        volts = power / current if current != 0 else 0
        result.update({
            "current": round(current, 2),
            "power": round(power),
            "volts": round(volts, 1)
        })

    elif can_id == 0x683:
        result["freq_shift_volts"] = u16(payload[2], payload[3]) / 100
        result["tcch_amps"] = u16(payload[4], payload[5]) / 10

    elif can_id == 0x150:
        current = u16(payload[0], payload[1])
        volts = u16(payload[2], payload[3]) / 10
        result["volts"] = volts
        result["raw_current"] = current

    return result
