# Ted's Cards Backend

Home Assistant integration backing **Ted's Cards** alarms & timers — so the Alarm and Timer cards can add/manage them without you creating helpers by hand.

- **Alarms** — label, description, time, repeat days, enabled; fires a `teds_cards_backend_alarm_ringing` event you can automate.
- **Timers** — countdown (h/m/s) + name, pause/resume, edit (rename/change duration), view/cancel in-progress, last 5 recent for one-tap restart; fires `teds_cards_backend_timer_finished`.

Install via HACS (custom repository, category **Integration**), restart, then add **Ted's Cards Backend** from Settings → Devices & Services. Pair with the cards in [Teds-Cards](https://github.com/tedr91/Teds-Cards).

## Changelog

### v1.0.4

- **Finished timers are removed reliably** — the timer-finished handler now runs on the event loop (as a proper callback), so a completed timer is dropped from the active list and the sensor updates immediately instead of occasionally lingering at 0:00.
- The `teds_cards_backend_timer_finished` event now also includes the timer's **`duration`** (seconds), so the cards can show how long the finished timer ran.

### v1.0.3

- **Location-aware alarms & timers** — `add_alarm` and `start_timer` now accept an optional **`location`** (an HA area) that is stored on each alarm/timer (and recent preset). The `teds_cards_backend_alarm_ringing` and `teds_cards_backend_timer_finished` events now include `location` (area id) and `area_name`, so automations can announce in the room the item belongs to. Unset `location` behaves as before (global).

### v1.0.2

- **Reliable card refresh** — the alarms and timers sensors now expose fresh copies of their data on every read instead of the manager's live list. Home Assistant compares old vs. new state by value, so returning the live (already-mutated) reference could make attribute-only changes — such as adding or editing an alarm — fail to push an update to the cards. They now refresh immediately.

### v1.0.1

- **Timers** — added **pause**, **resume**, and **edit** (rename / change duration) via new `pause_timer`, `resume_timer`, and `update_timer` services. The timers sensor now exposes each active timer's `duration`, `remaining`, and `paused` state so the Timer card can render progress and paused timers.

### v1.0.0

- Initial release — alarms and timers backend for Ted's Cards.
