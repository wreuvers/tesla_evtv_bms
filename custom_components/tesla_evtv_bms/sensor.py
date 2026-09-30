"""Tesla EVTV BMS sensors.

v1.3.6 changes (unique_ids / entity_ids unchanged):
- Entities are SensorEntity/RestoreSensor (state_class & device_class now
  actually reach HA; the old plain-Entity class never exposed state_class).
- Charge/discharge energy: per pack, positive power -> charge kWh, negative ->
  discharge kWh (left Riemann sum, gaps > 60 s skipped). Totals are
  total_increasing and restored across restarts.
- Charge/Discharge Hour/Day/Week/Month/Year meters actually accumulate now
  (they were only ever reset to 0), reset on local-time calendar boundaries
  (week starts Monday), state_class total with last_reset, restored across
  restarts when the saved period is still current.
- Rolling power averages / hours-to-full/empty are per pack (the sample
  lists used to be one module-level pool shared by all packs).
- Device is a normal device (not a Service), sw_version reported.
- All listeners are released on unload.
"""
import logging
import time
from datetime import datetime, timedelta

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_UPDATE_ENTITY, pack_device_info

_LOGGER = logging.getLogger(__name__)

# key -> (interval, window)   samples are kept per pack in the coordinator
ROLLING_AVERAGE_INTERVALS = {
    "power_average": (timedelta(minutes=1), 10),
    "power_hourly_average": (timedelta(minutes=5), 12),
}

SENSOR_TYPES = {
    "state_of_charge": "%",
    "power": "W",
    "current": "A",
    "volts": "V",
    "raw_current": "",
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
    "cell_difference": "V",
    "trigger_cell_voltage": "V",
    "power_average": "W",
    "power_hourly_average": "W",
    "hours_to_empty": "h",
    "hours_to_full": "h",
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
    "cell_difference": "mdi:arrow-expand-vertical",
    "trigger_cell_voltage": "mdi:transmission-tower",
    "power_average": "mdi:chart-line",
    "power_hourly_average": "mdi:chart-timeline-variant",
    "hours_to_empty": "mdi:battery-alert",
    "hours_to_full": "mdi:battery-clock",
    "summary": "mdi:clock-outline",
}

ENERGY_BASES = ("charge_energy", "discharge_energy")
METER_PERIODS = ("hour", "day", "week", "month", "year")
METER_KEYS = tuple(f"{b}_{p}" for b in ENERGY_BASES for p in METER_PERIODS)
MAX_INTEGRATION_GAP_S = 60.0

VOLTAGE_KEYS = ("volts", "lowest_cell", "highest_cell", "average_cell", "cell_difference",
                "trigger_cell_voltage", "freq_shift_volts")
POWER_KEYS = ("power", "charge", "discharge", "power_average", "power_hourly_average")
MEASUREMENT_KEYS = VOLTAGE_KEYS + POWER_KEYS + (
    "current", "tcch_amps", "state_of_charge", "available_energy", "hours_to_empty", "hours_to_full")
TEXT_KEYS = ("battery_status", "summary")


def period_start(period: str, now: datetime) -> datetime:
    """Start of the local-time calendar period containing `now` (aware, local tz)."""
    now = dt_util.as_local(now)
    if period == "hour":
        return now.replace(minute=0, second=0, microsecond=0)
    today = now.date()
    if period == "day":
        return dt_util.start_of_local_day(today)
    if period == "week":  # Monday, like HA utility_meter
        return dt_util.start_of_local_day(today - timedelta(days=today.weekday()))
    if period == "month":
        return dt_util.start_of_local_day(today.replace(day=1))
    if period == "year":
        return dt_util.start_of_local_day(today.replace(month=1, day=1))
    raise ValueError(period)


def _new_coordinator_state(coordinator: dict) -> None:
    """Per-pack runtime state (idempotent)."""
    coordinator.setdefault("entities", {})
    coordinator.setdefault("values", {})
    coordinator.setdefault("energy", {
        "charge_energy": 0.0,
        "discharge_energy": 0.0,
        "restored": set(),
        "last_mono": None,
        "last_power": None,
    })
    now = dt_util.now()
    coordinator.setdefault("meters", {
        key: {"value": 0.0, "last_reset": period_start(key.rsplit("_", 1)[1], now), "restored": False}
        for key in METER_KEYS
    })
    coordinator.setdefault("rolling", {key: [] for key in ROLLING_AVERAGE_INTERVALS})


def _roll_meter(coordinator: dict, key: str, now: datetime) -> bool:
    """Reset meter if a calendar boundary was crossed. Returns True if reset."""
    meter = coordinator["meters"][key]
    start = period_start(key.rsplit("_", 1)[1], now)
    if start != meter["last_reset"]:
        meter["value"] = 0.0
        meter["last_reset"] = start
        coordinator["values"][key] = 0.0
        return True
    return False


def _add_energy(coordinator: dict, base: str, kwh: float, now: datetime) -> None:
    energy = coordinator["energy"]
    v = coordinator["values"]
    energy[base] += kwh
    v[base] = round(energy[base], 3)
    for period in METER_PERIODS:
        key = f"{base}_{period}"
        _roll_meter(coordinator, key, now)
        meter = coordinator["meters"][key]
        meter["value"] += kwh
        v[key] = round(meter["value"], 3)


def integrate_power(coordinator: dict, power, now_mono: float, now: datetime) -> None:
    """Left-Riemann integration of W into kWh, split by sign. Per pack."""
    energy = coordinator["energy"]
    last_mono, last_power = energy["last_mono"], energy["last_power"]
    if last_mono is not None and isinstance(last_power, (int, float)):
        dt_s = now_mono - last_mono
        if 0 < dt_s <= MAX_INTEGRATION_GAP_S and last_power != 0:
            kwh = abs(last_power) * dt_s / 3_600_000
            _add_energy(coordinator, "charge_energy" if last_power > 0 else "discharge_energy", kwh, now)
    energy["last_mono"] = now_mono
    energy["last_power"] = power if isinstance(power, (int, float)) else None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    name = entry.data["name"].lower()
    title = entry.data["name"]

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
    _new_coordinator_state(coordinator)
    device_info = pack_device_info(name, title)

    def make_sensor(key):
        cls = EnergyMeterSensor if key in METER_KEYS else TeslaEvtvSensor
        return cls(name, key, SENSOR_TYPES.get(key, "kWh" if key in METER_KEYS else ""), coordinator, device_info)

    # Create every known entity up front so restore happens before live data
    # (energy totals/meters seed from their restored state).
    initial = []
    for key in list(SENSOR_TYPES) + list(METER_KEYS):
        if key not in coordinator["entities"]:
            sensor = make_sensor(key)
            coordinator["entities"][key] = sensor
            initial.append(sensor)
    async_add_entities(initial)

    async def add_sensor_entity(key, unit):
        if key not in coordinator["entities"]:
            sensor = TeslaEvtvSensor(name, key, unit, coordinator, device_info)
            coordinator["entities"][key] = sensor
            async_add_entities([sensor])

    async def handle_update(values):
        if "config" not in coordinator:
            return

        coordinator["values"].update(values)

        v = coordinator["values"]
        config = coordinator["config"]
        soc = v.get("state_of_charge")
        power = v.get("power")
        current = v.get("current")
        pack_size = config["pack_size"]

        if isinstance(soc, (int, float)):
            v["available_energy"] = round(pack_size * soc / 100, 2)

        if isinstance(current, (int, float)):
            if current > 1:
                v["battery_status"] = "Charging"
            elif current < -1:
                v["battery_status"] = "Discharging"
            else:
                v["battery_status"] = "Idle"

        if isinstance(power, (int, float)):
            v["discharge"] = abs(power) if power < 0 else 0
            v["charge"] = power if power > 0 else 0
        integrate_power(coordinator, power, time.monotonic(), dt_util.now())

        # Cell Difference
        if all(isinstance(v.get(k), (int, float)) for k in ("highest_cell", "lowest_cell")):
            v["cell_difference"] = round(v["highest_cell"] - v["lowest_cell"], 4)

        # Trigger Cell Voltage
        if isinstance(soc, (int, float)):
            if soc >= 75 and "highest_cell" in v:
                v["trigger_cell_voltage"] = v["highest_cell"]
            elif soc <= 25 and "lowest_cell" in v:
                v["trigger_cell_voltage"] = v["lowest_cell"]
            elif "average_cell" in v:
                v["trigger_cell_voltage"] = v["average_cell"]

        for key in list(v):
            if key in SENSOR_TYPES or key in METER_KEYS:
                continue
            await add_sensor_entity(key, SENSOR_TYPES.get(key, ""))

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_UPDATE_ENTITY.format(name), handle_update)
    )

    # Calendar resets even when no power data arrives (top of every local hour).
    @callback
    def _reset_meters(now):
        for key in METER_KEYS:
            if _roll_meter(coordinator, key, now):
                ent = coordinator["entities"].get(key)
                if ent is not None and ent.hass is not None:
                    ent.async_write_ha_state()

    entry.async_on_unload(async_track_time_change(hass, _reset_meters, minute=0, second=0))

    def track_rolling_averages(interval_key):
        interval, window = ROLLING_AVERAGE_INTERVALS[interval_key]

        async def updater(now):
            power = coordinator["values"].get("power")
            if not isinstance(power, (int, float)):
                return
            samples = coordinator["rolling"][interval_key]
            samples.append(power)
            if len(samples) > window:
                samples.pop(0)

            avg = sum(samples) / len(samples)
            coordinator["values"][interval_key] = round(avg, 1)

            status = coordinator["values"].get("battery_status", "")
            available_energy = coordinator["values"].get("available_energy", 0)
            if not isinstance(available_energy, (int, float)):
                available_energy = 0
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

            for key in (interval_key, "hours_to_empty", "hours_to_full", "summary"):
                ent = coordinator["entities"].get(key)
                if ent is not None and ent.hass is not None:
                    ent.async_write_ha_state()

        entry.async_on_unload(async_track_time_interval(hass, updater, interval))

    for key in ROLLING_AVERAGE_INTERVALS:
        track_rolling_averages(key)


class TeslaEvtvSensor(RestoreSensor):
    _attr_should_poll = False

    def __init__(self, device_name, key, unit, coordinator, device_info=None):
        self._device = device_name
        self._key = key
        self._unit = unit or None
        self._coordinator = coordinator
        self._last_update = 0
        self._cooldown = 1.0
        self._attr_device_info = device_info or pack_device_info(device_name, device_name)
        # Only used if the entity is not yet in the entity registry (existing
        # entities keep their registered entity_id). Matches the historical
        # sensor.<pack>_<key> pattern instead of HA 2026's area-prefixed ids.
        self.entity_id = f"sensor.{device_name}_{key}"

    @property
    def name(self):
        return f"{self._device} {self._key.replace('_', ' ').title()}"

    @property
    def unique_id(self):
        return f"{self._device}_{self._key}"

    @property
    def native_value(self):
        value = self._coordinator["values"].get(self._key)
        if self._is_numeric() and not isinstance(value, (int, float)):
            return None
        return value

    @property
    def native_unit_of_measurement(self):
        return self._unit

    def _is_numeric(self):
        return self._key not in TEXT_KEYS

    @property
    def icon(self):
        soc = self._coordinator["values"].get(self._key)
        if self._key == "state_of_charge" and isinstance(soc, (int, float)):
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
    def device_class(self):
        k = self._key
        if k in ENERGY_BASES or k in METER_KEYS:
            return SensorDeviceClass.ENERGY
        if k == "available_energy":
            return SensorDeviceClass.ENERGY_STORAGE
        if k in VOLTAGE_KEYS:
            return SensorDeviceClass.VOLTAGE
        if k in ("current", "tcch_amps"):
            return SensorDeviceClass.CURRENT
        if k in POWER_KEYS:
            return SensorDeviceClass.POWER
        if k == "state_of_charge":
            return SensorDeviceClass.BATTERY
        return None

    @property
    def state_class(self):
        k = self._key
        if k in ENERGY_BASES:
            return SensorStateClass.TOTAL_INCREASING
        if k in MEASUREMENT_KEYS:
            return SensorStateClass.MEASUREMENT
        return None

    async def _async_restore(self):
        old_state = await self.async_get_last_state()
        if old_state is None or old_state.state in (None, "unknown", "unavailable", ""):
            return None
        try:
            restored = float(old_state.state)
        except ValueError:
            restored = None if self._is_numeric() else old_state.state
        if restored is not None and self._key not in self._coordinator["values"]:
            self._coordinator["values"][self._key] = restored
        return old_state if restored is not None else None

    async def async_added_to_hass(self):
        old_state = await self._async_restore()

        # Seed lifetime energy totals so they survive restarts.
        if self._key in ENERGY_BASES and old_state is not None:
            energy = self._coordinator["energy"]
            if self._key not in energy["restored"]:
                energy["restored"].add(self._key)
                try:
                    energy[self._key] += float(old_state.state)
                except ValueError:
                    pass
                self._coordinator["values"][self._key] = round(energy[self._key], 3)

        async def handle_update(values):
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


class EnergyMeterSensor(TeslaEvtvSensor):
    """charge/discharge_energy_{hour,day,week,month,year}: calendar-reset kWh."""

    @property
    def native_value(self):
        return round(self._coordinator["meters"][self._key]["value"], 3)

    @property
    def state_class(self):
        return SensorStateClass.TOTAL

    @property
    def last_reset(self):
        return self._coordinator["meters"][self._key]["last_reset"]

    async def _async_restore(self):
        old_state = await self.async_get_last_state()
        meter = self._coordinator["meters"][self._key]
        if old_state is None or meter["restored"]:
            return None
        meter["restored"] = True
        try:
            restored = float(old_state.state)
            saved_reset = dt_util.parse_datetime(str(old_state.attributes.get("last_reset") or ""))
        except (ValueError, TypeError):
            return None
        now = dt_util.now()
        _roll_meter(self._coordinator, self._key, now)
        if saved_reset is not None and saved_reset == meter["last_reset"]:
            # Same calendar period as before the restart: keep counting.
            meter["value"] += restored
        self._coordinator["values"][self._key] = round(meter["value"], 3)
        return old_state
