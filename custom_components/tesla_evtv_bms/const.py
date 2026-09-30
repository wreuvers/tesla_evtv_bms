DOMAIN = "tesla_evtv_bms"
PLATFORMS = ["sensor"]

CONF_NAME = "name"
CONF_PORT = "port"

SIGNAL_UPDATE_ENTITY = f"{DOMAIN}_{{}}_update"


# v1.3.5: per-pack current sign inversion (config/options flag). Some EVTV
# controllers (e.g. PP0, 48 V SolArk pack) report current with the opposite
# sign; HA convention is + = charging.
CONF_INVERT_CURRENT = "invert_current"
DEFAULT_INVERT_CURRENT = False
