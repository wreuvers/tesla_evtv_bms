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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_INVERT_CURRENT,
    DEFAULT_INVERT_CURRENT,
    SIGNAL_OPTIONS_UPDATED,
    pack_device_info,
    SIGNAL_CONTACTOR_UPDATE,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    from .const import CONTACTOR_SWITCH_PACKS
    entities = [InvertCurrentSwitch(hass, entry)]
    name_lower = entry.data["name"].lower()
    if name_lower in CONTACTOR_SWITCH_PACKS:
        entities.extend([
            EvtvContactorSwitch(hass, entry, "negative"),
            EvtvContactorSwitch(hass, entry, "positive"),
        ])
    async_add_entities(entities)


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


class EvtvContactorSwitch(SwitchEntity):
    """Guarded EVTV Gigavac contactor (PP1/PP2). Open/close send only key 1 or 2.

    Closes are blocked for automations (user/dashboard only). Taps on the
    Emergency dashboard use more-info; this entity is the switch inside it.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, pole: str):
        from .const import pack_device_info
        self.hass = hass
        self._entry = entry
        self._pole = pole  # "negative" or "positive"
        self._name_lower = entry.data["name"].lower()
        title = "Negative" if pole == "negative" else "Positive"
        self._attr_name = f"EVTV {title.lower()} contactor"
        self._attr_unique_id = f"{self._name_lower}_evtv_{pole}_contactor"
        self._attr_device_info = pack_device_info(entry.data["name"].lower(), entry.data["name"])
        self.entity_id = f"switch.{self._name_lower}_evtv_{pole}_contactor"
        self._attr_icon = "mdi:electric-switch"

    def _pack(self) -> dict:
        from .const import DOMAIN
        return (self.hass.data.get(DOMAIN, {}) or {}).get(self._name_lower) or {}

    def _contactor(self) -> dict:
        return self._pack().get("contactor") or {}

    @property
    def available(self) -> bool:
        data = self._contactor()
        key = f"{self._pole}_commanded"
        return data.get("available") is True and key in data

    @property
    def is_on(self):
        val = self._contactor().get(f"{self._pole}_commanded")
        if val is None:
            return None
        return bool(val)

    @property
    def extra_state_attributes(self):
        data = self._contactor()
        attrs = {}
        for k in ("screen", "error", "host"):
            if data.get(k) not in (None, ""):
                attrs[k] = data[k]
        reported = data.get(f"{self._pole}_reported")
        if reported is not None:
            attrs["reported"] = reported
        if data.get("welded") is not None:
            attrs["welded"] = data["welded"]
        return attrs or None

    async def async_turn_on(self, **kwargs) -> None:
        from .contactor_ops import async_set_contactor
        try:
            await async_set_contactor(
                self.hass, self._entry, self._name_lower, self._pole, True, self._context
            )
        except Exception as err:
            raise HomeAssistantError(str(err)) from err

    async def async_turn_off(self, **kwargs) -> None:
        from .contactor_ops import async_set_contactor
        try:
            await async_set_contactor(
                self.hass, self._entry, self._name_lower, self._pole, False, self._context
            )
        except Exception as err:
            raise HomeAssistantError(str(err)) from err

    async def async_added_to_hass(self) -> None:
        @callback
        def _updated():
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_CONTACTOR_UPDATE.format(self._name_lower), _updated
            )
        )
