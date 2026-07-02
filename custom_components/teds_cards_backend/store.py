"""Persistent manager for Ted's Cards alarms and recent timers."""

from __future__ import annotations

import functools
import uuid
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    EVENT_ALARM_RINGING,
    EVENT_NOTIFICATION,
    EVENT_TIMER_FINISHED,
    NOTIFICATIONS_MAX,
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
        self.notifications: list[dict] = []  # newest-first notification list
        self._listeners: list = []
        self._update_cbs: set = set()

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.alarms = data.get("alarms", [])
        self.recent = data.get("recent", [])
        self.notifications = data.get("notifications", [])
        # Per-minute alarm check.
        self._listeners.append(async_track_time_change(self.hass, self._tick, second=0))

    async def _save(self) -> None:
        await self._store.async_save({
            "alarms": self.alarms,
            "recent": self.recent,
            "notifications": self.notifications,
        })

    def shutdown(self) -> None:
        for unsub in self._listeners:
            unsub()
        for t in self.active.values():
            if t.get("cancel"):
                t["cancel"]()

    # ── alarms ──────────────────────────────────────────────
    async def add_alarm(self, label, time, days, description="", enabled=True, location=None):
        self.alarms.append({
            "id": uuid.uuid4().hex,
            "label": label,
            "description": description,
            "time": time,
            "days": days or [0, 1, 2, 3, 4, 5, 6],
            "enabled": enabled,
            "location": location,
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
        rang = False
        for a in self.alarms:
            if a.get("enabled") and local.weekday() in a.get("days", []) and a.get("time") == hhmm:
                loc = a.get("location")
                self.hass.bus.async_fire(EVENT_ALARM_RINGING, {
                    "id": a["id"],
                    "label": a["label"],
                    "location": loc,
                    "area_name": self._area_name(loc),
                })
                self._add_notification(
                    title="Alarm",
                    message=a["label"],
                    severity="warning",
                    icon="mdi:alarm",
                    area=loc,
                    timeout=120,
                    source="alarm",
                )
                rang = True
        if rang:
            self.hass.async_create_task(self._save())
            self._notify()

    def _area_name(self, location):
        """Resolve an area_id to its friendly name (None when unknown/unset)."""
        if not location:
            return None
        area = ar.async_get(self.hass).async_get_area(location)
        return area.name if area else None

    # ── timers ──────────────────────────────────────────────
    async def start_timer(self, name, hours=0, minutes=0, seconds=0, location=None):
        secs = hours * 3600 + minutes * 60 + seconds
        tid = uuid.uuid4().hex
        ends = dt_util.utcnow() + timedelta(seconds=secs)
        cancel = async_call_later(self.hass, secs, functools.partial(self._on_elapsed, tid))
        self.active[tid] = {
            "id": tid, "name": name, "ends": ends.isoformat(),
            "duration": secs, "remaining": secs, "paused": False, "cancel": cancel,
            "location": location,
        }
        self.recent = [{"name": name, "h": hours, "m": minutes, "s": seconds, "location": location}] + [
            r for r in self.recent
            if not (r["h"] == hours and r["m"] == minutes and r["s"] == seconds
                    and r["name"] == name and r.get("location") == location)
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
        t["cancel"] = async_call_later(self.hass, secs, functools.partial(self._on_elapsed, tid))
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
                t["cancel"] = async_call_later(self.hass, secs, functools.partial(self._on_elapsed, tid))
        self._notify()

    def cancel_timer(self, tid):
        t = self.active.pop(tid, None)
        if t and t.get("cancel"):
            t["cancel"]()
        self._notify()

    async def remove_recent(self, name, hours=0, minutes=0, seconds=0, location=None):
        """Drop a preset from the Recent timers list."""
        self.recent = [
            r for r in self.recent
            if not (r["name"] == name and r["h"] == hours and r["m"] == minutes
                    and r["s"] == seconds and r.get("location") == location)
        ]
        await self._save()
        self._notify()

    @callback
    def _on_elapsed(self, tid, _now=None):
        """Timer duration elapsed — runs in the event loop (via HassJob callback)."""
        self._finish(tid)

    @callback
    def _finish(self, tid):
        t = self.active.pop(tid, None)
        if t:
            loc = t.get("location")
            self.hass.bus.async_fire(EVENT_TIMER_FINISHED, {
                "id": tid,
                "name": t["name"],
                "duration": t.get("duration", 0),
                "location": loc,
                "area_name": self._area_name(loc),
            })
            self._add_notification(
                title="Timer complete",
                message=f"{t['name']} ({self._fmt_duration(t.get('duration', 0))} timer)",
                severity="info",
                icon="mdi:timer-check-outline",
                area=loc,
                timeout=60,
                source="timer",
            )
            self.hass.async_create_task(self._save())
        self._notify()

    @staticmethod
    def _fmt_duration(sec) -> str:
        """Seconds → "1 hr, 30 min" using only the relevant parts."""
        sec = int(sec or 0)
        h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
        parts = []
        if h:
            parts.append(f"{h} hr")
        if m:
            parts.append(f"{m} min")
        if s:
            parts.append(f"{s} sec")
        return ", ".join(parts) or "0 sec"

    # ── notifications ───────────────────────────────────────
    def _add_notification(self, *, title, message, severity="info", icon=None,
                          area=None, actions=None, notif_id=None, timeout=None,
                          sticky=False, source="service"):
        """Create/replace a notification, persist, fire the event, and refresh sensors."""
        nid = notif_id or uuid.uuid4().hex
        item = {
            "id": nid,
            "title": title,
            "message": message,
            "severity": severity,
            "icon": icon,
            "area": area,
            "area_name": self._area_name(area),
            "created": dt_util.utcnow().isoformat(),
            "read": False,
            "sticky": bool(sticky),
            "timeout": timeout,
            "actions": actions or [],
            "source": source,
        }
        # Upsert by id (newest first), then cap.
        self.notifications = [n for n in self.notifications if n["id"] != nid]
        self.notifications.insert(0, item)
        del self.notifications[NOTIFICATIONS_MAX:]
        self.hass.bus.async_fire(EVENT_NOTIFICATION, item)
        return item

    async def notify(self, title, message, severity="info", icon=None, area=None,
                     actions=None, notif_id=None, timeout=None, sticky=False, source="service"):
        self._add_notification(
            title=title, message=message, severity=severity, icon=icon, area=area,
            actions=actions, notif_id=notif_id, timeout=timeout, sticky=sticky, source=source,
        )
        await self._save()
        self._notify()

    async def dismiss_notification(self, notif_id):
        self.notifications = [n for n in self.notifications if n["id"] != notif_id]
        await self._save()
        self._notify()

    async def mark_read(self, notif_id=None, area=None):
        for n in self.notifications:
            if notif_id is not None and n["id"] != notif_id:
                continue
            if area is not None and n.get("area") != area:
                continue
            n["read"] = True
        await self._save()
        self._notify()

    async def clear_notifications(self, area=None):
        if area is None:
            self.notifications = []
        else:
            self.notifications = [n for n in self.notifications if n.get("area") != area]
        await self._save()
        self._notify()

    # ── notify sensors ──────────────────────────────────────
    def register(self, cb):
        self._update_cbs.add(cb)
        return lambda: self._update_cbs.discard(cb)

    def _notify(self):
        for cb in list(self._update_cbs):
            cb()
