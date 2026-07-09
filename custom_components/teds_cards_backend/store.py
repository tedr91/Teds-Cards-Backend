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

from .playback import PlaybackEngine

from .const import (
    DEVICE_PRESENCE_TTL,
    EVENT_ALARM_RINGING,
    EVENT_NOTIFICATION,
    EVENT_SETTINGS,
    EVENT_TIMER_FINISHED,
    NOTIFICATIONS_MAX,
    RECENT_TIMERS_MAX,
    SETTINGS_DEFAULTS,
    SETTINGS_KEYS,
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
        # Settings: global baseline + per-device overrides (only overridden keys stored).
        self.settings: dict = {"global": {}, "devices": {}}
        # Devices that have registered themselves (device_id -> {area, name, last_seen}).
        self.device_registry: dict[str, dict] = {}
        # Server-side dependency detection results (req_id -> ok/missing/unknown).
        self.requirements: dict[str, str] = {}
        # This integration's version (from the manifest), for status displays.
        self.version: str | None = None
        self.playback = PlaybackEngine(self)
        self._listeners: list = []
        self._update_cbs: set = set()

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.alarms = data.get("alarms", [])
        self.recent = data.get("recent", [])
        self.notifications = data.get("notifications", [])
        stored_settings = data.get("settings") or {}
        self.settings = {
            "global": dict(stored_settings.get("global") or {}),
            "devices": {k: dict(v) for k, v in (stored_settings.get("devices") or {}).items()},
        }
        self.device_registry = {k: dict(v) for k, v in (data.get("devices") or {}).items()}
        # Per-minute alarm check.
        self._listeners.append(async_track_time_change(self.hass, self._tick, second=0))

    async def _save(self) -> None:
        await self._store.async_save({
            "alarms": self.alarms,
            "recent": self.recent,
            "notifications": self.notifications,
            "settings": self.settings,
            "devices": self.device_registry,
        })

    def shutdown(self) -> None:
        for unsub in self._listeners:
            unsub()
        for t in self.active.values():
            if t.get("cancel"):
                t["cancel"]()
        self.playback.shutdown()

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
                for k, v in changes.items():
                    # `location` may be set to None to make an alarm house-wide; other
                    # fields are only overwritten when a value is actually provided.
                    if k == "location" or v is not None:
                        a[k] = v
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
                    snooze={"kind": "alarm", "name": a["label"], "area": loc},
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

    def update_timer(self, tid, name=None, hours=None, minutes=None, seconds=None, location=None, _set_location=False):
        t = self.active.get(tid)
        if not t:
            return
        if name is not None:
            t["name"] = name
        if _set_location:
            t["location"] = location
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
                snooze={"kind": "timer", "name": t["name"], "area": loc},
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
                          persistence="normal", source="service", snooze=None):
        """Create a notification, fire the event, play sound, and refresh sensors.

        `persistence` controls its lifetime:
          - "transient": shown as a toast (+ sound) but never stored in the list.
          - "normal":    stored; auto-removed when the user reads/dismisses it.
          - "sticky":    stored; marked read on interaction, kept until cleared.
        """
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
            "persistence": persistence,
            "timeout": timeout,
            # Client-resolved snooze: the device renders/acts using its own effective
            # settings (enable + minutes) — {"kind": "timer"|"alarm", "name", "area"}.
            "snooze": snooze,
            "actions": actions or [],
            "source": source,
        }
        # Transient notifications are never persisted: just deliver the toast + sound.
        if persistence != "transient":
            # Upsert by id (newest first), then cap.
            self.notifications = [n for n in self.notifications if n["id"] != nid]
            self.notifications.insert(0, item)
            del self.notifications[NOTIFICATIONS_MAX:]
        self.hass.bus.async_fire(EVENT_NOTIFICATION, item)
        # Single spot that drives sound for every notification (mapped by source +
        # severity). Alarm/timer alerts use their own sounds; others use the
        # per-severity notification sound.
        self.playback.on_notification(item)
        return item

    async def notify(self, title, message, severity="info", icon=None, area=None,
                     actions=None, notif_id=None, timeout=None, persistence="normal",
                     source="service"):
        self._add_notification(
            title=title, message=message, severity=severity, icon=icon, area=area,
            actions=actions, notif_id=notif_id, timeout=timeout, persistence=persistence,
            source=source,
        )
        await self._save()
        self._notify()

    async def dismiss_notification(self, notif_id):
        self.notifications = [n for n in self.notifications if n["id"] != notif_id]
        self._fire_dismissed(notif_id)
        await self._save()
        self._notify()

    async def mark_read(self, notif_id=None, area=None):
        """Handle a read/dismiss interaction.

        Sticky notifications are flagged read and kept; normal ones auto-clear
        (removed) on interaction. In both cases subscribers are told to close the
        matching toast, so acting on one device clears it everywhere.
        """
        affected = []
        remaining = []
        for n in self.notifications:
            match = (notif_id is None or n["id"] == notif_id) and (
                area is None or n.get("area") == area
            )
            if not match:
                remaining.append(n)
                continue
            affected.append(n["id"])
            if n.get("persistence") == "sticky":
                n["read"] = True
                remaining.append(n)
            # normal → dropped (auto-clear on interaction)
        self.notifications = remaining
        for nid in affected:
            self._fire_dismissed(nid)
        await self._save()
        self._notify()

    async def clear_notifications(self, area=None):
        if area is None:
            removed = [n["id"] for n in self.notifications]
            self.notifications = []
        else:
            removed = [n["id"] for n in self.notifications if n.get("area") == area]
            self.notifications = [n for n in self.notifications if n.get("area") != area]
        for nid in removed:
            self._fire_dismissed(nid)
        await self._save()
        self._notify()

    def _fire_dismissed(self, notif_id):
        """Signal subscribers that a notification was dismissed/read, so their
        toasts close on every device (not just the one that acted)."""
        self.playback.stop(notif_id)
        self.hass.bus.async_fire(EVENT_NOTIFICATION, {"id": notif_id, "dismissed": True})

    # ── settings ────────────────────────────────────────────
    def effective_settings(self, device_id=None) -> dict:
        """Merge defaults ⊕ global ⊕ this device's overrides."""
        merged = dict(SETTINGS_DEFAULTS)
        merged.update(self.settings.get("global") or {})
        if device_id:
            merged.update(self.settings.get("devices", {}).get(device_id) or {})
        return merged

    def settings_payload(self) -> dict:
        """The full settings snapshot pushed to subscribers / exposed on the sensor."""
        return {
            "defaults": dict(SETTINGS_DEFAULTS),
            "global": dict(self.settings.get("global") or {}),
            "devices": {k: dict(v) for k, v in (self.settings.get("devices") or {}).items()},
            "registry": {k: dict(v) for k, v in self.device_registry.items()},
        }

    def _fire_settings(self) -> None:
        self.hass.bus.async_fire(EVENT_SETTINGS, self.settings_payload())

    async def set_settings(self, values: dict, scope="global", device_id=None) -> None:
        """Set one or more setting keys at the given scope. `None` value clears a key."""
        clean = {k: v for k, v in (values or {}).items() if k in SETTINGS_KEYS}
        if scope == "device":
            if not device_id:
                return
            target = self.settings["devices"].setdefault(device_id, {})
        else:
            target = self.settings["global"]
        for key, value in clean.items():
            if value is None:
                target.pop(key, None)
            else:
                target[key] = value
        # Drop an emptied per-device override bucket so it fully inherits again.
        if scope == "device" and not self.settings["devices"].get(device_id):
            self.settings["devices"].pop(device_id, None)
        await self._save()
        self._fire_settings()
        self._notify()

    async def clear_settings(self, keys=None, scope="global", device_id=None) -> None:
        """Clear specific keys (or all) at a scope so they inherit again."""
        if scope == "device":
            bucket = self.settings["devices"].get(device_id)
            if not bucket:
                return
            if keys is None:
                self.settings["devices"].pop(device_id, None)
            else:
                for key in keys:
                    bucket.pop(key, None)
                if not bucket:
                    self.settings["devices"].pop(device_id, None)
        else:
            if keys is None:
                self.settings["global"] = {}
            else:
                for key in keys:
                    self.settings["global"].pop(key, None)
        await self._save()
        self._fire_settings()
        self._notify()

    async def register_device(
        self, device_id, area=None, name=None, media_player=None,
        client_width=None, client_height=None,
        client_orientation=None, client_form_factor=None,
    ) -> None:
        """Record/refresh a device so server-side playback can target its area."""
        if not device_id:
            return
        entry = self.device_registry.setdefault(device_id, {})
        if area is not None:
            entry["area"] = area
        if name is not None:
            entry["name"] = name
        if media_player is not None:
            # The device's own media player (browser_mod / View Assist), used as the
            # final fallback when no per-device or global media_player is set.
            entry["media_player"] = media_player or None
        # Frontend-reported client characteristics (viewport / orientation).
        if client_width is not None:
            entry["client_width"] = client_width
        if client_height is not None:
            entry["client_height"] = client_height
        if client_orientation is not None:
            entry["client_orientation"] = client_orientation
        if client_form_factor is not None:
            entry["client_form_factor"] = client_form_factor
        entry["last_seen"] = dt_util.utcnow().isoformat()
        await self._save()
        self._fire_settings()
        self._notify()

    def _present_devices(self):
        """Registered devices seen within the presence TTL (device_id, entry)."""
        now = dt_util.utcnow()
        for did, entry in self.device_registry.items():
            seen = dt_util.parse_datetime(entry.get("last_seen") or "")
            if seen and (now - seen).total_seconds() <= DEVICE_PRESENCE_TTL:
                yield did, entry

    # ── notify sensors ──────────────────────────────────────
    def register(self, cb):
        self._update_cbs.add(cb)
        return lambda: self._update_cbs.discard(cb)

    def _notify(self):
        for cb in list(self._update_cbs):
            cb()

    async def refresh_requirements(self) -> None:
        """Re-run server-side dependency detection and update the sensor."""
        from .requirements import compute_requirements

        self.requirements = await compute_requirements(self.hass)
        self._notify()
