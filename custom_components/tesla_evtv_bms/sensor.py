# sensor.py - Final Version with Raw Current Hidden, Cell Difference, Trigger Cell Voltage

import time
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

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
    "battery_pack_energy": "kWh",
    "battery_status": "",
    "consumption": "W",
    "production": "W",
    "consumption_energy": "kWh",
    "production_energy": "kWh",
    "cell_difference": "V",
    "trigger_cell_voltage": "V"
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
    "battery_pack_energy": "mdi:battery-charging-70",
    "battery_status": "mdi:battery-clock",
    "consumption": "mdi:transmission-tower-import",
    "production": "mdi:transmission-tower-export",
    "consumption_energy": "mdi:transmission-tower-import",
    "production_energy": "mdi:transmission-tower-export",
    "cell_difference": "mdi:arrow-expand-vertical",
    "trigger_cell_voltage": "mdi:transmission-tower"
}

UTILITY_METER_PERIODS = {
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
    "month": timedelta(days=30),
    "year": timedelta(days=365),
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

    async def add_sensor_entity(key, unit):
        if key not in coordinator["entities"]:
            sensor = TeslaEvtvSensor(name, key, unit, coordinator)
            coordinator["entities"][key] = sensor
            async_add_entities([sensor])

    async def handle_update(values):
        if "config" not in coordinator:
            return

        if "energy" not in coordinator:
            coordinator["energy"] = {
                "consumption": 0.0,
                "production": 0.0,
                "last_update": time.monotonic()
            }

        # Exclude raw_current
        if "raw_current" in values:
            del values["raw_current"]

        coordinator["values"].update(values)

        soc = values.get("state_of_charge")
        power = values.get("power")
        current = values.get("current")
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
            coordinator["values"]["production"] = abs(power) if power < 0 else 0
            coordinator["values"]["consumption"] = power if power > 0 else 0

            now = time.monotonic()
            delta = now - coordinator["energy"]["last_update"]
            coordinator["energy"]["last_update"] = now

            if power < 0:
                coordinator["energy"]["production"] += (abs(power) * delta / 3600) / 1000
            elif power > 0:
                coordinator["energy"]["consumption"] += (power * delta / 3600) / 1000

            coordinator["values"]["production_energy"] = round(coordinator["energy"]["production"], 3)
            coordinator["values"]["consumption_energy"] = round(coordinator["energy"]["consumption"], 3)

        # ➕ Add calculated sensors:
        if all(k in coordinator["values"] for k in ("highest_cell", "lowest_cell")):
            coordinator["values"]["cell_difference"] = round(
                coordinator["values"]["highest_cell"] - coordinator["values"]["lowest_cell"], 4
            )

        if soc is not None:
            if soc >= 75 and "highest_cell" in coordinator["values"]:
                coordinator["values"]["trigger_cell_voltage"] = coordinator["values"]["highest_cell"]
            elif soc <= 25 and "lowest_cell" in coordinator["values"]:
                coordinator["values"]["trigger_cell_voltage"] = coordinator["values"]["lowest_cell"]
            elif "average_cell" in coordinator["values"]:
                coordinator["values"]["trigger_cell_voltage"] = coordinator["values"]["average_cell"]

        for key in coordinator["values"]:
            unit = SENSOR_TYPES.get(key, "")
            await add_sensor_entity(key, unit)

    hass.helpers.dispatcher.async_dispatcher_connect(
        SIGNAL_UPDATE_ENTITY.format(name),
        handle_update
    )

    def create_utility_updater(base_key):
        for label, interval in UTILITY_METER_PERIODS.items():
            meter_key = f"{base_key}_{label}"
            coordinator["values"][meter_key] = 0.0

            async def update_utility_sensor(now, key=meter_key, base=base_key):
                coordinator["values"][key] = coordinator["values"].get(base, 0.0)
                if key not in coordinator["entities"]:
                    await add_sensor_entity(key, "kWh")

            async_track_time_interval(hass, update_utility_sensor, interval)

    create_utility_updater("production_energy")
    create_utility_updater("consumption_energy")


class TeslaEvtvSensor(RestoreEntity):
    def __init__(self, device_name, key, unit, coordinator):
        self._device = device_name
        self._key = key
        self._unit = unit
        self._coordinator = coordinator
        self._state = None
        self._last_update = 0
        self._cooldown = 1.0

    @property
    def name(self):
        return f"{self._device} {self._key.replace('_', ' ').title()}"

    @property
    def unique_id(self):
        return f"{self._device}_{self._key}"

    @property
    def state(self):
        return self._state or self._coordinator["values"].get(self._key)

    @property
    def unit_of_measurement(self):
        return self._unit

    @property
    def icon(self):
        if self._key == "state_of_charge" and self.state is not None:
            soc = float(self.state)
            thresholds = [90, 80, 70, 60, 50, 40, 30, 20, 10]
            icons = [
                "mdi:battery",
                "mdi:battery-90",
                "mdi:battery-80",
                "mdi:battery-70",
                "mdi:battery-60",
                "mdi:battery-50",
                "mdi:battery-40",
                "mdi:battery-30",
                "mdi:battery-20",
                "mdi:battery-alert"
            ]
            for i, threshold in enumerate(thresholds):
                if soc >= threshold:
                    return icons[i]
        return ICON_MAP.get(self._key, "mdi:chip")

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device)},
            "name": self._device,
            "manufacturer": "EVTV",
            "model": "Tesla BMS",
            "entry_type": "service",
            "suggested_area": "Battery Storage"
        }

    @property
    def device_class(self):
        if self._key.endswith("_energy"):
            return "energy"
        if self._key in ("volts", "lowest_cell", "highest_cell", "average_cell", "cell_difference", "trigger_cell_voltage"):
            return "voltage"
        if self._key in ("current", "tcch_amps"):
            return "current"
        if self._key == "power":
            return "power"
        return None

    @property
    def state_class(self):
        if self._key.endswith("_energy"):
            return "total_increasing"
        if self._key in ("power", "volts", "current", "state_of_charge", "cell_difference", "trigger_cell_voltage"):
            return "measurement"
        return None

    async def async_added_to_hass(self):
        old_state = await self.async_get_last_state()
        if old_state and old_state.state not in (None, "unknown", ""):
            try:
                self._state = float(old_state.state)
            except ValueError:
                self._state = old_state.state

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
