from homeassistant import config_entries
from homeassistant.core import callback
import voluptuous as vol
from .const import DOMAIN, CONF_NAME, CONF_PORT, CONF_INVERT_CURRENT, DEFAULT_INVERT_CURRENT

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
            }),
            description_placeholders={
                "info": "Configure the Tesla EVTV BMS listener"
            }
        )


class TeslaEVTVBMSOptionsFlow(config_entries.OptionsFlow):
    """Per-pack options. invert_current flips the current sign so + = charging."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_INVERT_CURRENT,
            self.config_entry.data.get(CONF_INVERT_CURRENT, DEFAULT_INVERT_CURRENT),
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(CONF_INVERT_CURRENT, default=current): bool,
            }),
        )
