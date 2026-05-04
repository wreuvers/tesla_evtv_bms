import time
from datetime import timedelta
from functools import partial

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval, async_track_time_change

from .const import DOMAIN, SIGNAL_UPDATE_ENTITY

ROLLING_AVERAGE_INTERVALS = {
    "power_average": {"interval": timedelta(minutes=1), "window": 10, "samples": []},
    "power_hourly_average": {"interval": timedelta(minutes=5), "window": 12, "samples": []},
}

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
    "battery_status": "",
    "charge": "W",
    "discharge": "W",
    "charge_energy": "kWh",
    "discharge_energy": "kWh",
    "available_energy": "kWh",
    "charge_energy_hour": "kWh",
    "charge_energy_day": "kWh",
    "charge_energy_week": "kWh",
    "charge_energy_month": "kWh",
    "charge_energy_year": "kWh",
    "discharge_energy_hour": "kWh",
    "discharge_energy_day": "kWh",
    "discharge_energy_week": "kWh",
    "discharge_energy_month": "kWh",
    "discharge_energy_year": "kWh",
    "cell_difference": "V",
    "trigger_cell_voltage": "V",
    "power_average": "W",
    "power_hourly_average": "W",
    "hours_to_empty": "h",
    "hours_to_full": "h",
    "lowest_temp": "°C",
    "highest_temp": "°C",
    "pack_ah_used": "Ah",
    "high_voltage_cutoff": "V",
    "low_voltage_cutoff": "V",
    "contactor_negative": "",
    "contactor_positive": "",
    "charge_enable": "",
    "heat_enable": "",
    "power_source": "",
    "fault_code": "",
    "fault_status": "",
    "total_modules": "",
    "total_cells": "",
    "summary": "",
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
    "charge": "mdi:transmission-tower-import",
    "discharge": "mdi:transmission-tower-export",
    "charge_energy": "mdi:transmission-tower-import",
    "discharge_energy": "mdi:transmission-tower-export",
    "available_energy": "mdi:battery-charging-70",
    "charge_energy_hour": "mdi:transmission-tower-import",
    "charge_energy_day": "mdi:transmission-tower-import",
    "charge_energy_week": "mdi:transmission-tower-import",
    "charge_energy_month": "mdi:transmission-tower-import",
    "charge_energy_year": "mdi:transmission-tower-import",
    "discharge_energy_hour": "mdi:transmission-tower-export",
    "discharge_energy_day": "mdi:transmission-tower-export",
    "discharge_energy_week": "mdi:transmission-tower-export",
    "discharge_energy_month": "mdi:transmission-tower-export",
    "discharge_energy_year": "mdi:transmission-tower-export",
    "cell_difference": "mdi:arrow-expand-vertical",
    "trigger_cell_voltage": "mdi:transmission-tower",
    "power_average": "mdi:chart-line",
    "power_hourly_average": "mdi:chart-timeline-variant",
    "hours_to_empty": "mdi:battery-alert",
    "hours_to_full": "mdi:battery-clock",
    "lowest_temp": "mdi:thermometer-low",
    "highest_temp": "mdi:thermometer-high",
    "pack_ah_used": "mdi:counter",
    "high_voltage_cutoff": "mdi:arrow-up-bold-circle",
    "low_voltage_cutoff": "mdi:arrow-down-bold-circle",
    "contactor_negative": "mdi:electric-switch",
    "contactor_positive": "mdi:electric-switch",
    "charge_enable": "mdi:battery-plus-variant",
    "heat_enable": "mdi:radiator",
    "power_source": "mdi:power-plug",
    "fault_code": "mdi:numeric",
    "fault_status": "mdi:alert-circle",
    "total_modules": "mdi:cube-outline",
    "total_cells": "mdi:checkbox-multiple-marked-circle",
    "summary": "mdi:clock-outline",
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
        "config": pack_config,
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
                "charge": 0.0,
                "discharge": 0.0,
                "last_update": time.monotonic()
            }

        coordinator["values"].update(values)

        v = coordinator["values"]
        config = coordinator["config"]
        soc = v.get("state_of_charge")
        power = v.get("power")
        current = v.get("current")
        pack_size = config["pack_size"]

        if soc is not None:
            v["available_energy"] = round(pack_size * soc / 100, 2)

        if current is not None:
            if current > 1:
                v["battery_status"] = "Charging"
            elif current < -1:
                v["battery_status"] = "Discharging"
            else:
                v["battery_status"] = "Idle"

        if power is not None:
            v["discharge"] = abs(power) if power < 0 else 0
            v["charge"] = power if power > 0 else 0

            now = time.monotonic()
            delta = now - coordinator["energy"]["last_update"]
            coordinator["energy"]["last_update"] = now

            # Energy increment in kWh for this update interval
            increment = (abs(power) * delta / 3600) / 1000

            if power < 0:
                coordinator["energy"]["discharge"] += increment
                # Each period meter is its own accumulator. Reset at period
                # boundaries; persisted across restarts via RestoreEntity.
                for label in UTILITY_METER_PERIODS:
                    mk = f"discharge_energy_{label}"
                    v[mk] = round(v.get(mk, 0.0) + increment, 3)
            elif power > 0:
                coordinator["energy"]["charge"] += increment
                for label in UTILITY_METER_PERIODS:
                    mk = f"charge_energy_{label}"
                    v[mk] = round(v.get(mk, 0.0) + increment, 3)

            v["discharge_energy"] = round(coordinator["energy"]["discharge"], 3)
            v["charge_energy"] = round(coordinator["energy"]["charge"], 3)

        # Cell Difference
        if all(k in v for k in ("highest_cell", "lowest_cell")):
            v["cell_difference"] = round(v["highest_cell"] - v["lowest_cell"], 4)

        # Trigger Cell Voltage
        if soc is not None:
            if soc >= 75 and "highest_cell" in v:
                v["trigger_cell_voltage"] = v["highest_cell"]
            elif soc <= 25 and "lowest_cell" in v:
                v["trigger_cell_voltage"] = v["lowest_cell"]
            elif "average_cell" in v:
                v["trigger_cell_voltage"] = v["average_cell"]

        for key in v:
            unit = SENSOR_TYPES.get(key, "")
            await add_sensor_entity(key, unit)

    async_dispatcher_connect(
        hass,
        SIGNAL_UPDATE_ENTITY.format(name),
        handle_update
    )

    def create_utility_updater(base_key):
        # Each period meter is its own accumulator stored in coordinator["values"]
        # so RestoreEntity will repopulate it on HA restart. Resets fire at
        # wall-clock period boundaries.
        for label in UTILITY_METER_PERIODS:
            coordinator["values"].setdefault(f"{base_key}_{label}", 0.0)

        async def reset_meter(meter_key):
            coordinator["values"][meter_key] = 0.0
            entity = coordinator["entities"].get(meter_key)
            if entity is not None:
                entity.async_schedule_update_ha_state()

        async def hourly(now, base=base_key):
            await reset_meter(f"{base}_hour")

        async def daily(now, base=base_key):
            # Fires at 00:00 every day; week/month/year branches fire conditionally
            await reset_meter(f"{base}_day")
            if now.weekday() == 0:  # Monday
                await reset_meter(f"{base}_week")
            if now.day == 1:
                await reset_meter(f"{base}_month")
                if now.month == 1:
                    await reset_meter(f"{base}_year")

        async_track_time_change(hass, hourly, minute=0, second=0)
        async_track_time_change(hass, daily, hour=0, minute=0, second=0)

    create_utility_updater("discharge_energy")
    create_utility_updater("charge_energy")

    def track_rolling_averages(interval_key):
        interval_info = ROLLING_AVERAGE_INTERVALS[interval_key]

        async def updater(now):
            power = coordinator["values"].get("power")
            if power is not None:
                interval_info["samples"].append(power)
                if len(interval_info["samples"]) > interval_info["window"]:
                    interval_info["samples"].pop(0)

                avg = sum(interval_info["samples"]) / len(interval_info["samples"])
                key_name = interval_key
                coordinator["values"][key_name] = round(avg, 1)
                await add_sensor_entity(key_name, "W")

                status = coordinator["values"].get("battery_status", "")
                available_energy = coordinator["values"].get("available_energy", 0)
                pack_size = coordinator["config"]["pack_size"]

                if abs(avg) > 0:
                    if status == "Discharging":
                        coordinator["values"]["hours_to_empty"] = round(available_energy / (abs(avg) / 1000), 2)
                        coordinator["values"]["hours_to_full"] = 0
                    elif status == "Charging":
                        coordinator["values"]["hours_to_empty"] = 0
                        coordinator["values"]["hours_to_full"] = round((pack_size - available_energy) / (abs(avg) / 1000), 2)
                    else:
                        coordinator["values"]["hours_to_empty"] = 0
                        coordinator["values"]["hours_to_full"] = 0
                else:
                    coordinator["values"]["hours_to_empty"] = 0
                    coordinator["values"]["hours_to_full"] = 0

                await add_sensor_entity("hours_to_empty", "h")
                await add_sensor_entity("hours_to_full", "h")

                # Summary Sensor Logic
                summary_value = "Idle"
                if status == "Discharging":
                    hrs = coordinator["values"]["hours_to_empty"]
                    hrs_str = f"{hrs:.1f}" if hrs < 10 else f"{int(hrs)}"
                    summary_value = f"{hrs_str} hrs to Empty"
                elif status == "Charging":
                    hrs = coordinator["values"]["hours_to_full"]
                    hrs_str = f"{hrs:.1f}" if hrs < 10 else f"{int(hrs)}"
                    summary_value = f"{hrs_str} hrs to Full"

                coordinator["values"]["summary"] = summary_value
                await add_sensor_entity("summary", "")

        async_track_time_interval(hass, updater, interval_info["interval"])

    for key in ROLLING_AVERAGE_INTERVALS:
        track_rolling_averages(key)

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
        return self._coordinator["values"].get(self._key)

    @property
    def unit_of_measurement(self):
        return self._unit

    @property
    def icon(self):
        soc = self.state
        if self._key == "state_of_charge" and soc is not None:
            soc = float(soc)
            for threshold, icon in zip(
                [90, 80, 70, 60, 50, 40, 30, 20, 10],
                [
                    "mdi:battery",
                    "mdi:battery-90",
                    "mdi:battery-80",
                    "mdi:battery-70",
                    "mdi:battery-60",
                    "mdi:battery-50",
                    "mdi:battery-40",
                    "mdi:battery-30",
                    "mdi:battery-20",
                    "mdi:battery-alert",
                ],
            ):
                if soc >= threshold:
                    return icon
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
        if self._key.endswith("_energy") or "_energy_" in self._key or self._key in ("available_energy",):
            return "energy"
        if self._key in ("volts", "lowest_cell", "highest_cell", "average_cell", "cell_difference", "trigger_cell_voltage", "high_voltage_cutoff", "low_voltage_cutoff"):
            return "voltage"
        if self._key in ("current", "tcch_amps"):
            return "current"
        if self._key == "power":
            return "power"
        if self._key in ("lowest_temp", "highest_temp"):
            return "temperature"
        return None

    @property
    def state_class(self):
        if self._key.endswith("_energy") or "_energy_" in self._key or self._key in ("available_energy",):
            return "total_increasing"
        if self._key in ("power", "volts", "current", "state_of_charge", "cell_difference", "trigger_cell_voltage", "power_average", "power_hourly_average", "hours_to_empty", "hours_to_full", "lowest_temp", "highest_temp", "pack_ah_used", "high_voltage_cutoff", "low_voltage_cutoff"):
            return "measurement"
        return None

    async def async_added_to_hass(self):
        old_state = await self.async_get_last_state()
        if old_state and old_state.state not in (None, "unknown", ""):
            try:
                self._coordinator["values"][self._key] = float(old_state.state)
            except ValueError:
                self._coordinator["values"][self._key] = old_state.state

        async def handle_update(values):
            if self._key in values:
                now = time.monotonic()
                if now - self._last_update >= self._cooldown:
                    self._last_update = now
                    self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_UPDATE_ENTITY.format(self._device),
                handle_update
            )
        )
