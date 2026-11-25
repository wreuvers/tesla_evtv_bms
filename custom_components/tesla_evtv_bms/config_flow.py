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
    default_controller_host,
)

DEFAULT_PORT = 6850
DEFAULT_PACK_SIZE = 75.0
DEFAULT_CELLS_SERIES = 96
DEFAULT_MIN_VOLTS = 3.2
DEFAULT_MAX_VOLTS = 4.1


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
                vol.Required("min_cell_volts", default=DEFAULT_MIN_VOLTS): vol.Coerce(float),
                vol.Required("max_cell_volts", default=DEFAULT_MAX_VOLTS): vol.Coerce(float),
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
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(CONF_INVERT_CURRENT, default=current): bool,
                vol.Optional(CONF_HOST, default=host or ""): str,
                vol.Optional(CONF_TELNET_PORT, default=int(telnet_port)): vol.Coerce(int),
                vol.Optional(CONF_OPEN_CURRENT_MAX, default=float(open_max)): vol.Coerce(float),
            }),
        )
