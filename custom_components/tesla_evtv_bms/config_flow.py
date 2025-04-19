from homeassistant import config_entries
import voluptuous as vol
from .const import DOMAIN, CONF_NAME, CONF_PORT

DEFAULT_PORT = 6850
DEFAULT_PACK_SIZE = 75.0
DEFAULT_CELLS_SERIES = 96
DEFAULT_MIN_VOLTS = 3.2
DEFAULT_MAX_VOLTS = 4.1

class TeslaEVTVBMSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

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
            }),
            description_placeholders={
                "info": "Configure the Tesla BMS listener"
            }
        )
