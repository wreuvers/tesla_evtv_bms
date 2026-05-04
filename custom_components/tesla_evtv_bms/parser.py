import logging
_LOGGER = logging.getLogger(__name__)

def parse_udp_packet(payload: bytes, port: int) -> dict:
    _LOGGER.debug(f"[tesla_evtv_bms] Received UDP payload on port {port}: {payload.hex()} (length={len(payload)})")

    if len(payload) < 12:
        _LOGGER.warning(f"[tesla_evtv_bms] Ignored short packet on port {port} (length={len(payload)})")
        return None

    can_id = payload[8] + (payload[9] << 8) + (payload[10] << 16) + (payload[11] << 24)
    _LOGGER.debug(f"[tesla_evtv_bms] Parsed CAN ID: {hex(can_id)}")

    if can_id not in [0x150, 0x151, 0x650, 0x651, 0x652, 0x654, 0x683, 0x68F]:
        _LOGGER.debug(f"[tesla_evtv_bms] Ignored unrecognized CAN ID: {hex(can_id)}")
        return None

    def u16(b0, b1): return b0 + (b1 << 8)
    def s16(b0, b1):
        v = b0 + (b1 << 8)
        return v - 0x10000 if v & 0x8000 else v
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
        # Keeping 0x151 parsing in case needed, but optional now
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
        raw_current = u16(payload[0], payload[1])
        volts = u16(payload[2], payload[3]) / 10.0

        if raw_current > 32768:
            # Charging
            charging_current = 65535 - raw_current
            current = charging_current
            power = round(volts * charging_current)
        else:
            # Discharging
            discharging_current = raw_current
            current = -discharging_current
            power = -round(volts * discharging_current)

        result.update({
            "current": round(current, 2),
            "power": round(power),
            "volts": round(volts, 1),
            "raw_current": raw_current
        })

        # Bytes 4/5: Pack Ah used (signed int16 LE, x10) per BMS protocol spec.
        # "Almost always negative" - represents Ah extracted from the pack.
        result["pack_ah_used"] = round(s16(payload[4], payload[5]) / 10.0, 1)

        # Bytes 6/7: max/min pack terminal temperature in whole degrees C (u8)
        # per BMS protocol spec (Message 0x150).
        result["highest_temp"] = payload[6]
        result["lowest_temp"] = payload[7]

    elif can_id == 0x652:
        # Bytes 4/5: High Voltage Cutoff per cell (u16 LE, x10 -> volts)
        # Bytes 6/7: Low Voltage Cutoff per cell (u16 LE, x10 -> volts)
        result["high_voltage_cutoff"] = round(u16(payload[4], payload[5]) / 10.0, 2)
        result["low_voltage_cutoff"] = round(u16(payload[6], payload[7]) / 10.0, 2)

    elif can_id == 0x654:
        # Byte 0: I/O status bitfield
        #   bit 0: negative contactor commanded (0=open, 1=closed)
        #   bit 1: positive contactor commanded (0=open, 1=closed)
        #   bit 2: charge enable
        #   bit 3: heat enable
        #   bit 4: negative contactor confirmation (1=open, 0=closed) -- INVERTED
        #   bit 5: positive contactor confirmation (1=open, 0=closed) -- INVERTED
        #   bit 6: 1=USB-only power, 0=12V powered
        status = payload[0]
        result["contactor_negative"] = "Open" if (status & 0x10) else "Closed"
        result["contactor_positive"] = "Open" if (status & 0x20) else "Closed"
        result["charge_enable"] = "On" if (status & 0x04) else "Off"
        result["heat_enable"] = "On" if (status & 0x08) else "Off"
        result["power_source"] = "USB" if (status & 0x40) else "12V"

        # Byte 1: Fault status
        #   bits 0-5: fault code
        #   bit 6: temperature fault flag
        #   bit 7: voltage fault flag
        fault = payload[1]
        fault_code = fault & 0x3F
        fault_reasons = {
            0: "No Fault",
            1: "Cell Undervoltage",
            2: "Cell Overvoltage",
            3: "Module Undertemperature",
            4: "Module Overtemperature",
            5: "Voltage Imbalance",
        }
        result["fault_code"] = fault_code
        result["fault_status"] = fault_reasons.get(fault_code, f"Unknown ({fault_code})")

    elif can_id == 0x68F:
        # Multiplexed per-cell voltage frame.
        #   Byte 0: sequence number (frame index)
        #   Byte 1: total 6-cell modules in pack
        #   Bytes 2-7: six cell voltages, each = (raw + 200) / 100 volts
        # Per-cell voltages aren't exposed individually (would create up to 192
        # entities). lowest/highest/average from 0x651 cover the practical use case.
        result["total_modules"] = payload[1]
        result["total_cells"] = payload[1] * 6

    return result
