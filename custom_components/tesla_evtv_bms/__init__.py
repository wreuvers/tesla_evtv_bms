import asyncio
import socket
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN, PLATFORMS
from .parser import parse_udp_packet

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    name = entry.data["name"]
    port = entry.data["port"]

    async def udp_listener():
        _LOGGER.info("Starting UDP listener for %s on port %d", name, port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", port))
        sock.setblocking(False)
        loop = asyncio.get_event_loop()

        while True:
            data, addr = await loop.run_in_executor(None, sock.recvfrom, 1024)
            parsed = parse_udp_packet(data, port)
            if parsed:
                for key, value in parsed.items():
                    hass.states.async_set(f"sensor.{name.lower()}_{key}", value)

    hass.loop.create_task(udp_listener())

    for platform in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, platform)
        )
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True
