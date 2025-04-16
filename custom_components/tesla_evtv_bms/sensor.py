from homeassistant.helpers.entity import Entity
from .const import DOMAIN

async def async_setup_entry(hass, config_entry, async_add_entities):
    pass  # Sensors are created dynamically in __init__.py
