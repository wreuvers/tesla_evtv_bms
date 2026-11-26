"""Read-only EVTV contactor binary sensors (v1.3.7).

State comes from short-lived, receive-only telnet polls. No keys are sent.
"""
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_CONTACTOR_UPDATE, pack_device_info

# key, name, device_class, icon, unique/entity suffix
CONTACTOR_BINARY = (
    ("negative_commanded", "Negative contactor commanded", None, "mdi:electric-switch", "negative_contactor_commanded"),
    ("negative_reported", "Negative contactor reported", None, "mdi:electric-switch-closed", "negative_contactor_reported"),
    ("positive_commanded", "Positive contactor commanded", None, "mdi:electric-switch", "positive_contactor_commanded"),
    ("positive_reported", "Positive contactor reported", None, "mdi:electric-switch-closed", "positive_contactor_reported"),
    ("welded", "Contactor welded warning", BinarySensorDeviceClass.PROBLEM, "mdi:alert", "contactor_welded_warning"),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    name_lower = entry.data["name"].lower()
    title = entry.data["name"]
    device_info = pack_device_info(name_lower, title)
    async_add_entities(
        [
            EvtvContactorBinarySensor(name_lower, key, label, device_class, icon, suffix, device_info)
            for key, label, device_class, icon, suffix in CONTACTOR_BINARY
        ]
        + [
            EvtvDerivedBinarySensor(name_lower, key, label, icon, suffix, device_info)
            for key, label, icon, suffix in DERIVED_BINARY
        ]
    )


class EvtvContactorBinarySensor(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, name_lower, key, label, device_class, icon, suffix, device_info):
        self._name_lower = name_lower
        self._key = key
        self._attr_name = label
        self._attr_unique_id = f"{name_lower}_{suffix}"
        self._attr_device_info = device_info
        self.entity_id = f"binary_sensor.{name_lower}_{suffix}"
        self._attr_icon = icon
        self._attr_device_class = device_class

    def _contactor(self) -> dict:
        pack = (self.hass.data.get(DOMAIN, {}) if self.hass else {}).get(self._name_lower) or {}
        return pack.get("contactor") or {}

    @property
    def available(self) -> bool:
        data = self._contactor()
        return data.get("available") is True and self._key in data

    @property
    def is_on(self):
        val = self._contactor().get(self._key)
        if val is None:
            return None
        return bool(val)

    @property
    def extra_state_attributes(self):
        data = self._contactor()
        attrs = {}
        if data.get("screen"):
            attrs["screen"] = data["screen"]
        if data.get("error"):
            attrs["error"] = data["error"]
        if data.get("host"):
            attrs["host"] = data["host"]
        return attrs or None

    async def async_added_to_hass(self) -> None:
        @callback
        def _updated():
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_CONTACTOR_UPDATE.format(self._name_lower), _updated
            )
        )


# v1.3.9: derived, read-only health flags from the same telnet main screen.
# key, name, icon, unique/entity suffix
DERIVED_BINARY = (
    ("faulted", "EVTV faulted", "mdi:battery-alert", "evtv_faulted"),
    ("contactors_open", "EVTV contactors open", "mdi:electric-switch", "evtv_contactors_open"),
    ("temperature_alarm", "EVTV temperature alarm", "mdi:thermometer-alert", "evtv_temperature_alarm"),
    ("voltage_alarm", "EVTV voltage alarm", "mdi:flash-alert", "evtv_voltage_alarm"),
)


def derived_value(data: dict, key: str):
    """Return True/False/None for a derived flag from contactor state."""
    if key == "contactors_open":
        # v1.3.10: REPORTED (auxiliary feedback) state only. A commanded/reported
        # mismatch (e.g. commanded OPEN but still reported CLOSED = possible weld)
        # is exposed as the commanded_mismatch attribute, not counted as open.
        vals = [data.get(k) for k in ("negative_reported", "positive_reported")]
        if all(v is None for v in vals):
            return None
        return any(v is False for v in vals)
    val = data.get(key)
    if val is None:
        return None
    return bool(val)


class EvtvDerivedBinarySensor(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, name_lower, key, label, icon, suffix, device_info):
        self._name_lower = name_lower
        self._key = key
        self._attr_name = label
        self._attr_unique_id = f"{name_lower}_{suffix}"
        self._attr_device_info = device_info
        self.entity_id = f"binary_sensor.{name_lower}_{suffix}"
        self._attr_icon = icon

    def _contactor(self) -> dict:
        pack = (self.hass.data.get(DOMAIN, {}) if self.hass else {}).get(self._name_lower) or {}
        return pack.get("contactor") or {}

    @property
    def available(self) -> bool:
        data = self._contactor()
        return data.get("screen") == "main" and derived_value(data, self._key) is not None

    @property
    def is_on(self):
        return derived_value(self._contactor(), self._key)

    @property
    def extra_state_attributes(self):
        data = self._contactor()
        attrs = {}
        for k in ("pack_status", "held", "held_reason", "host"):
            if data.get(k) not in (None, ""):
                attrs[k] = data[k]
        if data.get("last_main") is not None:
            attrs["last_main_screen"] = data["last_main"].isoformat()
        if self._key == "contactors_open":
            mism = []
            for pole in ("negative", "positive"):
                c, r = data.get(f"{pole}_commanded"), data.get(f"{pole}_reported")
                if c is not None and r is not None and c != r:
                    mism.append(f"{pole} commanded {'closed' if c else 'open'} / reported {'closed' if r else 'open'}")
            attrs["commanded_mismatch"] = "; ".join(mism) if mism else "none"
            attrs["basis"] = "reported contactor feedback only"
        if data.get("fields_held"):
            attrs["fields_held"] = ", ".join(data["fields_held"])
        return attrs or None

    async def async_added_to_hass(self) -> None:
        @callback
        def _updated():
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_CONTACTOR_UPDATE.format(self._name_lower), _updated
            )
        )
