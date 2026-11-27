from homeassistant import config_entries
from homeassistant.core import callback
import voluptuous as vol
from .const import (
    DOMAIN,
    CONF_NAME,
    CONF_PORT,
    CONF_HOST,
    CONF_TELNET_PORT,
    CONF_INVERT_CURRENT,
    DEFAULT_INVERT_CURRENT,
    DEFAULT_TELNET_PORT,
    CONF_OPEN_CURRENT_MAX,
    DEFAULT_OPEN_CURRENT_MAX,
    CONF_MIN_CELL_VOLTS,
    CONF_MAX_CELL_VOLTS,
    CONF_CUTOFF_SOC_OVERRIDE,
    DEFAULT_MIN_CELL_VOLTS,
    DEFAULT_MAX_CELL_VOLTS,
    DEFAULT_PACK_SIZE,
    DEFAULT_CELLS_SERIES,
    default_controller_host,
)

DEFAULT_PORT = 6850


class TeslaEVTVBMSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return TeslaEVTVBMSOptionsFlow()

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
                vol.Required("pack_size", default=DEFAULT_PACK_SIZE): vol.Coerce(float),
                vol.Required("cells_in_series", default=DEFAULT_CELLS_SERIES): vol.Coerce(int),
                vol.Required(CONF_MIN_CELL_VOLTS, default=DEFAULT_MIN_CELL_VOLTS): vol.Coerce(float),
                vol.Required(CONF_MAX_CELL_VOLTS, default=DEFAULT_MAX_CELL_VOLTS): vol.Coerce(float),
                vol.Optional(CONF_INVERT_CURRENT, default=DEFAULT_INVERT_CURRENT): bool,
                vol.Optional(CONF_HOST, default=""): str,
                vol.Optional(CONF_TELNET_PORT, default=DEFAULT_TELNET_PORT): vol.Coerce(int),
            }),
            description_placeholders={
                "info": "Configure the Tesla EVTV BMS listener"
            }
        )


class TeslaEVTVBMSOptionsFlow(config_entries.OptionsFlow):
    """Per-pack options. invert_current flips the current sign so + = charging.

    host / telnet_port are optional; blank host uses the built-in default
    for Powerpack0/1/2. Status poll is read-only; PP1/PP2 switches may send key 1 or 2 after guards.
    """

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_INVERT_CURRENT,
            self.config_entry.data.get(CONF_INVERT_CURRENT, DEFAULT_INVERT_CURRENT),
        )
        name = self.config_entry.data.get(CONF_NAME, "")
        host = self.config_entry.options.get(
            CONF_HOST,
            self.config_entry.data.get(CONF_HOST, default_controller_host(name) or ""),
        )
        telnet_port = self.config_entry.options.get(
            CONF_TELNET_PORT,
            self.config_entry.data.get(CONF_TELNET_PORT, DEFAULT_TELNET_PORT),
        )
        open_max = self.config_entry.options.get(
            CONF_OPEN_CURRENT_MAX,
            self.config_entry.data.get(CONF_OPEN_CURRENT_MAX, DEFAULT_OPEN_CURRENT_MAX),
        )
        min_cell = self.config_entry.options.get(
            CONF_MIN_CELL_VOLTS,
            self.config_entry.data.get(CONF_MIN_CELL_VOLTS, DEFAULT_MIN_CELL_VOLTS),
        )
        override = self.config_entry.options.get(CONF_CUTOFF_SOC_OVERRIDE, "")
        if override is None:
            override = ""
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(CONF_INVERT_CURRENT, default=current): bool,
                vol.Optional(CONF_HOST, default=host or ""): str,
                vol.Optional(CONF_TELNET_PORT, default=int(telnet_port)): vol.Coerce(int),
                vol.Optional(CONF_OPEN_CURRENT_MAX, default=float(open_max)): vol.Coerce(float),
                vol.Optional(CONF_MIN_CELL_VOLTS, default=float(min_cell)): vol.Coerce(float),
                vol.Optional(CONF_CUTOFF_SOC_OVERRIDE, default=str(override)): str,
            }),
        )
