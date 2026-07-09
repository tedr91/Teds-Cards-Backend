"""Ted's Cards Backend integration."""

from __future__ import annotations

import os
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started

from .const import DOMAIN
from .store import TedsManager
from .websocket import async_register as async_register_ws

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    manager = TedsManager(hass)
    await manager.async_load()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = manager
    async_register_ws(hass)
    await _register_sound_path(hass)

    async def add_alarm(call: ServiceCall):
        await manager.add_alarm(
            call.data["label"], call.data["time"], call.data.get("days"),
            call.data.get("description", ""), call.data.get("enabled", True),
            call.data.get("location"),
        )

    async def update_alarm(call: ServiceCall):
        await manager.update_alarm(call.data["id"], **{k: call.data[k] for k in ("label", "time", "days", "description", "enabled", "location") if k in call.data})

    async def remove_alarm(call: ServiceCall):
        await manager.remove_alarm(call.data["id"])

    async def start_timer(call: ServiceCall):
        await manager.start_timer(call.data["name"], call.data.get("hours", 0), call.data.get("minutes", 0), call.data.get("seconds", 0), call.data.get("location"))

    async def cancel_timer(call: ServiceCall):
        manager.cancel_timer(call.data["id"])

    async def remove_recent(call: ServiceCall):
        await manager.remove_recent(
            call.data["name"], call.data.get("hours", 0), call.data.get("minutes", 0),
            call.data.get("seconds", 0), call.data.get("location"),
        )

    async def notify(call: ServiceCall):
        await manager.notify(
            call.data["title"], call.data["message"],
            severity=call.data.get("severity", "info"), icon=call.data.get("icon"),
            area=call.data.get("area"), actions=call.data.get("actions"),
            notif_id=call.data.get("id"), timeout=call.data.get("timeout"),
            persistence=call.data.get("persistence", "normal"),
        )

    async def dismiss_notification(call: ServiceCall):
        await manager.dismiss_notification(call.data["id"])

    async def mark_read(call: ServiceCall):
        await manager.mark_read(call.data.get("id"), call.data.get("area"))

    async def clear_notifications(call: ServiceCall):
        await manager.clear_notifications(call.data.get("area"))

    async def set_setting(call: ServiceCall):
        await manager.set_settings(
            {call.data["key"]: call.data.get("value")},
            scope=call.data.get("scope", "global"),
            device_id=call.data.get("device_id"),
        )

    async def clear_setting(call: ServiceCall):
        key = call.data.get("key")
        await manager.clear_settings(
            keys=[key] if key else None,
            scope=call.data.get("scope", "global"),
            device_id=call.data.get("device_id"),
        )

    async def register_device(call: ServiceCall):
        await manager.register_device(
            call.data["device_id"], call.data.get("area"), call.data.get("name"),
            call.data.get("media_player"),
            client_width=call.data.get("client_width"),
            client_height=call.data.get("client_height"),
            client_orientation=call.data.get("client_orientation"),
            client_form_factor=call.data.get("client_form_factor"),
        )

    async def pause_timer(call: ServiceCall):
        manager.pause_timer(call.data["id"])

    async def resume_timer(call: ServiceCall):
        manager.resume_timer(call.data["id"])

    async def update_timer(call: ServiceCall):
        manager.update_timer(
            call.data["id"], name=call.data.get("name"),
            hours=call.data.get("hours"), minutes=call.data.get("minutes"), seconds=call.data.get("seconds"),
            location=call.data.get("location"), _set_location=("location" in call.data),
        )

    hass.services.async_register(DOMAIN, "add_alarm", add_alarm, schema=vol.Schema({
        vol.Required("label"): cv.string, vol.Required("time"): cv.string,
        vol.Optional("days"): [int], vol.Optional("description"): cv.string, vol.Optional("enabled"): cv.boolean,
        vol.Optional("location"): vol.Any(None, cv.string)}))
    hass.services.async_register(DOMAIN, "update_alarm", update_alarm, schema=vol.Schema({vol.Required("id"): cv.string}, extra=vol.ALLOW_EXTRA))
    hass.services.async_register(DOMAIN, "remove_alarm", remove_alarm, schema=vol.Schema({vol.Required("id"): cv.string}))
    hass.services.async_register(DOMAIN, "start_timer", start_timer, schema=vol.Schema({
        vol.Required("name"): cv.string, vol.Optional("hours"): int, vol.Optional("minutes"): int, vol.Optional("seconds"): int,
        vol.Optional("location"): vol.Any(None, cv.string)}))
    hass.services.async_register(DOMAIN, "cancel_timer", cancel_timer, schema=vol.Schema({vol.Required("id"): cv.string}))
    hass.services.async_register(DOMAIN, "remove_recent", remove_recent, schema=vol.Schema({
        vol.Required("name"): cv.string, vol.Optional("hours"): int, vol.Optional("minutes"): int, vol.Optional("seconds"): int,
        vol.Optional("location"): vol.Any(None, cv.string)}))
    hass.services.async_register(DOMAIN, "pause_timer", pause_timer, schema=vol.Schema({vol.Required("id"): cv.string}))
    hass.services.async_register(DOMAIN, "resume_timer", resume_timer, schema=vol.Schema({vol.Required("id"): cv.string}))
    hass.services.async_register(DOMAIN, "update_timer", update_timer, schema=vol.Schema({
        vol.Required("id"): cv.string, vol.Optional("name"): cv.string,
        vol.Optional("hours"): int, vol.Optional("minutes"): int, vol.Optional("seconds"): int,
        vol.Optional("location"): vol.Any(None, cv.string)}))
    hass.services.async_register(DOMAIN, "notify", notify, schema=vol.Schema({
        vol.Required("title"): cv.string, vol.Required("message"): cv.string,
        vol.Optional("severity"): cv.string, vol.Optional("icon"): cv.string,
        vol.Optional("area"): vol.Any(None, cv.string), vol.Optional("actions"): list,
        vol.Optional("id"): cv.string, vol.Optional("timeout"): vol.Any(None, int),
        vol.Optional("persistence"): vol.In(("transient", "normal", "sticky"))}))
    hass.services.async_register(DOMAIN, "dismiss_notification", dismiss_notification, schema=vol.Schema({vol.Required("id"): cv.string}))
    hass.services.async_register(DOMAIN, "mark_read", mark_read, schema=vol.Schema({
        vol.Optional("id"): cv.string, vol.Optional("area"): vol.Any(None, cv.string)}))
    hass.services.async_register(DOMAIN, "clear_notifications", clear_notifications, schema=vol.Schema({
        vol.Optional("area"): vol.Any(None, cv.string)}))
    hass.services.async_register(DOMAIN, "set_setting", set_setting, schema=vol.Schema({
        vol.Required("key"): cv.string, vol.Optional("value"): vol.Any(None, bool, int, float, cv.string),
        vol.Optional("scope"): vol.In(["global", "device"]), vol.Optional("device_id"): cv.string}))
    hass.services.async_register(DOMAIN, "clear_setting", clear_setting, schema=vol.Schema({
        vol.Optional("key"): cv.string, vol.Optional("scope"): vol.In(["global", "device"]),
        vol.Optional("device_id"): cv.string}))
    hass.services.async_register(DOMAIN, "register_device", register_device, schema=vol.Schema({
        vol.Required("device_id"): cv.string, vol.Optional("area"): vol.Any(None, cv.string),
        vol.Optional("name"): vol.Any(None, cv.string),
        vol.Optional("media_player"): vol.Any(None, cv.string),
        vol.Optional("client_width"): vol.Any(None, int),
        vol.Optional("client_height"): vol.Any(None, int),
        vol.Optional("client_orientation"): vol.Any(None, cv.string),
        vol.Optional("client_form_factor"): vol.Any(None, cv.string)}))

    async def check_requirements(call: ServiceCall):
        await manager.refresh_requirements()

    hass.services.async_register(DOMAIN, "check_requirements", check_requirements, schema=vol.Schema({}))

    # Detect optional dependencies server-side. Run once HA has fully started (so
    # all integrations + Lovelace resources are loaded), then re-check when the
    # dashboards change and periodically.
    async def _refresh_reqs(*_):
        await manager.refresh_requirements()

    entry.async_on_unload(async_at_started(hass, _refresh_reqs))
    entry.async_on_unload(hass.bus.async_listen("lovelace_updated", _refresh_reqs))
    entry.async_on_unload(async_track_time_interval(hass, _refresh_reqs, timedelta(minutes=10)))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _register_sound_path(hass: HomeAssistant) -> None:
    """Serve bundled alert sounds at /teds_cards_backend/sounds/* (once)."""
    flag = f"{DOMAIN}_sounds_registered"
    if hass.data.get(flag):
        return
    sounds_dir = os.path.join(os.path.dirname(__file__), "sounds")
    url = "/teds_cards_backend/sounds"
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(url, sounds_dir, False)]
        )
        hass.data[flag] = True
    except Exception:  # noqa: BLE001 - fall back for older HA cores
        try:
            hass.http.register_static_path(url, sounds_dir, False)
            hass.data[flag] = True
        except Exception:  # noqa: BLE001
            pass


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id).shutdown()
    return unloaded
