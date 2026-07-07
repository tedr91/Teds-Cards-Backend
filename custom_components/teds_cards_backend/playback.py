"""Server-side sound playback for Ted's Cards alerts (notifications/timers/alarms).

When an alert fires, the engine resolves the target `media_player`(s) from the
firing area's present devices' effective settings (falling back to the global
media player), deduped by entity, and plays the configured sound at the configured
volume. Timer/alarm alerts repeat up to the configured max, and are cancelled when
the notification is dismissed. Devices in Do Not Disturb are skipped.
"""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later

from .const import DEFAULT_SOUND

# Seconds between repeated plays of a timer/alarm alert.
REPEAT_INTERVAL = 6


class PlaybackEngine:
    def __init__(self, manager) -> None:
        self._m = manager
        self.hass = manager.hass
        # notif_id -> {"cancel": fn, "left": int, "plays": [...]}
        self._active: dict[str, dict] = {}

    @staticmethod
    def _sound_url(value, kind) -> str:
        """Resolve a sound setting to a URL ("default" → bundled file for the kind)."""
        if not value or value == DEFAULT_SOUND:
            return f"/teds_cards_backend/sounds/{kind}.mp3"
        return value

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

    def play(self, kind, area, notif_id) -> None:
        """Play an alert of `kind` ("timer"|"alarm"|"notification") for an area."""
        targets = self._targets(area)
        if not targets:
            return
        plays = []
        for eff, mp in targets:
            if kind == "notification":
                sound = self._sound_url(eff.get("notification_sound"), "notification")
                volume = eff.get("notification_volume", 50)
            else:
                sound = self._sound_url(eff.get(f"{kind}_alert_sound"), kind)
                volume = eff.get(f"{kind}_alert_volume", 60)
            plays.append({"mp": mp, "sound": sound, "volume": volume})

        self._fire_once(plays)

        # Repeat scheduling (timers/alarms only), using the first target's settings.
        if kind in ("timer", "alarm") and notif_id:
            eff0 = targets[0][0]
            if eff0.get(f"{kind}_alert_repeat", True):
                left = int(eff0.get(f"{kind}_alert_max_repeats", 10) or 1) - 1
                if left > 0:
                    self._schedule(notif_id, plays, left)

    def _fire_once(self, plays) -> None:
        for p in plays:
            self.hass.async_create_task(self._play_one(p["mp"], p["sound"], p["volume"]))

    async def _play_one(self, mp, sound, volume) -> None:
        try:
            level = max(0.0, min(1.0, (float(volume or 0)) / 100.0))
            await self.hass.services.async_call(
                "media_player", "volume_set",
                {"entity_id": mp, "volume_level": level}, blocking=False,
            )
            await self.hass.services.async_call(
                "media_player", "play_media",
                {"entity_id": mp, "media_content_id": sound, "media_content_type": "music"},
                blocking=False,
            )
        except Exception:  # noqa: BLE001 - a bad media_player must not break the tick loop
            pass

    def _schedule(self, notif_id, plays, left) -> None:
        @callback
        def _tick(_now=None):
            entry = self._active.get(notif_id)
            if not entry:
                return
            self._fire_once(plays)
            entry["left"] -= 1
            if entry["left"] > 0:
                entry["cancel"] = async_call_later(self.hass, REPEAT_INTERVAL, _tick)
            else:
                self._active.pop(notif_id, None)

        cancel = async_call_later(self.hass, REPEAT_INTERVAL, _tick)
        self._active[notif_id] = {"cancel": cancel, "left": left, "plays": plays}

    def stop(self, notif_id) -> None:
        entry = self._active.pop(notif_id, None)
        if entry and entry.get("cancel"):
            entry["cancel"]()

    def shutdown(self) -> None:
        for entry in list(self._active.values()):
            if entry.get("cancel"):
                entry["cancel"]()
        self._active.clear()
