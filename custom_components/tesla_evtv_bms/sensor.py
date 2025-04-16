from homeassistant.helpers.entity import Entity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN, SIGNAL_UPDATE_ENTITY

SENSOR_TYPES = {
    "state_of_charge": "%", "power": "W", "current": "A", "volts": "V",
    "lowest_cell": "V", "highest_cell": "V", "average_cell": "V",
    "max_cells": "", "active_cells": "", "freq_shift_volts": "V", "tcch_amps": "A"
}

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    name = entry.data["name"].lower()
    coordinator = hass.data.setdefault(DOMAIN, {}).setdefault(name, {
        "entities": {}, "values": {}
    })

    async def add_or_update_entity(key):
        if key not in coordinator["entities"]:
            unit = SENSOR_TYPES.get(key, None)
            sensor = TeslaEvtvSensor(name, key, unit, coordinator)
            coordinator["entities"][key] = sensor
            async_add_entities([sensor])

    async def handle_update(updated_values):
        coordinator["values"].update(updated_values)
        for key in updated_values:
            await add_or_update_entity(key)

    hass.helpers.dispatcher.async_dispatcher_connect(
        SIGNAL_UPDATE_ENTITY.format(name),
        handle_update
    )

class TeslaEvtvSensor(Entity):
    def __init__(self, device_name, key, unit, coordinator):
        self._device = device_name
        self._key = key
        self._unit = unit
        self._coordinator = coordinator

    @property
    def name(self):
        return f"{self._device} {self._key.replace('_', ' ').title()}"

    @property
    def unique_id(self):
        return f"{self._device}_{self._key}"

    @property
    def state(self):
        return self._coordinator["values"].get(self._key)

    @property
    def unit_of_measurement(self):
        return self._unit

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device)},
            "name": self._device,
            "manufacturer": "EVTV",
            "model": "Tesla BMS"
        }
