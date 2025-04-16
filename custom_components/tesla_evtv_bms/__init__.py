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
        "values": {}
    }

    async def udp_listener():
        _LOGGER.info("Starting UDP listener for %s on port %d", name, port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", port))
        sock.setblocking(False)
        loop = asyncio.get_event_loop()

        while True:
            data, _ = await loop.run_in_executor(None, sock.recvfrom, 1024)
            parsed = parse_udp_packet(data, port)
            if parsed:
                async_dispatcher_send(hass, SIGNAL_UPDATE_ENTITY.format(name_lower), parsed)

    hass.loop.create_task(udp_listener())

    for platform in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, platform)
        )

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True
