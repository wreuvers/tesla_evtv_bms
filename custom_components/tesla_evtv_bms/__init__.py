import asyncio
import socket
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DOMAIN,
    PLATFORMS,
    SIGNAL_UPDATE_ENTITY,
    SIGNAL_OPTIONS_UPDATED,
    CONF_INVERT_CURRENT,
    DEFAULT_INVERT_CURRENT,
)
from .parser import parse_udp_packet

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    name = entry.data["name"]
    port = entry.data["port"]
    name_lower = name.lower()
    # Options override data (options flow); default False keeps PP1/PP2 unchanged.
    invert_current = bool(entry.options.get(
        CONF_INVERT_CURRENT,
        entry.data.get(CONF_INVERT_CURRENT, DEFAULT_INVERT_CURRENT),
    ))

    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    hass.data[DOMAIN][name_lower] = {
        "entities": {},
        "values": {},
        "config": {
            "pack_size": entry.data.get("pack_size", 22.0),
            "cells_in_series": entry.data.get("cells_in_series", 96),
            "min_cell_volts": entry.data.get("min_cell_volts", 3.0),
            "max_cell_volts": entry.data.get("max_cell_volts", 4.2),
            "invert_current": invert_current,
        },
        "socket": None
    }

    def udp_callback(sock):
        try:
            data, _ = sock.recvfrom(1024)
            parsed = parse_udp_packet(
                data, port, hass.data[DOMAIN][name_lower]["config"]["invert_current"]
            )
            if parsed:
                name_data = hass.data[DOMAIN][name_lower]
                previous_values = name_data.get("values", {})
                merged_values = {**previous_values, **parsed}

                async_dispatcher_send(
                    hass,
                    SIGNAL_UPDATE_ENTITY.format(name_lower),
                    merged_values
                )
        except BlockingIOError:
            _LOGGER.debug(f"[{DOMAIN}] Non-blocking UDP read would block on {name}")
        except Exception as e:
            _LOGGER.error(f"[{DOMAIN}] UDP read error on {name}: {e}")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", port))
        sock.setblocking(False)
        loop = asyncio.get_event_loop()
        loop.add_reader(sock, udp_callback, sock)
        hass.data[DOMAIN][name_lower]["socket"] = sock
        _LOGGER.info(
            "Started non-blocking UDP listener for %s on port %d (invert_current=%s)",
            name, port, invert_current,
        )
    except OSError as e:
        _LOGGER.error("Failed to bind UDP socket on port %d for %s: %s", port, name, e)
        hass.data[DOMAIN].pop(name_lower, None)  # Clean up
        return False

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Options change: update invert_current in place (no entry reload/socket rebind).
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    name_lower = entry.data["name"].lower()
    pack = hass.data.get(DOMAIN, {}).get(name_lower)
    if pack is None:
        return
    invert_current = bool(entry.options.get(
        CONF_INVERT_CURRENT,
        entry.data.get(CONF_INVERT_CURRENT, DEFAULT_INVERT_CURRENT),
    ))
    if pack["config"].get("invert_current") != invert_current:
        pack["config"]["invert_current"] = invert_current
        _LOGGER.info("%s: invert_current set to %s", entry.data["name"], invert_current)
    # Let the Invert switch (and anything else) refresh its state.
    async_dispatcher_send(hass, SIGNAL_OPTIONS_UPDATED.format(name_lower))

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    name_lower = entry.data["name"].lower()
    if sock := hass.data[DOMAIN][name_lower].get("socket"):
        loop = asyncio.get_event_loop()
        loop.remove_reader(sock)
        sock.close()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hass.data[DOMAIN].pop(name_lower, None)
    return unloaded