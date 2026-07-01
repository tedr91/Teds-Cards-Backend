"""Persistent manager for Ted's Cards alarms and recent timers."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    EVENT_ALARM_RINGING,
    EVENT_TIMER_FINISHED,
    RECENT_TIMERS_MAX,
    STORAGE_KEY,
    STORAGE_VERSION,
)


class TedsManager:
    """Owns alarms + active/recent timers, persists them, and fires them."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.alarms: list[dict] = []
        self.recent: list[dict] = []  # last N timer presets (h/m/s + name)
        self.active: dict[str, dict] = {}  # id -> {name, ends, cancel}
        self._listeners: list = []
        self._update_cbs: set = set()

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.alarms = data.get("alarms", [])
        self.recent = data.get("recent", [])
        # Per-minute alarm check.
        self._listeners.append(async_track_time_change(self.hass, self._tick, second=0))

    async def _save(self) -> None:
        await self._store.async_save({"alarms": self.alarms, "recent": self.recent})

    def shutdown(self) -> None:
        for unsub in self._listeners:
            unsub()
        for t in self.active.values():
            if t.get("cancel"):
                t["cancel"]()

    # ── alarms ──────────────────────────────────────────────
    async def add_alarm(self, label, time, days, description="", enabled=True):
        self.alarms.append({
            "id": uuid.uuid4().hex,
            "label": label,
            "description": description,
            "time": time,
            "days": days or [0, 1, 2, 3, 4, 5, 6],
            "enabled": enabled,
        })
        await self._save()
        self._notify()

    async def update_alarm(self, alarm_id, **changes):
        for a in self.alarms:
            if a["id"] == alarm_id:
                a.update({k: v for k, v in changes.items() if v is not None})
                break
        await self._save()
        self._notify()

    async def remove_alarm(self, alarm_id):
        self.alarms = [a for a in self.alarms if a["id"] != alarm_id]
        await self._save()
        self._notify()

    @callback
    def _tick(self, now: datetime) -> None:
        local = dt_util.as_local(now)
        hhmm = local.strftime("%H:%M")
        for a in self.alarms:
            if a.get("enabled") and local.weekday() in a.get("days", []) and a.get("time") == hhmm:
                self.hass.bus.async_fire(EVENT_ALARM_RINGING, {"id": a["id"], "label": a["label"]})

    # ── timers ──────────────────────────────────────────────
    async def start_timer(self, name, hours=0, minutes=0, seconds=0):
        secs = hours * 3600 + minutes * 60 + seconds
        tid = uuid.uuid4().hex
        ends = dt_util.utcnow() + timedelta(seconds=secs)
        cancel = async_call_later(self.hass, secs, lambda *_: self._finish(tid))
        self.active[tid] = {
            "id": tid, "name": name, "ends": ends.isoformat(),
            "duration": secs, "remaining": secs, "paused": False, "cancel": cancel,
        }
        self.recent = [{"name": name, "h": hours, "m": minutes, "s": seconds}] + [
            r for r in self.recent if not (r["h"] == hours and r["m"] == minutes and r["s"] == seconds and r["name"] == name)
        ][: RECENT_TIMERS_MAX - 1]
        await self._save()
        self._notify()

    def pause_timer(self, tid):
        t = self.active.get(tid)
        if not t or t.get("paused"):
            return
        if t.get("cancel"):
            t["cancel"]()
            t["cancel"] = None
        ends = dt_util.parse_datetime(t["ends"])
        remaining = (ends - dt_util.utcnow()).total_seconds() if ends else t.get("remaining", 0)
        t["remaining"] = max(0, int(round(remaining)))
        t["paused"] = True
        self._notify()

    def resume_timer(self, tid):
        t = self.active.get(tid)
        if not t or not t.get("paused"):
            return
        secs = max(0, int(t.get("remaining", 0)))
        t["ends"] = (dt_util.utcnow() + timedelta(seconds=secs)).isoformat()
        t["paused"] = False
        t["cancel"] = async_call_later(self.hass, secs, lambda *_: self._finish(tid))
        self._notify()

    def update_timer(self, tid, name=None, hours=None, minutes=None, seconds=None):
        t = self.active.get(tid)
        if not t:
            return
        if name is not None:
            t["name"] = name
        if hours is not None or minutes is not None or seconds is not None:
            secs = (hours or 0) * 3600 + (minutes or 0) * 60 + (seconds or 0)
            t["duration"] = secs
            t["remaining"] = secs
            if t.get("cancel"):
                t["cancel"]()
                t["cancel"] = None
            t["ends"] = (dt_util.utcnow() + timedelta(seconds=secs)).isoformat()
            if not t.get("paused"):
                t["cancel"] = async_call_later(self.hass, secs, lambda *_: self._finish(tid))
        self._notify()

    def cancel_timer(self, tid):
        t = self.active.pop(tid, None)
        if t and t.get("cancel"):
            t["cancel"]()
        self._notify()

    def _finish(self, tid):
        t = self.active.pop(tid, None)
        if t:
            self.hass.bus.async_fire(EVENT_TIMER_FINISHED, {"id": tid, "name": t["name"]})
        self._notify()

    # ── notify sensors ──────────────────────────────────────
    def register(self, cb):
        self._update_cbs.add(cb)
        return lambda: self._update_cbs.discard(cb)

    def _notify(self):
        for cb in list(self._update_cbs):
            cb()
