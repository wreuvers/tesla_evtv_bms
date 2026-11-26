DOMAIN = "tesla_evtv_bms"
PLATFORMS = ["sensor", "switch", "binary_sensor"]

CONF_NAME = "name"
CONF_PORT = "port"
CONF_HOST = "host"
CONF_TELNET_PORT = "telnet_port"

SIGNAL_UPDATE_ENTITY = f"{DOMAIN}_{{}}_update"
# Fired (per pack) after invert_current changes via options dialog or switch.
SIGNAL_OPTIONS_UPDATED = f"{DOMAIN}_{{}}_options"
# Fired (per pack) after a read-only telnet status poll.
SIGNAL_CONTACTOR_UPDATE = f"{DOMAIN}_{{}}_contactor"

# Keep in sync with manifest.json "version" (checked by the offline tests).
SW_VERSION = "1.3.10"
MANUFACTURER = "EVTV"
MODEL = "Tesla pack BMS"

# v1.3.5: per-pack current sign inversion (config/options flag). Some EVTV
# controllers (e.g. PP0, 48 V SolArk pack) report current with the opposite
# sign; HA convention is + = charging.
CONF_INVERT_CURRENT = "invert_current"
DEFAULT_INVERT_CURRENT = False

# v1.3.7: optional telnet host for read-only contactor status. Existing
# entries need no migration; the pack name selects the default.
DEFAULT_TELNET_PORT = 23
DEFAULT_CONTROLLER_HOSTS = {
    "powerpack0": "192.168.6.95",
    "powerpack1": "192.168.6.96",
    "powerpack2": "192.168.6.97",
}
TELNET_POLL_SECONDS = 60
TELNET_LISTEN_SECONDS = 2.5
TELNET_CONNECT_TIMEOUT = 5.0
# v1.3.9 no_screen robustness: one retry with a longer receive-only listen,
# hold the last good main screen for a few minutes, stagger the pack polls.
TELNET_RETRY_LISTEN_SECONDS = 6.5
TELNET_HOLD_SECONDS = 180
TELNET_POLL_OFFSETS = {"powerpack0": 3, "powerpack1": 23, "powerpack2": 43}
# v1.3.9 reboot detector: Runtime dropped by more than this, or the derived
# boot time jumped by more than REBOOT_BOOT_JUMP_S.
REBOOT_RUNTIME_SLACK_S = 30
REBOOT_BOOT_JUMP_S = 180
EVENT_EVTV_REBOOT = "tesla_evtv_bms_evtv_reboot"

# v1.3.8: guarded EVTV Gigavac contactors (PP1/PP2 only).
# Open is blocked if |pack current| exceeds this (amps) unless the Sonoff
# connect path is already off. Close is never allowed from automations.
CONF_OPEN_CURRENT_MAX = "open_current_max"
DEFAULT_OPEN_CURRENT_MAX = 2.0
PRECHARGE_SECONDS = 10.0
# Packs that may have EVTV contactor switches (PP0 is read-only).
CONTACTOR_SWITCH_PACKS = frozenset({"powerpack1", "powerpack2"})
SONOFF_CONNECT = {
    "powerpack1": "switch.powerpack1_connect_main_inverter",
    "powerpack2": "switch.powerpack2_connect_main_inverter",
}
CURRENT_SENSOR = {
    "powerpack1": "sensor.powerpack1_current",
    "powerpack2": "sensor.powerpack2_current",
}
# Existing HA sync / last_good entities reused for the close-on-bus rule.
# v1.3.9: fresh-voltage hysteresis sensor (stale/unknown = not synced).
SYNC_IN_SYNC = "binary_sensor.powerpacks_volts_synced"
SYNC_LAST_GOOD_DELTA = "sensor.powerpacks_volts_last_good_delta"
SYNC_MAX_DIFF = "input_number.battery_sync_max_voltage_diff"
SYNC_CELL_PP1 = "sensor.powerpack1_average_cell"
SYNC_CELL_PP2 = "sensor.powerpack2_average_cell"
SYNC_CELL_MAX_DIFF = 0.05  # same gate as script.reconnect_powerpacks_now
OTHER_PACK = {"powerpack1": "powerpack2", "powerpack2": "powerpack1"}


def default_controller_host(name: str) -> str | None:
    """Default telnet host for a pack name (case-insensitive, no spaces)."""
    key = "".join(ch for ch in name.lower() if ch.isalnum())
    return DEFAULT_CONTROLLER_HOSTS.get(key)


def pack_device_info(name_lower: str, title: str) -> dict:
    """Device info shared by every entity of a pack.

    identifiers stay (DOMAIN, "<name lower>") so the existing device (and all
    entity/device links) is reused. entry_type is set explicitly to None: the
    device registry keeps a previously stored value when the key is omitted,
    and these devices were originally registered as DeviceEntryType.SERVICE.
    """
    return {
        "identifiers": {(DOMAIN, name_lower)},
        "name": title,
        "manufacturer": MANUFACTURER,
        "model": MODEL,
        "sw_version": SW_VERSION,
        "entry_type": None,
        "suggested_area": "Battery Storage",
    }
