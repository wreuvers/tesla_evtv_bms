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
SW_VERSION = "1.3.7"
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
