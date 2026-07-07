# Ted's Cards Backend

Home Assistant integration backing **Ted's Cards** alarms & timers — so the Alarm and Timer cards can add/manage them without you creating helpers by hand.

- **Alarms** — label, description, time, repeat days, enabled; fires a `teds_cards_backend_alarm_ringing` event you can automate.
- **Timers** — countdown (h/m/s) + name, pause/resume, edit (rename/change duration), view/cancel in-progress, last 5 recent for one-tap restart; fires `teds_cards_backend_timer_finished`.

Install via HACS (custom repository, category **Integration**), restart, then add **Ted's Cards Backend** from Settings → Devices & Services. Pair with the cards in [Teds-Cards](https://github.com/tedr91/Teds-Cards).

## Changelog

### v1.0.9

- **Dismissals sync across devices** — `mark_read`, `dismiss_notification`, and `clear_notifications` now broadcast a dismissal signal (a `teds_cards_backend_notification` event with `dismissed: true`) for each affected notification, so a toast dismissed on one device closes on every device showing it (e.g. a house-wide alarm cleared everywhere at once). Pairs with Ted's Cards v1.0.65+.

### v1.0.8

- **Change an alarm/timer's scope (incl. house-wide)** — `update_alarm` and `update_timer` now accept **`location`** and apply it even when set to `null`, so an existing alarm or timer can be moved to a room or made **house-wide** (cleared location). Previously `update_alarm` ignored `null`/unset fields, so a location could never be cleared, and `update_timer` didn't take a location at all. Pairs with Ted's Cards v1.0.62+ (per-item "This room / House-wide" scope).

### v1.0.7

- **Notifications work for non-admin users** — added a `teds_cards_backend/subscribe_notifications` WebSocket command so kiosk/Wallpanel (non-admin) dashboards can receive notifications. Home Assistant blocks non-admin users from subscribing to custom events via `subscribe_events`, which caused repeated "Unauthorized" errors; cards now use this command instead. The `teds_cards_backend_notification` event still fires, so event-triggered automations are unaffected. Pairs with Ted's Cards v1.0.49+.

### v1.0.6

- **Notifications (foundation)** — a new server-side notification store: a **`notify`** service (title, message, severity, icon, area, timeout, sticky) plus `dismiss_notification`, `mark_read`, and `clear_notifications`. Exposes **`sensor.teds_notifications`** (unread count + list) and fires a `teds_cards_backend_notification` event. Finished timers and ringing alarms now also create notifications, so they flow through one path. Pairs with Ted's Cards v1.0.34+.

### v1.0.5

- **New `remove_recent` service** — removes a preset from the Recent timers list (matched by name, duration, and area). Powers the Timer card's long-press "Delete" on recent presets.

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
