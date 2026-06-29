"""Ted's Cards Backend — alarms & timers for Ted's Cards."""

DOMAIN = "teds_cards_backend"

STORAGE_VERSION = 1
STORAGE_KEY = DOMAIN

# How many most-recent timers to remember for quick re-start.
RECENT_TIMERS_MAX = 5

EVENT_ALARM_RINGING = f"{DOMAIN}_alarm_ringing"
EVENT_TIMER_FINISHED = f"{DOMAIN}_timer_finished"
