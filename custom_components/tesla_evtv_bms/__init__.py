import asyncio
import logging
import socket
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval
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
    TELNET_RETRY_LISTEN_SECONDS,
    TELNET_HOLD_SECONDS,
    TELNET_POLL_OFFSETS,
    REBOOT_RUNTIME_SLACK_S,
    REBOOT_BOOT_JUMP_S,
    EVENT_EVTV_REBOOT,
    default_controller_host,
)
from .parser import parse_udp_packet
from .telnet_status import SCREEN_EXTRA_KEYS, parse_status_screen, read_telnet_screen

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
            prev = pack.get("contactor") or {}
            try:
                raw = await hass.async_add_executor_job(
                    read_telnet_screen, host, tport, TELNET_LISTEN_SECONDS, TELNET_CONNECT_TIMEOUT
                )
                parsed = parse_status_screen(raw)
                if parsed.get("screen") == "no_screen":
                    # v1.3.9: the v3.17 screen redraws ~every 2.5 s and a short
                    # listen can miss it. Retry ONCE with a longer receive-only
                    # listen. Still never sends a byte.
                    raw = await hass.async_add_executor_job(
                        read_telnet_screen, host, tport, TELNET_RETRY_LISTEN_SECONDS, TELNET_CONNECT_TIMEOUT
                    )
                    parsed = parse_status_screen(raw)
            except Exception as err:
                # Connect failed (PuTTY may be using the only session) or I/O error.
                _LOGGER.info("%s: telnet read failed for %s:%s (%s); will retry", name, host, tport, err)
                pack["contactor"] = {
                    "available": False,
                    "status_available": True,
                    "error": "connect_failed",
                    "screen": None,
                    "host": host,
                    "last_attempt": attempt,
                    "last_read": prev.get("last_read"),
                    "last_main": prev.get("last_main"),
                }
                async_dispatcher_send(hass, SIGNAL_CONTACTOR_UPDATE.format(name_lower))
                return

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
                "last_main": prev.get("last_main"),
                "held": False,
            }
            if screen == "main":
                for k in (
                    "negative_commanded", "negative_reported",
                    "positive_commanded", "positive_reported", "welded",
                    "internal_status", "internal_allowed", "internal_state",
                ) + SCREEN_EXTRA_KEYS:
                    if k in parsed:
                        state[k] = parsed[k]
                # v1.3.10: a main screen caught mid-redraw can miss single
                # contactor lines; carry the previous pole value for up to
                # TELNET_HOLD_SECONDS instead of flapping to unavailable.
                held = []
                prev_main = prev.get("last_main")
                if prev_main is not None and (attempt - prev_main).total_seconds() <= TELNET_HOLD_SECONDS:
                    for k in ("negative_commanded", "negative_reported",
                              "positive_commanded", "positive_reported", "welded"):
                        if k not in parsed and prev.get(k) is not None:
                            state[k] = prev[k]
                            held.append(k)
                state["fields_held"] = held
                if held:
                    state["available"] = True
                state["last_main"] = attempt
                _track_runtime(hass, pack, name_lower, parsed.get("runtime_s"), attempt)
            elif screen == "menu":
                state["internal_status"] = "menu"
                state["error"] = None
            elif screen == "no_screen":
                last_main = prev.get("last_main")
                if (
                    last_main is not None
                    and (attempt - last_main).total_seconds() <= TELNET_HOLD_SECONDS
                    and (prev.get("screen") == "main" or prev.get("held"))
                ):
                    # v1.3.9: hold the last good main-screen values for a few
                    # minutes instead of flapping to unavailable.
                    for k, v in prev.items():
                        if k not in ("last_attempt", "last_read", "error", "screen"):
                            state[k] = v
                    state["screen"] = "main"
                    state["held"] = True
                    state["held_reason"] = "no_screen"
                    state["available"] = bool(prev.get("available"))
                else:
                    state["internal_status"] = "no_screen"
                    state["available"] = False
            pack["contactor"] = state
            async_dispatcher_send(hass, SIGNAL_CONTACTOR_UPDATE.format(name_lower))

    # First poll shortly after startup; then every TELNET_POLL_SECONDS.
    # v1.3.9: stagger the per-pack polls so PP0/PP1/PP2 do not all connect in
    # the same second.
    offset = TELNET_POLL_OFFSETS.get(name_lower, 0)
    unsub_holder: dict = {}

    async def _start_polling(_now=None):
        unsub_holder.pop("later", None)
        await _poll_contactor()
        unsub_holder["interval"] = async_track_time_interval(
            hass, _poll_contactor, timedelta(seconds=TELNET_POLL_SECONDS)
        )

    def _cancel_polling():
        for key in ("later", "interval"):
            unsub = unsub_holder.pop(key, None)
            if unsub:
                unsub()

    entry.async_on_unload(_cancel_polling)
    unsub_holder["later"] = async_call_later(hass, offset, _start_polling)
    return True


def _track_runtime(hass: HomeAssistant, pack: dict, name_lower: str, runtime_s, now) -> None:
    """Detect an EVTV controller reboot (Runtime went down). Read-only."""
    if not isinstance(runtime_s, int):
        return
    boot = now - timedelta(seconds=runtime_s)
    prev_rt = pack.get("runtime_last")
    prev_boot = pack.get("boot_time")
    pack["runtime_last"] = runtime_s
    rebooted = False
    if prev_rt is not None and runtime_s + REBOOT_RUNTIME_SLACK_S < prev_rt:
        rebooted = True
    elif prev_boot is not None and (boot - prev_boot).total_seconds() > REBOOT_BOOT_JUMP_S:
        # Runtime restarted between polls (e.g. a reboot shorter than the gap).
        rebooted = True
    # Keep a stable boot time between polls (read timing jitters a few seconds).
    if prev_boot is None or rebooted:
        pack["boot_time"] = boot
    if rebooted:
        pack["last_reboot"] = boot
        _LOGGER.warning(
            "%s: EVTV controller reboot detected (runtime %ss, previous %ss, boot ~%s)",
            name_lower, runtime_s, prev_rt, boot.isoformat(),
        )
        hass.bus.async_fire(
            EVENT_EVTV_REBOOT,
            {
                "pack": name_lower,
                "runtime_s": runtime_s,
                "previous_runtime_s": prev_rt,
                "boot_time": boot.isoformat(),
            },
        )


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
    from .const import CONF_MIN_CELL_VOLTS, CONF_CUTOFF_SOC_OVERRIDE, DEFAULT_MIN_CELL_VOLTS
    min_cell = entry.options.get(
        CONF_MIN_CELL_VOLTS, entry.data.get(CONF_MIN_CELL_VOLTS, DEFAULT_MIN_CELL_VOLTS)
    )
    pack["config"]["min_cell_volts"] = min_cell
    raw_override = entry.options.get(CONF_CUTOFF_SOC_OVERRIDE)
    if raw_override is None or raw_override == "":
        pack["config"]["cutoff_soc_override"] = None
    else:
        try:
            pack["config"]["cutoff_soc_override"] = float(raw_override)
        except (TypeError, ValueError):
            pack["config"]["cutoff_soc_override"] = None
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
