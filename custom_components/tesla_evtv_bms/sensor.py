import time
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN, SIGNAL_UPDATE_ENTITY

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
    "raw_current": "A",
    "battery_pack_energy": "kWh",
    "battery_status": "",
    "consumption": "W",
    "production": "W",
}

ICON_MAP = {
    "state_of_charge": "mdi:battery",
    "power": "mdi:flash",
    "current": "mdi:current-dc",
    "volts": "mdi:car-battery",
    "lowest_cell": "mdi:battery-low",
    "highest_cell": "mdi:battery-high",
    "average_cell": "mdi:battery-medium",
    "max_cells": "mdi:grid",
    "active_cells": "mdi:checkbox-multiple-marked-circle",
    "freq_shift_volts": "mdi:waveform",
    "tcch_amps": "mdi:current-ac",
    "raw_current": "mdi:flash",
    "battery_pack_energy": "mdi:battery-charging-70",
    "battery_status": "mdi:battery-clock",
    "consumption": "mdi:transmission-tower-import",
    "production": "mdi:transmission-tower-export",
}

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    name = entry.data["name"].lower()
    pack_config = {
        "pack_size": entry.data.get("pack_size", 22.0),
        "cells_in_series": entry.data.get("cells_in_series", 96),
        "min_cell_volts": entry.data.get("min_cell_volts", 3.0),
        "max_cell_volts": entry.data.get("max_cell_volts", 4.2),
    }

    coordinator = hass.data.setdefault(DOMAIN, {}).setdefault(name, {
        "entities": {},
        "values": {},
        "config": pack_config
    })

    async def add_or_update_entity(key):
        if key not in coordinator["entities"]:
            unit = SENSOR_TYPES.get(key)
            sensor = TeslaEvtvSensor(name, key, unit, coordinator)
            coordinator["entities"][key] = sensor
            async_add_entities([sensor])

    async def handle_update(updated_values):
        coordinator["values"].update(updated_values)

        # Add computed values
        soc = updated_values.get("state_of_charge")
        power = updated_values.get("power")
        current = updated_values.get("current")
        config = coordinator["config"]
        pack_size = config["pack_size"]

        if soc is not None:
            coordinator["values"]["battery_pack_energy"] = round(pack_size * soc / 100, 2)

        if current is not None:
            if current > 1:
                coordinator["values"]["battery_status"] = "Charging"
            elif current < -1:
                coordinator["values"]["battery_status"] = "Discharging"
            else:
                coordinator["values"]["battery_status"] = "Idle"

        if power is not None:
            coordinator["values"]["production"] = power if power > 0 else 0
            coordinator["values"]["consumption"] = abs(power) if power < 0 else 0

        for key in coordinator["values"]:
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
    def icon(self):
        if self._key == "state_of_charge":
            if self._state is not None:
                soc = float(self._state)
                if soc >= 90:
                    return "mdi:battery"
                elif soc >= 80:
                    return "mdi:battery-90"
                elif soc >= 70:
                    return "mdi:battery-80"
                elif soc >= 60:
                    return "mdi:battery-70"
                elif soc >= 50:
                    return "mdi:battery-60"
                elif soc >= 40:
                    return "mdi:battery-50"
                elif soc >= 30:
                    return "mdi:battery-40"
                elif soc >= 20:
                    return "mdi:battery-30"
                elif soc >= 10:
                    return "mdi:battery-20"
                else:
                    return "mdi:battery-alert"
        return ICON_MAP.get(self._key, "mdi:chip")

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
