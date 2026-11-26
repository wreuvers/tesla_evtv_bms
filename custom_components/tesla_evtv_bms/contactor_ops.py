"""Async open/close for EVTV contactors. Sends only key 1 or 2, and only after guards."""
from __future__ import annotations

import asyncio
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    TELNET_HOLD_SECONDS,
    DOMAIN,
    CONF_HOST,
    CONF_TELNET_PORT,
    CONF_OPEN_CURRENT_MAX,
    DEFAULT_TELNET_PORT,
    DEFAULT_OPEN_CURRENT_MAX,
    TELNET_LISTEN_SECONDS,
    TELNET_CONNECT_TIMEOUT,
    CURRENT_SENSOR,
    OTHER_PACK,
    SIGNAL_CONTACTOR_UPDATE,
    default_controller_host,
)
from .contactor_control import (
    TOGGLE_KEY,
    already_at_target,
    close_allowed,
    open_allowed,
    pack_allows_switches,
    precharge_wait_s,
    verify_match,
)
from .telnet_status import SCREEN_EXTRA_KEYS, apply_contactor_toggle, parse_status_screen, read_telnet_screen

_LOGGER = logging.getLogger(__name__)


def _is_user_context(context: Context | None) -> bool:
    # HA sets user_id for dashboard / UI / logged-in service calls; automations
    # and most scripts leave it unset.
    return bool(context and getattr(context, "user_id", None))


def _snapshot_states(hass: HomeAssistant) -> dict:
    out = {}
    for eid in hass.states.async_entity_ids():
        st = hass.states.get(eid)
        if st is not None:
            out[eid] = st.state
    return out


def _host_port(entry: ConfigEntry) -> tuple[str, int]:
    host = entry.options.get(CONF_HOST, entry.data.get(CONF_HOST))
    if isinstance(host, str):
        host = host.strip()
    if not host:
        host = default_controller_host(entry.data.get("name", "")) or ""
    try:
        port = int(entry.options.get(
            CONF_TELNET_PORT, entry.data.get(CONF_TELNET_PORT, DEFAULT_TELNET_PORT)
        ))
    except (TypeError, ValueError):
        port = DEFAULT_TELNET_PORT
    return host, port or DEFAULT_TELNET_PORT


def _open_current_max(entry: ConfigEntry) -> float:
    try:
        return float(entry.options.get(
            CONF_OPEN_CURRENT_MAX,
            entry.data.get(CONF_OPEN_CURRENT_MAX, DEFAULT_OPEN_CURRENT_MAX),
        ))
    except (TypeError, ValueError):
        return DEFAULT_OPEN_CURRENT_MAX


def _current_amps(hass: HomeAssistant, name_lower: str):
    eid = CURRENT_SENSOR.get(name_lower)
    if not eid:
        return None
    st = hass.states.get(eid)
    if st is None or st.state in ("unknown", "unavailable", "none", ""):
        return None
    try:
        return float(st.state)
    except (TypeError, ValueError):
        return None


def _store_contactor(pack: dict, parsed: dict, host: str, error=None, attempt=None, allow_hold: bool = True) -> None:
    attempt = attempt or dt_util.now()
    screen = parsed.get("screen")
    state = {
        "host": host,
        "screen": screen,
        "last_attempt": attempt,
        "last_read": attempt if screen in ("main", "menu", "no_screen") else (pack.get("contactor") or {}).get("last_read"),
        "error": error,
        "status_available": True,
        "last_main": (pack.get("contactor") or {}).get("last_main"),
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
        ) + SCREEN_EXTRA_KEYS:
            if k in parsed:
                state[k] = parsed[k]
        # v1.3.10: a main screen captured mid-redraw can miss single contactor
        # lines. Carry the previous value of a missing pole for up to
        # TELNET_HOLD_SECONDS instead of flapping the entity to unavailable.
        prev = pack.get("contactor") or {}
        prev_main = prev.get("last_main")
        held = []
        if allow_hold and prev_main is not None and (attempt - prev_main).total_seconds() <= TELNET_HOLD_SECONDS:
            for k in ("negative_commanded", "negative_reported",
                      "positive_commanded", "positive_reported", "welded"):
                if k not in parsed and prev.get(k) is not None:
                    state[k] = prev[k]
                    held.append(k)
        state["fields_held"] = held
        if held:
            # keep the older main timestamp for held fields' freshness
            state["fields_held_since"] = prev.get("fields_held_since") or prev_main
        state["available"] = state["available"] or bool(held)
        state["last_main"] = attempt
        if parsed.get("negative_commanded") is True and parsed.get("negative_reported") is True:
            # Keep an existing close timestamp; set one if we didn't have it.
            state["negative_closed_mono"] = (pack.get("contactor") or {}).get("negative_closed_mono") or time.monotonic()
    elif screen == "menu":
        state["internal_status"] = "menu"
        state["available"] = False
    elif screen == "no_screen":
        state["internal_status"] = "no_screen"
        state["available"] = False
    pack["contactor"] = state


async def _locked_read(hass: HomeAssistant, pack: dict, host: str, port: int) -> dict:
    raw = await hass.async_add_executor_job(
        read_telnet_screen, host, port, TELNET_LISTEN_SECONDS, TELNET_CONNECT_TIMEOUT
    )
    parsed = parse_status_screen(raw)
    _store_contactor(pack, parsed, host)
    async_dispatcher_send(hass, SIGNAL_CONTACTOR_UPDATE.format(pack.get("name_lower") or ""), )
    return parsed


async def async_set_contactor(
    hass: HomeAssistant,
    entry: ConfigEntry,
    name_lower: str,
    pole: str,
    target_on: bool,
    context: Context | None,
) -> dict:
    """Set one pole. Raises RuntimeError with a user-visible reason on abort."""
    if not pack_allows_switches(name_lower):
        raise RuntimeError(f"{name_lower} has no EVTV contactor switches")
    pack = (hass.data.get(DOMAIN, {}) or {}).get(name_lower)
    if pack is None:
        raise RuntimeError(f"{name_lower} is not loaded")
    host, port = _host_port(entry)
    if not host:
        raise RuntimeError("no telnet host configured")
    pack["name_lower"] = name_lower
    lock = pack.setdefault("telnet_lock", asyncio.Lock())
    async with lock:
        try:
            parsed = await hass.async_add_executor_job(
                read_telnet_screen, host, port, TELNET_LISTEN_SECONDS, TELNET_CONNECT_TIMEOUT
            )
        except Exception as err:
            _store_contactor(pack, {"screen": "no_screen"}, host, error="connect_failed")
            async_dispatcher_send(hass, SIGNAL_CONTACTOR_UPDATE.format(name_lower))
            raise RuntimeError(f"telnet connect failed for {host}: {err}") from err
        before = parse_status_screen(parsed)
        _store_contactor(pack, before, host, allow_hold=False)
        async_dispatcher_send(hass, SIGNAL_CONTACTOR_UPDATE.format(name_lower))

        if already_at_target(before if before.get("screen") == "main" else pack.get("contactor") or {}, pole, target_on) and before.get("screen") == "main":
            return {"changed": False, "before": before, "after": before}

        states = _snapshot_states(hass)
        other = OTHER_PACK.get(name_lower)
        other_c = None
        if other:
            other_c = ((hass.data.get(DOMAIN, {}) or {}).get(other) or {}).get("contactor")

        if target_on:
            ok, reason = close_allowed(
                name_lower=name_lower,
                pole=pole,
                user_initiated=_is_user_context(context),
                states=states,
                this_contactor=pack.get("contactor") or before,
                other_contactor=other_c,
            )
        else:
            ok, reason = open_allowed(
                name_lower=name_lower,
                states=states,
                this_contactor=pack.get("contactor") or before,
                current_amps=_current_amps(hass, name_lower),
                open_current_max=_open_current_max(entry),
            )
        if not ok:
            if "welded" in reason.lower():
                hass.async_create_task(_notify_welded(hass, name_lower, pole, reason))
            raise RuntimeError(reason)

        if target_on and pole == "positive":
            wait = precharge_wait_s((pack.get("contactor") or {}).get("negative_closed_mono"), time.monotonic())
            if wait > 0:
                _LOGGER.info("%s: waiting %.1fs precharge before closing positive", name_lower, wait)
                await asyncio.sleep(wait)

        key = TOGGLE_KEY[pole]
        def _run():
            return apply_contactor_toggle(
                host,
                port,
                key,
                listen_s=TELNET_LISTEN_SECONDS,
                connect_timeout_s=TELNET_CONNECT_TIMEOUT,
                verify_s=TELNET_LISTEN_SECONDS,
            )
        try:
            result = await hass.async_add_executor_job(_run)
        except Exception as err:
            raise RuntimeError(f"telnet toggle failed: {err}") from err

        after = result.get("after") or {}
        _store_contactor(pack, after if after.get("screen") == "main" else (result.get("before") or after), host, error=result.get("error"), allow_hold=False)
        if result.get("sent") and target_on and pole == "negative" and after.get("negative_commanded") is True:
            pack["contactor"]["negative_closed_mono"] = time.monotonic()
        async_dispatcher_send(hass, SIGNAL_CONTACTOR_UPDATE.format(name_lower))

        if not result.get("sent"):
            raise RuntimeError(result.get("error") or "no key was sent")
        ok, reason = verify_match(pack.get("contactor") or after, pole, target_on)
        if not ok:
            if "weld" in reason.lower():
                hass.async_create_task(_notify_welded(hass, name_lower, pole, reason))
            raise RuntimeError("toggle sent but verify failed: " + reason)
        return {"changed": True, "before": result.get("before"), "after": after, "key": result.get("key")}


async def async_set_both(
    hass: HomeAssistant,
    entry: ConfigEntry,
    name_lower: str,
    target_on: bool,
    context: Context | None,
) -> dict:
    """Open both (positive then negative) or close both (negative, precharge, positive)."""
    results = []
    if target_on:
        results.append(await async_set_contactor(hass, entry, name_lower, "negative", True, context))
        results.append(await async_set_contactor(hass, entry, name_lower, "positive", True, context))
    else:
        results.append(await async_set_contactor(hass, entry, name_lower, "positive", False, context))
        results.append(await async_set_contactor(hass, entry, name_lower, "negative", False, context))
    return {"steps": results}


async def _notify_welded(hass: HomeAssistant, name_lower: str, pole: str, reason: str) -> None:
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "title": f"{name_lower} possible welded contactor",
            "notification_id": f"{name_lower}_{pole}_welded",
            "message": reason,
        },
        blocking=False,
    )
