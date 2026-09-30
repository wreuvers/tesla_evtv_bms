"""Per-pack 'Invert current sign' switch (v1.3.6).

The switch is a view of the config entry option `invert_current` (same setting
as the Configure dialog). Toggling it stores the option on the config entry
(persisted in core.config_entries) and the update listener in __init__.py
applies it live to the UDP parser - no entry reload, no HA restart.
"""
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_INVERT_CURRENT,
    DEFAULT_INVERT_CURRENT,
    SIGNAL_OPTIONS_UPDATED,
    pack_device_info,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    async_add_entities([InvertCurrentSwitch(hass, entry)])


class InvertCurrentSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "Invert current sign"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:swap-vertical-bold"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self._entry = entry
        self._name_lower = entry.data["name"].lower()
        self._attr_unique_id = f"{self._name_lower}_invert_current"
        self._attr_device_info = pack_device_info(self._name_lower, entry.data["name"])
        # Suggested entity_id for first registration (HA 2026.x would otherwise
        # prefix new entity_ids with the device's area, e.g. battery_storage_).
        self.entity_id = f"switch.{self._name_lower}_invert_current_sign"

    def _option_value(self) -> bool:
        return bool(self._entry.options.get(
            CONF_INVERT_CURRENT,
            self._entry.data.get(CONF_INVERT_CURRENT, DEFAULT_INVERT_CURRENT),
        ))

    @property
    def is_on(self) -> bool:
        pack = self.hass.data.get(DOMAIN, {}).get(self._name_lower) if self.hass else None
        if pack is not None and "invert_current" in pack.get("config", {}):
            return bool(pack["config"]["invert_current"])
        return self._option_value()

    async def async_turn_on(self, **kwargs) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._async_set(False)

    async def _async_set(self, value: bool) -> None:
        # Apply live immediately; the update listener re-applies the same value.
        pack = self.hass.data.get(DOMAIN, {}).get(self._name_lower)
        if pack is not None:
            pack["config"]["invert_current"] = value
        if self._option_value() != value:
            self.hass.config_entries.async_update_entry(
                self._entry, options={**self._entry.options, CONF_INVERT_CURRENT: value}
            )
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        @callback
        def _options_updated():
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_OPTIONS_UPDATED.format(self._name_lower), _options_updated
            )
        )
