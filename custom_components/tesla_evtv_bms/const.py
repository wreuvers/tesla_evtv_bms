DOMAIN = "tesla_evtv_bms"
PLATFORMS = ["sensor", "switch"]

CONF_NAME = "name"
CONF_PORT = "port"

SIGNAL_UPDATE_ENTITY = f"{DOMAIN}_{{}}_update"
# Fired (per pack) after invert_current changes via options dialog or switch.
SIGNAL_OPTIONS_UPDATED = f"{DOMAIN}_{{}}_options"

# Keep in sync with manifest.json "version" (checked by the offline tests).
SW_VERSION = "1.3.6"
MANUFACTURER = "EVTV"
MODEL = "Tesla pack BMS"

# v1.3.5: per-pack current sign inversion (config/options flag). Some EVTV
# controllers (e.g. PP0, 48 V SolArk pack) report current with the opposite
# sign; HA convention is + = charging.
CONF_INVERT_CURRENT = "invert_current"
DEFAULT_INVERT_CURRENT = False


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
