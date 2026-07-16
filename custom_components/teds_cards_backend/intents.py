"""Custom Assist intents for Ted's Cards Backend.

Registers voice/conversation intents for managing alarms and notifications,
backed by the existing :class:`TedsManager` services. Requests are scoped to the
voice satellite's area (or a spoken area) so the same phrase works from any
device — and, in the future, from a Ted's Dashboard device acting as a
satellite.

The matching *sentences* live in ``sentences/en.yaml`` and are installed into
``<config>/custom_sentences/en/`` at setup (that's the only folder the default
conversation agent auto-loads).
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    intent,
)

from .const import (
    DOMAIN,
    INTENT_ADD_ALARM,
    INTENT_CLEAR_NOTIFICATIONS,
    INTENT_DISABLE_ALARM,
    INTENT_ENABLE_ALARM,
    INTENT_LIST_ALARMS,
    INTENT_MARK_NOTIFICATIONS_READ,
    INTENT_READ_NOTIFICATIONS,
    INTENT_REMOVE_ALARM,
)

_REGISTERED = f"{DOMAIN}_intents_registered"

# Ted's alarms store weekdays as Python weekday ints (Monday = 0).
_WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_DAY_TOKEN_TO_INT = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}
_EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]
_WEEKDAYS = [0, 1, 2, 3, 4]
_WEEKENDS = [5, 6]


@callback
def async_register_intents(hass: HomeAssistant) -> None:
    """Register Ted's custom Assist intent handlers once."""
    if hass.data.get(_REGISTERED):
        return
    intent.async_register(hass, AddAlarmIntent())
    intent.async_register(hass, ListAlarmsIntent())
    intent.async_register(hass, SetAlarmEnabledIntent(INTENT_ENABLE_ALARM, True))
    intent.async_register(hass, SetAlarmEnabledIntent(INTENT_DISABLE_ALARM, False))
    intent.async_register(hass, RemoveAlarmIntent())
    intent.async_register(hass, ReadNotificationsIntent())
    intent.async_register(hass, ClearNotificationsIntent())
    intent.async_register(hass, MarkNotificationsReadIntent())
    hass.data[_REGISTERED] = True


# ── shared helpers ──────────────────────────────────────────


def _manager(hass: HomeAssistant):
    """Return the single TedsManager (first config entry), or None."""
    return next(iter((hass.data.get(DOMAIN) or {}).values()), None)


def _slot(intent_obj: intent.Intent, name: str):
    """Return a recognized slot's value, or None when absent/empty."""
    entry = intent_obj.slots.get(name)
    if not entry:
        return None
    value = entry.get("value")
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _area_id_by_name(hass: HomeAssistant, name: str) -> str | None:
    """Resolve a spoken area name to its area_id (case-insensitive)."""
    reg = ar.async_get(hass)
    area = reg.async_get_area_by_name(name)
    if area:
        return area.id
    wanted = name.strip().casefold()
    for area in reg.async_list_areas():
        if area.name.casefold() == wanted:
            return area.id
        for alias in area.aliases:
            if alias.casefold() == wanted:
                return area.id
    return None


def _resolve_area(hass: HomeAssistant, intent_obj: intent.Intent) -> str | None:
    """Resolve the target area_id for a request.

    Priority: spoken area → the satellite's preferred area → the calling
    device's area → None (house-wide).
    """
    spoken = _slot(intent_obj, "area")
    if spoken:
        if area_id := _area_id_by_name(hass, str(spoken)):
            return area_id
    if preferred := _slot(intent_obj, "preferred_area_id"):
        return str(preferred)
    device_id = intent_obj.device_id
    if device_id and (device := dr.async_get(hass).async_get(device_id)):
        if device.area_id:
            return device.area_id
    return None


def _to_24h(hour: int, minute: int, meridiem: str | None) -> str:
    """Build an ``HH:MM`` string from a spoken hour/minute/(am|pm)."""
    h = int(hour) % 24
    m = int(minute) % 60
    if meridiem == "pm" and h < 12:
        h += 12
    elif meridiem == "am" and h == 12:
        h = 0
    return f"{h:02d}:{m:02d}"


def _spoken_time(hhmm: str) -> str:
    """Format ``HH:MM`` (24h) as a friendly 12-hour string, e.g. ``7:05 AM``."""
    try:
        h, m = (int(x) for x in hhmm.split(":"))
    except (ValueError, AttributeError):
        return hhmm
    period = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {period}"


def _days_from_set(dayset: str | None) -> list[int]:
    """Map a spoken day set token to Ted's weekday-int list (Monday = 0)."""
    if not dayset or dayset == "daily":
        return list(_EVERY_DAY)
    if dayset == "weekdays":
        return list(_WEEKDAYS)
    if dayset == "weekends":
        return list(_WEEKENDS)
    if dayset in _DAY_TOKEN_TO_INT:
        return [_DAY_TOKEN_TO_INT[dayset]]
    return list(_EVERY_DAY)


def _spoken_days(days: list[int] | None) -> str:
    """Describe a weekday-int list for speech."""
    normalized = sorted(set(days or _EVERY_DAY))
    if normalized == _EVERY_DAY:
        return "every day"
    if normalized == _WEEKDAYS:
        return "on weekdays"
    if normalized == _WEEKENDS:
        return "on weekends"
    names = [_WEEKDAY_NAMES[d] for d in normalized if 0 <= d <= 6]
    return "on " + ", ".join(names) if names else "every day"


def _alarm_time_slots(intent_obj: intent.Intent) -> str | None:
    """Return an ``HH:MM`` from hour/minute/meridiem slots, if an hour was given."""
    hour = _slot(intent_obj, "hour")
    if hour is None:
        return None
    minute = _slot(intent_obj, "minute") or 0
    meridiem = _slot(intent_obj, "meridiem")
    return _to_24h(hour, minute, meridiem)


def _match_alarms(mgr, intent_obj: intent.Intent) -> list[dict]:
    """Find alarms matching a spoken label (substring) or time."""
    name = _slot(intent_obj, "name")
    if name:
        wanted = str(name).casefold()
        return [a for a in mgr.alarms if wanted in (a.get("label") or "").casefold()]
    hhmm = _alarm_time_slots(intent_obj)
    if hhmm:
        return [a for a in mgr.alarms if a.get("time") == hhmm]
    return []


def _speech(intent_obj: intent.Intent, text: str) -> intent.IntentResponse:
    response = intent_obj.create_response()
    response.async_set_speech(text)
    return response


# ── alarm intents ───────────────────────────────────────────


class AddAlarmIntent(intent.IntentHandler):
    """Create an alarm from a spoken time (and optional day set / name)."""

    intent_type = INTENT_ADD_ALARM
    description = "Add an alarm to Ted's Cards for a given time"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        mgr = _manager(hass)
        if mgr is None:
            return _speech(intent_obj, "Ted's Cards is not set up yet.")

        hour = _slot(intent_obj, "hour")
        if hour is None:
            return _speech(intent_obj, "What time should I set the alarm for?")

        hhmm = _to_24h(hour, _slot(intent_obj, "minute") or 0, _slot(intent_obj, "meridiem"))
        days = _days_from_set(_slot(intent_obj, "dayset"))
        area_id = _resolve_area(hass, intent_obj)
        label = _slot(intent_obj, "name") or f"{_spoken_time(hhmm)} alarm"

        await mgr.add_alarm(str(label), hhmm, days, location=area_id)
        return _speech(
            intent_obj,
            f"Alarm set for {_spoken_time(hhmm)} {_spoken_days(days)}.",
        )


class ListAlarmsIntent(intent.IntentHandler):
    """Read back the alarms for the current area (plus house-wide)."""

    intent_type = INTENT_LIST_ALARMS
    description = "List the alarms in Ted's Cards"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        mgr = _manager(hass)
        if mgr is None:
            return _speech(intent_obj, "Ted's Cards is not set up yet.")

        area_id = _resolve_area(hass, intent_obj)
        alarms = [
            a for a in mgr.alarms
            if a.get("location") in (None, area_id)
        ]
        if not alarms:
            return _speech(intent_obj, "You have no alarms set.")

        alarms.sort(key=lambda a: (not a.get("enabled"), a.get("time") or ""))
        parts = []
        for a in alarms:
            state = "" if a.get("enabled") else " (disabled)"
            parts.append(
                f"{a.get('label') or 'Alarm'} at {_spoken_time(a.get('time') or '')}"
                f" {_spoken_days(a.get('days'))}{state}"
            )
        count = len(alarms)
        noun = "alarm" if count == 1 else "alarms"
        return _speech(intent_obj, f"You have {count} {noun}: " + "; ".join(parts) + ".")


class SetAlarmEnabledIntent(intent.IntentHandler):
    """Enable or disable a matched alarm."""

    def __init__(self, intent_type: str, enabled: bool) -> None:
        self.intent_type = intent_type
        self._enabled = enabled
        self.description = (
            "Enable an alarm in Ted's Cards" if enabled
            else "Disable an alarm in Ted's Cards"
        )

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        mgr = _manager(hass)
        if mgr is None:
            return _speech(intent_obj, "Ted's Cards is not set up yet.")

        word = "enable" if self._enabled else "disable"
        matches = _match_alarms(mgr, intent_obj)
        if not matches:
            return _speech(intent_obj, f"I couldn't find an alarm to {word}.")
        if len(matches) > 1:
            return _speech(
                intent_obj,
                f"You have {len(matches)} matching alarms — please be more specific.",
            )

        alarm = matches[0]
        await mgr.update_alarm(alarm["id"], enabled=self._enabled)
        state = "enabled" if self._enabled else "disabled"
        return _speech(
            intent_obj,
            f"{alarm.get('label') or 'Alarm'} at {_spoken_time(alarm.get('time') or '')} {state}.",
        )


class RemoveAlarmIntent(intent.IntentHandler):
    """Delete a matched alarm."""

    intent_type = INTENT_REMOVE_ALARM
    description = "Remove an alarm from Ted's Cards"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        mgr = _manager(hass)
        if mgr is None:
            return _speech(intent_obj, "Ted's Cards is not set up yet.")

        matches = _match_alarms(mgr, intent_obj)
        if not matches:
            return _speech(intent_obj, "I couldn't find an alarm to remove.")
        if len(matches) > 1:
            return _speech(
                intent_obj,
                f"You have {len(matches)} matching alarms — please be more specific.",
            )

        alarm = matches[0]
        await mgr.remove_alarm(alarm["id"])
        return _speech(
            intent_obj,
            f"Removed the {_spoken_time(alarm.get('time') or '')} alarm.",
        )


# ── notification intents ────────────────────────────────────


def _notifications_for_area(mgr, area_id: str | None) -> list[dict]:
    """Notifications relevant to an area: house-wide plus that area (all if none)."""
    if area_id is None:
        return list(mgr.notifications)
    return [n for n in mgr.notifications if n.get("area") in (None, area_id)]


class ReadNotificationsIntent(intent.IntentHandler):
    """Read out the current notifications for this area."""

    intent_type = INTENT_READ_NOTIFICATIONS
    description = "Read Ted's Cards notifications"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        mgr = _manager(hass)
        if mgr is None:
            return _speech(intent_obj, "Ted's Cards is not set up yet.")

        area_id = _resolve_area(hass, intent_obj)
        items = _notifications_for_area(mgr, area_id)
        if not items:
            return _speech(intent_obj, "You have no notifications.")

        parts = []
        for n in items:
            title = (n.get("title") or "").strip()
            message = (n.get("message") or "").strip()
            if title and message:
                parts.append(f"{title}: {message}")
            else:
                parts.append(title or message)
        count = len(items)
        noun = "notification" if count == 1 else "notifications"
        return _speech(intent_obj, f"You have {count} {noun}. " + ". ".join(parts) + ".")


class ClearNotificationsIntent(intent.IntentHandler):
    """Clear notifications for this area (or everywhere)."""

    intent_type = INTENT_CLEAR_NOTIFICATIONS
    description = "Clear Ted's Cards notifications"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        mgr = _manager(hass)
        if mgr is None:
            return _speech(intent_obj, "Ted's Cards is not set up yet.")

        # "clear all notifications everywhere" forces a house-wide clear.
        force_all = _slot(intent_obj, "scope") == "all"
        area_id = None if force_all else _resolve_area(hass, intent_obj)
        await mgr.clear_notifications(area_id)
        where = "" if area_id is None else " here"
        return _speech(intent_obj, f"Cleared your notifications{where}.")


class MarkNotificationsReadIntent(intent.IntentHandler):
    """Mark notifications for this area (or everywhere) as read."""

    intent_type = INTENT_MARK_NOTIFICATIONS_READ
    description = "Mark Ted's Cards notifications as read"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        mgr = _manager(hass)
        if mgr is None:
            return _speech(intent_obj, "Ted's Cards is not set up yet.")

        force_all = _slot(intent_obj, "scope") == "all"
        area_id = None if force_all else _resolve_area(hass, intent_obj)
        await mgr.mark_read(None, area_id)
        return _speech(intent_obj, "Marked your notifications as read.")
