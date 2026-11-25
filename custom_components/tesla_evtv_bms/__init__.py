import asyncio
import logging
import socket
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    PLATFORMS,
    SIGNAL_UPDATE_ENTITY,
    SIGNAL_OPTIONS_UPDATED,
    SIGNAL_CONTACTOR_UPDATE,
    CONF_INVERT_CURRENT,
    CONF_HOST,
    CONF_TELNET_PORT,
    DEFAULT_INVERT_CURRENT,
    DEFAULT_TELNET_PORT,
    TELNET_POLL_SECONDS,
    TELNET_LISTEN_SECONDS,
    TELNET_CONNECT_TIMEOUT,
    default_controller_host,
)
from .parser import parse_udp_packet
from .telnet_status import parse_status_screen, read_telnet_screen

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


def _controller_host(entry: ConfigEntry) -> str | None:
    host = entry.options.get(CONF_HOST, entry.data.get(CONF_HOST))
    if isinstance(host, str):
        host = host.strip()
    if host:
        return host
    return default_controller_host(entry.data.get("name", ""))


def _telnet_port(entry: ConfigEntry) -> int:
    try:
        port = int(entry.options.get(
            CONF_TELNET_PORT, entry.data.get(CONF_TELNET_PORT, DEFAULT_TELNET_PORT)
        ))
    except (TypeError, ValueError):
        port = DEFAULT_TELNET_PORT
    return port or DEFAULT_TELNET_PORT


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
        "socket": None,
        "title": name,
        "name_lower": name_lower,
        "entry": entry,
        "contactor": {},
        "telnet_lock": asyncio.Lock(),
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
    async_register_contactor_services(hass)
    # Options change: update invert_current in place (no entry reload/socket rebind).
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Read-only telnet contactor poll (connect, recv, disconnect; never send).
    async def _poll_contactor(_now=None):
        pack = hass.data.get(DOMAIN, {}).get(name_lower)
        if pack is None:
            return
        host = _controller_host(entry)
        tport = _telnet_port(entry)
        attempt = dt_util.now()
        if not host:
            pack["contactor"] = {
                "available": False,
                "status_available": True,
                "error": "no_host",
                "screen": None,
                "last_attempt": attempt,
                "last_read": (pack.get("contactor") or {}).get("last_read"),
            }
            async_dispatcher_send(hass, SIGNAL_CONTACTOR_UPDATE.format(name_lower))
            return
        lock = pack.get("telnet_lock")
        if lock is None:
            lock = asyncio.Lock()
            pack["telnet_lock"] = lock
        if lock.locked():
            _LOGGER.debug("%s: skipping telnet poll; previous still running", name)
            return
        async with lock:
            try:
                raw = await hass.async_add_executor_job(
                    read_telnet_screen, host, tport, TELNET_LISTEN_SECONDS, TELNET_CONNECT_TIMEOUT
                )
            except Exception as err:
                # Connect failed (PuTTY may be using the only session) or I/O error.
                _LOGGER.info("%s: telnet read failed for %s:%s (%s); will retry", name, host, tport, err)
                prev = pack.get("contactor") or {}
                pack["contactor"] = {
                    "available": False,
                    "status_available": True,
                    "error": "connect_failed",
                    "screen": None,
                    "host": host,
                    "last_attempt": attempt,
                    "last_read": prev.get("last_read"),
                }
                async_dispatcher_send(hass, SIGNAL_CONTACTOR_UPDATE.format(name_lower))
                return

            parsed = parse_status_screen(raw)
            screen = parsed.get("screen")
            # last_read is set whenever we received a recognizable screen (main or menu)
            # or a connected-but-empty session (no_screen). Connect failures keep the
            # previous last_read and mark binary sensors unavailable.
            last_read = attempt if screen in ("main", "menu", "no_screen") else None
            state = {
                "host": host,
                "screen": screen,
                "last_attempt": attempt,
                "last_read": last_read,
                "error": None,
                "status_available": True,
                "available": screen == "main" and any(
                    k in parsed for k in (
                        "negative_commanded", "negative_reported",
                        "positive_commanded", "positive_reported", "welded",
                    )
                ),
            }
            if screen == "main":
                for k in (
                    "negative_commanded", "negative_reported",
                    "positive_commanded", "positive_reported", "welded",
                    "internal_status", "internal_allowed", "internal_state",
                ):
                    if k in parsed:
                        state[k] = parsed[k]
            elif screen == "menu":
                state["internal_status"] = "menu"
                state["error"] = None
            elif screen == "no_screen":
                state["internal_status"] = "no_screen"
                state["available"] = False
            pack["contactor"] = state
            async_dispatcher_send(hass, SIGNAL_CONTACTOR_UPDATE.format(name_lower))

    # First poll shortly after startup; then every TELNET_POLL_SECONDS.
    async def _first_poll(_now=None):
        await _poll_contactor()

    entry.async_on_unload(
        async_track_time_interval(
            hass, _poll_contactor, timedelta(seconds=TELNET_POLL_SECONDS)
        )
    )
    hass.async_create_task(_first_poll())
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


def async_register_contactor_services(hass: HomeAssistant) -> None:
    """Register open/close-both services once."""
    if hass.services.has_service(DOMAIN, "open_contactors"):
        return

    async def _handle(call, target_on: bool):
        from homeassistant.exceptions import HomeAssistantError
        from .contactor_ops import async_set_both
        from .contactor_control import pack_allows_switches as _allows
        pack = (call.data or {}).get("pack") or (call.data or {}).get("name") or ""
        name_lower = "".join(ch for ch in str(pack).lower() if ch.isalnum())
        if not _allows(name_lower):
            raise HomeAssistantError("pack must be powerpack1 or powerpack2")
        data = (hass.data.get(DOMAIN, {}) or {}).get(name_lower)
        if not data or not data.get("entry"):
            raise HomeAssistantError(f"{name_lower} is not loaded")
        try:
            return await async_set_both(
                hass, data["entry"], name_lower, target_on, call.context
            )
        except Exception as err:
            raise HomeAssistantError(str(err)) from err

    async def _open(call):
        await _handle(call, False)

    async def _close(call):
        await _handle(call, True)

    hass.services.async_register(DOMAIN, "open_contactors", _open)
    hass.services.async_register(DOMAIN, "close_contactors", _close)
