from homeassistant import config_entries
import voluptuous as vol
from .const import DOMAIN, CONF_NAME, CONF_PORT

DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_NAME): str,
    vol.Required(CONF_PORT): int,
    vol.Required("pack_size"): float,
    vol.Required("cells_in_series"): int,
    vol.Required("min_cell_volts"): float,
    vol.Required("max_cell_volts"): float,
})

class TeslaEVTVBMSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA
        )
