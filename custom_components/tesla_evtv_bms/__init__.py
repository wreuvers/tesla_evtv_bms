import asyncio
import socket
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, PLATFORMS, SIGNAL_UPDATE_ENTITY
from .parser import parse_udp_packet

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    name = entry.data["name"]
    port = entry.data["port"]
    name_lower = name.lower()

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
        }
    }

    def udp_callback(sock):
        try:
            data, _ = sock.recvfrom(1024)
            parsed = parse_udp_packet(data, port)
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
            pass
        except Exception as e:
            _LOGGER.error(f"[{DOMAIN}] UDP read error on {name}: {e}")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", port))
        sock.setblocking(False)
        loop = asyncio.get_event_loop()
        loop.add_reader(sock, udp_callback, sock)
        _LOGGER.info("Started non-blocking UDP listener for %s on port %d", name, port)
    except OSError as e:
        _LOGGER.error("Failed to bind UDP socket on port %d for %s: %s", port, name, e)
        return False

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True
