"""Ted's Cards Backend integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN
from .store import TedsManager

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    manager = TedsManager(hass)
    await manager.async_load()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = manager

    async def add_alarm(call: ServiceCall):
        await manager.add_alarm(
            call.data["label"], call.data["time"], call.data.get("days"),
            call.data.get("description", ""), call.data.get("enabled", True),
            call.data.get("location"),
        )

    async def update_alarm(call: ServiceCall):
        await manager.update_alarm(call.data["id"], **{k: call.data.get(k) for k in ("label", "time", "days", "description", "enabled", "location")})

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

    async def pause_timer(call: ServiceCall):
        manager.pause_timer(call.data["id"])

    async def resume_timer(call: ServiceCall):
        manager.resume_timer(call.data["id"])

    async def update_timer(call: ServiceCall):
        manager.update_timer(
            call.data["id"], name=call.data.get("name"),
            hours=call.data.get("hours"), minutes=call.data.get("minutes"), seconds=call.data.get("seconds"),
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
        vol.Optional("hours"): int, vol.Optional("minutes"): int, vol.Optional("seconds"): int}))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id).shutdown()
    return unloaded
