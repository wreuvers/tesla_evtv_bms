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
