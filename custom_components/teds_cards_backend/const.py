"""Ted's Cards Backend — alarms & timers for Ted's Cards."""

DOMAIN = "teds_cards_backend"

STORAGE_VERSION = 1
STORAGE_KEY = DOMAIN

# How many most-recent timers to remember for quick re-start.
RECENT_TIMERS_MAX = 5

# How many notifications to keep in the store (FIFO, newest kept).
NOTIFICATIONS_MAX = 50

EVENT_ALARM_RINGING = f"{DOMAIN}_alarm_ringing"
EVENT_TIMER_FINISHED = f"{DOMAIN}_timer_finished"
EVENT_NOTIFICATION = f"{DOMAIN}_notification"
EVENT_SETTINGS = f"{DOMAIN}_settings"

# Sentinel meaning "use the bundled default sound for this alert kind".
DEFAULT_SOUND = "default"

# How long (seconds) a registered device is considered "present" for server-side
# playback targeting after its last heartbeat.
DEVICE_PRESENCE_TTL = 900

# Global settings baseline. Per-device overrides layer on top of these; a card's
# effective value = device override (if set) else global (if set) else default.
SETTINGS_DEFAULTS = {
    # Timers
    "timer_snooze_enabled": True,
    "timer_snooze_minutes": 1,
    "timer_alert_sound": DEFAULT_SOUND,
    "timer_alert_volume": 60,
    "timer_alert_repeat": True,
    # Alarms
    "alarm_snooze_enabled": True,
    "alarm_snooze_minutes": 9,
    "alarm_alert_sound": DEFAULT_SOUND,
    "alarm_alert_volume": 70,
    "alarm_alert_repeat": True,
    # Notifications
    "notification_sound": DEFAULT_SOUND,
    "notification_volume": 50,
    # Per-severity notification sounds ("default" → use notification_sound).
    "notification_sound_info": DEFAULT_SOUND,
    "notification_sound_success": DEFAULT_SOUND,
    "notification_sound_warning": DEFAULT_SOUND,
    "notification_sound_danger": DEFAULT_SOUND,
    "notification_sound_tip": DEFAULT_SOUND,
    # Media
    "media_player": None,
    "media_player_volume": 50,
    # Cameras — ordered list of camera entity ids. Global = the available allow-list;
    # per-device = the curated subset that device shows (empty inherits the global list).
    "cameras_list": [],
    # Navbar (per-device navbar behaviour; empty/false means "follow the card's YAML").
    "navbar_auto_hide": False,
    "navbar_auto_hide_delay": 5,
    "navbar_float": False,
    "navbar_position": "bottom",
    "navbar_size": 48,
    # General
    "do_not_disturb": False,
    "debug_mode": False,
    # Navigation
    "dashboard_root": "ted-dashboard",
    "home_dashboard": "[root]/welcome",
    "alarms_dashboard": "[root]/alarms-timers?tab=alarms",
    "timers_dashboard": "[root]/alarms-timers?tab=timers",
    "weather_dashboard": "[root]/weather",
    "calendar_dashboard": "[root]/calendar-month",
    "cameras_dashboard": "[root]/cameras",
    "climate_dashboard": "[root]/climate",
    "music_dashboard": "[root]/music",
    "photos_dashboard": "[root]/photos",
    "info_dashboard": "[root]/info",
    "announce_dashboard": "[root]/announce",
    "auto_return_home_after": 0,
}

# Only keys present in SETTINGS_DEFAULTS may be written (guards the services/WS).
SETTINGS_KEYS = frozenset(SETTINGS_DEFAULTS)

