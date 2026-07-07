"""Server-side sound playback for Ted's Cards alerts (notifications/timers/alarms).

When an alert fires, the engine resolves the target `media_player`(s) from the
firing area's present devices' effective settings (falling back to the global
media player), deduped by entity, and plays the configured sound at the configured
volume. Timer/alarm alerts hand a native `repeat` flag to the media player, so it
loops the sound for its own length; ringing stops when the notification is
dismissed, or auto-stops after the notification's timeout. Devices in Do Not
Disturb are skipped.
"""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later

from .const import DEFAULT_SOUND


class PlaybackEngine:
    def __init__(self, manager) -> None:
        self._m = manager
        self.hass = manager.hass
        # notif_id -> {"cancel": fn|None, "plays": [...]}
        self._active: dict[str, dict] = {}

    @staticmethod
    def _sound_url(value, kind) -> str:
        """Resolve a sound setting to a URL ("default" → bundled file for the kind)."""
        if not value or value == DEFAULT_SOUND:
            return f"/teds_cards_backend/sounds/{kind}.mp3"
        return value

    @staticmethod
    def _notification_sound(eff, severity):
        """Per-severity notification sound, falling back to the general one, then default."""
        val = eff.get(f"notification_sound_{severity}") if severity else None
        if not val or val == DEFAULT_SOUND:
            val = eff.get("notification_sound")
        if not val or val == DEFAULT_SOUND:
            return "/teds_cards_backend/sounds/notification.mp3"
        return val

    def on_notification(self, item) -> None:
        """Single entry point: drive sound for any created notification.

        Maps the notification `source` to a playback kind (alarm/timer play their
        own alert sound; everything else plays the severity's notification sound).
        """
        source = item.get("source")
        kind = "alarm" if source == "alarm" else "timer" if source == "timer" else "notification"
        self.play(
            kind,
            item.get("area"),
            item.get("id"),
            severity=item.get("severity"),
            timeout=item.get("timeout"),
        )

    def _targets(self, area):
        """(effective_settings, media_player) for each distinct player in the fired area.

        House-wide (area is None) targets every present device; otherwise only the
        devices whose registered area matches. Falls back to the global media player
        when no present device supplies one. Devices in DND are skipped.
        """
        m = self._m
        seen = set()
        out = []
        for did, entry in m._present_devices():
            if area and entry.get("area") != area:
                continue
            eff = m.effective_settings(did)
            if eff.get("do_not_disturb"):
                continue
            # Per-device / global media player, else the device's own client player.
            mp = eff.get("media_player") or entry.get("media_player")
            if not mp or mp in seen:
                continue
            seen.add(mp)
            out.append((eff, mp))
        if not out:
            eff = m.effective_settings(None)
            mp = eff.get("media_player")
            if mp and not eff.get("do_not_disturb"):
                out.append((eff, mp))
        return out

    def play(self, kind, area, notif_id, severity=None, timeout=None) -> None:
        """Play an alert of `kind` ("timer"|"alarm"|"notification") for an area.

        Timer/alarm alerts pass a native `repeat` flag to the media player so it
        loops the sound for its own length. Ringing stops when the notification is
        dismissed (`stop`), or auto-stops after `timeout` seconds if given.
        """
        targets = self._targets(area)
        if not targets:
            return
        repeat = kind in ("timer", "alarm") and bool(
            targets[0][0].get(f"{kind}_alert_repeat", True)
        )
        plays = []
        for eff, mp in targets:
            if kind == "notification":
                sound = self._notification_sound(eff, severity)
                volume = eff.get("notification_volume", 50)
            else:
                sound = self._sound_url(eff.get(f"{kind}_alert_sound"), kind)
                volume = eff.get(f"{kind}_alert_volume", 60)
            plays.append({"mp": mp, "sound": sound, "volume": volume})

        for p in plays:
            self.hass.async_create_task(
                self._play_one(p["mp"], p["sound"], p["volume"], repeat)
            )

        if not repeat or not notif_id:
            return

        # The player loops natively; auto-stop after the notification's lifetime
        # if it has one, otherwise keep looping until it is dismissed.
        cancel = None
        if timeout:
            @callback
            def _auto_stop(_now=None):
                self.stop(notif_id)

            cancel = async_call_later(self.hass, float(timeout), _auto_stop)
        self._active[notif_id] = {"cancel": cancel, "plays": plays}

    async def _play_one(self, mp, sound, volume, repeat) -> None:
        try:
            level = max(0.0, min(1.0, (float(volume or 0)) / 100.0))
            await self.hass.services.async_call(
                "media_player", "volume_set",
                {"entity_id": mp, "volume_level": level}, blocking=False,
            )
            await self.hass.services.async_call(
                "media_player", "repeat_set",
                {"entity_id": mp, "repeat": "one" if repeat else "off"}, blocking=False,
            )
            await self.hass.services.async_call(
                "media_player", "play_media",
                {"entity_id": mp, "media_content_id": sound, "media_content_type": "music"},
                blocking=False,
            )
        except Exception:  # noqa: BLE001 - a bad media_player must not break the tick loop
            pass

    async def _stop_one(self, mp) -> None:
        try:
            await self.hass.services.async_call(
                "media_player", "repeat_set",
                {"entity_id": mp, "repeat": "off"}, blocking=False,
            )
            await self.hass.services.async_call(
                "media_player", "media_stop",
                {"entity_id": mp}, blocking=False,
            )
        except Exception:  # noqa: BLE001 - a bad media_player must not break dismissal
            pass

    def stop(self, notif_id) -> None:
        entry = self._active.pop(notif_id, None)
        if not entry:
            return
        if entry.get("cancel"):
            entry["cancel"]()
        for p in entry["plays"]:
            self.hass.async_create_task(self._stop_one(p["mp"]))

    def shutdown(self) -> None:
        for notif_id in list(self._active):
            self.stop(notif_id)
