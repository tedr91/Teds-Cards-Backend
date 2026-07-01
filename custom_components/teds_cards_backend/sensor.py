"""Sensors exposing alarms and timers for the cards to read."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    manager = hass.data[DOMAIN][entry.entry_id]
    add([TedsAlarmsSensor(manager), TedsTimersSensor(manager)])


class _Base(SensorEntity):
    _attr_should_poll = False

    def __init__(self, manager) -> None:
        self._m = manager

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._m.register(self.async_write_ha_state))


class TedsAlarmsSensor(_Base):
    _attr_name = "Teds Alarms"
    _attr_unique_id = "teds_alarms"
    _attr_icon = "mdi:alarm"

    @property
    def native_value(self):
        return len([a for a in self._m.alarms if a.get("enabled")])

    @property
    def extra_state_attributes(self):
        return {"alarms": self._m.alarms}


class TedsTimersSensor(_Base):
    _attr_name = "Teds Timers"
    _attr_unique_id = "teds_timers"
    _attr_icon = "mdi:timer"

    @property
    def native_value(self):
        return len(self._m.active)

    @property
    def extra_state_attributes(self):
        return {
            "active": [
                {
                    "id": t["id"], "name": t["name"], "ends": t["ends"],
                    "duration": t.get("duration", 0), "remaining": t.get("remaining", 0),
                    "paused": t.get("paused", False),
                }
                for t in self._m.active.values()
            ],
            "recent": self._m.recent,
        }
