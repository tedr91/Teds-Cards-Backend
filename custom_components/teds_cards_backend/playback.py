"""Server-side sound playback for Ted's Cards alerts (notifications/timers/alarms).

When an alert fires, the engine resolves the target `media_player`(s) from the
firing area's present devices' effective settings (falling back to the global
media player), deduped by entity. Devices in Do Not Disturb are skipped.

Playing over existing media uses one of two strategies per player:

- **Announce-capable players** get `media_player.play_media` with `announce: True`,
  which natively ducks/pauses the current media and auto-resumes it when the alert
  finishes — no manual restore needed. Repeating alerts re-announce every sound
  length.
- **Other players** (e.g. BrowserMod, Squeezelite) fall back to snapshotting the
  current volume + media, playing the alert (looping natively via `repeat_set`),
  then restoring volume and resuming the previous media on stop.

Stopping is immediate (`media_stop`), and every player is returned to its prior
state so nothing is left in a weird state.
"""

from __future__ import annotations

import io
import logging
import os

from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import DEFAULT_SOUND

_LOGGER = logging.getLogger(__name__)

# MediaPlayerEntityFeature.MEDIA_ANNOUNCE bit (2**20).
MEDIA_ANNOUNCE = 1 << 20
# Fallback sound length (seconds) when the real duration can't be determined.
DEFAULT_DURATION = 5.0
# Bundled sounds are served under this path; the files live in ./sounds/.
SOUNDS_URL_PREFIX = "/teds_cards_backend/sounds/"


class PlaybackEngine:
    def __init__(self, manager) -> None:
        self._m = manager
        self.hass = manager.hass
        # notif_id -> {"plays": [...], "loops": [...], "cancels": [...]}
        self._active: dict[str, dict] = {}
        # sound url -> length in seconds (resolved lazily, then cached).
        self._durations: dict[str, float] = {}
        self._sounds_dir = os.path.join(os.path.dirname(__file__), "sounds")

    @staticmethod
    def _sound_url(value, kind) -> str:
        """Resolve a sound setting to a URL ("default" → bundled file for the kind)."""
        if not value or value == DEFAULT_SOUND:
            return f"{SOUNDS_URL_PREFIX}{kind}.mp3"
        return value

    @staticmethod
    def _notification_sound(eff, severity):
        """Per-severity notification sound, falling back to the general one, then default."""
        val = eff.get(f"notification_sound_{severity}") if severity else None
        if not val or val == DEFAULT_SOUND:
            val = eff.get("notification_sound")
        if not val or val == DEFAULT_SOUND:
            return f"{SOUNDS_URL_PREFIX}notification.mp3"
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

        Announce-capable players duck/pause and auto-resume their current media;
        other players snapshot their volume + media, play the alert, then restore.
        Timer/alarm alerts repeat (announce every sound length, or natively via
        `repeat_set`); ringing stops on dismiss (`stop`) or after `timeout` seconds.
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
            announce, snapshot = self._inspect(mp)
            plays.append({
                "mp": mp,
                "sound": sound,
                "volume": volume,
                "announce": announce,
                "snapshot": snapshot,
            })

        self.hass.async_create_task(self._run(plays, notif_id, repeat, timeout))

    def _inspect(self, mp):
        """Return (announce_supported, snapshot) for a media player.

        `snapshot` (for non-announce players) captures the current volume and, if
        playing, the media to resume afterwards. Announce players need no snapshot.
        """
        st = self.hass.states.get(mp)
        attrs = st.attributes if st else {}
        announce = bool(int(attrs.get("supported_features", 0) or 0) & MEDIA_ANNOUNCE)
        snapshot = None
        if not announce and st is not None:
            snapshot = {
                "volume": attrs.get("volume_level"),
                "content_id": attrs.get("media_content_id") if st.state == "playing" else None,
                "content_type": attrs.get("media_content_type") or "music",
            }
        return announce, snapshot

    async def _run(self, plays, notif_id, repeat, timeout) -> None:
        """Fire the initial play on each target and set up repeat/restore handling."""
        # Replacing an active alert on the same id: drop its timers (no restore —
        # we're about to play again on the same players).
        if notif_id:
            old = self._active.pop(notif_id, None)
            if old:
                self._cancel_timers(old)

        for p in plays:
            if p["announce"]:
                await self._announce(p["mp"], p["sound"])
            else:
                await self._play_media(p["mp"], p["sound"], p["volume"], repeat)

        if not notif_id:
            return

        entry = {"plays": plays, "loops": [], "cancels": []}

        if repeat:
            # Announce players re-announce every sound length; non-announce players
            # loop natively via repeat_set. Auto-stop after the notification lifetime.
            for p in plays:
                if p["announce"]:
                    duration = await self._sound_duration(p["sound"])
                    entry["loops"].append(self._schedule_reannounce(notif_id, p, duration))
            if timeout:
                entry["cancels"].append(
                    async_call_later(self.hass, float(timeout), self._auto_stop(notif_id))
                )
            self._active[notif_id] = entry
            return

        # One-shot: non-announce players that we touched need their volume/media
        # restored once the sound has played out (announce players auto-resume).
        restorable = [p for p in plays if not p["announce"] and p["snapshot"]]
        if restorable:
            durations = [await self._sound_duration(p["sound"]) for p in restorable]
            delay = max(durations) + 0.5
            entry["cancels"].append(
                async_call_later(self.hass, delay, self._auto_stop(notif_id))
            )
            self._active[notif_id] = entry

    def _auto_stop(self, notif_id):
        @callback
        def _cb(_now=None):
            self.stop(notif_id)

        return _cb

    def _schedule_reannounce(self, notif_id, p, duration):
        """Re-announce `p` every `duration` seconds until the alert is stopped."""
        loop: dict = {}

        @callback
        def _tick(_now=None):
            if notif_id not in self._active:
                return
            self.hass.async_create_task(self._announce(p["mp"], p["sound"]))
            loop["cancel"] = async_call_later(self.hass, duration, _tick)

        loop["cancel"] = async_call_later(self.hass, duration, _tick)
        return loop

    async def _announce(self, mp, sound) -> None:
        """Play `sound` as an announcement (native duck + auto-resume)."""
        try:
            await self.hass.services.async_call(
                "media_player", "play_media",
                {
                    "entity_id": mp,
                    "media_content_id": sound,
                    "media_content_type": "music",
                    "announce": True,
                },
                blocking=False,
            )
        except Exception:  # noqa: BLE001 - a bad media_player must not break playback
            pass

    async def _play_media(self, mp, sound, volume, repeat) -> None:
        """Set volume + repeat and play `sound` directly (non-announce path)."""
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
        except Exception:  # noqa: BLE001 - a bad media_player must not break playback
            pass

    async def _stop_and_restore(self, p) -> None:
        """Stop a non-announce player immediately and restore its prior state."""
        mp = p["mp"]
        try:
            await self.hass.services.async_call(
                "media_player", "repeat_set",
                {"entity_id": mp, "repeat": "off"}, blocking=False,
            )
            await self.hass.services.async_call(
                "media_player", "media_stop",
                {"entity_id": mp}, blocking=False,
            )
            snap = p.get("snapshot") or {}
            if snap.get("volume") is not None:
                await self.hass.services.async_call(
                    "media_player", "volume_set",
                    {"entity_id": mp, "volume_level": snap["volume"]}, blocking=False,
                )
            if snap.get("content_id"):
                await self.hass.services.async_call(
                    "media_player", "play_media",
                    {
                        "entity_id": mp,
                        "media_content_id": snap["content_id"],
                        "media_content_type": snap.get("content_type") or "music",
                    },
                    blocking=False,
                )
        except Exception:  # noqa: BLE001 - a bad media_player must not break dismissal
            pass

    @staticmethod
    def _cancel_timers(entry) -> None:
        for loop in entry.get("loops", []):
            if loop.get("cancel"):
                loop["cancel"]()
        for cancel in entry.get("cancels", []):
            if cancel:
                cancel()

    def stop(self, notif_id) -> None:
        entry = self._active.pop(notif_id, None)
        if not entry:
            return
        self._cancel_timers(entry)
        # Announce players auto-resume once their current clip ends; only the
        # non-announce (repeat_set / replaced) players need an explicit restore.
        for p in entry["plays"]:
            if not p["announce"]:
                self.hass.async_create_task(self._stop_and_restore(p))

    def shutdown(self) -> None:
        for notif_id in list(self._active):
            self.stop(notif_id)

    async def _sound_duration(self, url) -> float:
        """Length of `url` in seconds (cached). Bundled files are read from disk;
        custom URLs are fetched. Falls back to DEFAULT_DURATION on any failure."""
        if url in self._durations:
            return self._durations[url]
        duration = await self.hass.async_add_executor_job(self._read_duration, url)
        self._durations[url] = duration
        return duration

    def _read_duration(self, url) -> float:
        try:
            import mutagen  # noqa: PLC0415 - optional dep, only needed for durations

            if url.startswith(SOUNDS_URL_PREFIX):
                path = os.path.join(self._sounds_dir, os.path.basename(url))
                audio = mutagen.File(path)
            else:
                import requests  # noqa: PLC0415

                fetch = url
                if url.startswith("/"):
                    try:
                        fetch = f"{get_url(self.hass)}{url}"
                    except NoURLAvailableError:
                        return DEFAULT_DURATION
                resp = requests.get(fetch, timeout=10)
                audio = mutagen.File(io.BytesIO(resp.content))
            if audio and audio.info and audio.info.length:
                return round(float(audio.info.length), 2)
        except Exception as ex:  # noqa: BLE001 - duration is best-effort
            _LOGGER.debug("Could not read duration for %s: %s", url, ex)
        return DEFAULT_DURATION
