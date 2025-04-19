import time
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN, SIGNAL_UPDATE_ENTITY

# Define expected sensor types and their units
SENSOR_TYPES = {
    "state_of_charge": "%",
    "power": "W",
    "current": "A",
    "volts": "V",
    "lowest_cell": "V",
    "highest_cell": "V",
    "average_cell": "V",
    "max_cells": "",
    "active_cells": "",
    "freq_shift_volts": "V",
    "tcch_amps": "A",
    "raw_current": "A"
}

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    name = entry.data["name"].lower()
    coordinator = hass.data.setdefault(DOMAIN, {}).setdefault(name, {
        "entities": {},
        "values": {}
    })

    async def add_or_update_entity(key):
        if key not in coordinator["entities"]:
            unit = SENSOR_TYPES.get(key)
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
        self._state = None
        self._last_update = 0
        self._cooldown = 1.0  # seconds between allowed updates

    @property
    def name(self):
        return f"{self._device} {self._key.replace('_', ' ').title()}"

    @property
    def unique_id(self):
        return f"{self._device}_{self._key}"

    @property
    def state(self):
        return self._state

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

    async def async_added_to_hass(self):
        async def handle_update(values):
            if self._key in values:
                now = time.monotonic()
                if now - self._last_update >= self._cooldown:
                    self._state = values[self._key]
                    self._last_update = now
                    self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_UPDATE_ENTITY.format(self._device),
                handle_update
            )
        )
