# Ted's Cards Backend

Home Assistant integration backing **Ted's Cards** alarms & timers — so the Alarm and Timer cards can add/manage them without you creating helpers by hand.

- **Alarms** — label, description, time, repeat days, enabled; fires a `teds_cards_backend_alarm_ringing` event you can automate.
- **Timers** — countdown (h/m/s) + name, pause/resume, edit (rename/change duration), view/cancel in-progress, last 5 recent for one-tap restart; fires `teds_cards_backend_timer_finished`.

Install via HACS (custom repository, category **Integration**), restart, then add **Ted's Cards Backend** from Settings → Devices & Services. Pair with the cards in [Teds-Cards](https://github.com/tedr91/Teds-Cards).

## Changelog

### v1.0.24

- **Cameras list setting** — added the `cameras_list` setting and allowed list values in `set_setting`, so the Camera Card can store the available camera allow-list (global) and each device's curated camera subset. Pairs with Ted's Cards v1.0.96+.

### v1.0.23

- **Navbar size setting** — added `navbar_size` to the settings store so the Ted's Cards navbar thickness can be set per-device (via the navbar's long-press menu or Settings → Navbar). Pairs with Ted's Cards v1.0.92+.

### v1.0.22

- **Navbar settings** — added `navbar_auto_hide`, `navbar_auto_hide_delay`, `navbar_float`, and `navbar_position` to the settings store so the Ted's Cards navbar can be configured per-device (and driven by the navbar's long-press menu). Pairs with Ted's Cards v1.0.90+.

### v1.0.21

- **Integration version exposed** — `sensor.teds_requirements` now includes a `version` attribute (the integration's manifest version), so dashboards and the new Ted's Cards Status Card can display the installed backend version. Pairs with Ted's Cards v1.0.86+.

### v1.0.20

- **Global settings are admin-only** — the `set_setting` and `clear_setting` services now reject writes at **global** scope unless the calling user is an administrator (device-scope writes stay open for everyone, e.g. kiosk devices). Pairs with Ted's Cards v1.0.84+, which shows Global settings read-only for non-admins.

### v1.0.19

- **Expanded Navigation settings** — added dashboard-path settings for **Weather, Calendar, Cameras, Climate, Music, Photos, Info, and Announce**, and corrected the **Home dashboard** default to `[root]/welcome`. (New keys must exist here because the backend whitelists which settings may be written.) Pairs with Ted's Cards v1.0.83+.

### v1.0.18

- **Server-side dependency detection** — a new `sensor.teds_requirements` reports which optional Ted Dashboard dependencies are present (HACS, browser_mod, Custom Icons, card-mod/UIX, layout-card, Ted's Cards, Daylight Calendar Card, Kiosk-Mode, and a weather entity). Detection runs after Home Assistant starts and re-checks when dashboards change, every 10 minutes, or via the new `check_requirements` service. Each requirement is exposed as an attribute (`ok`/`missing`/`unknown`) so dashboards can surface targeted "missing dependency" prompts with no fragile front-end detection. Pairs with the Ted Dashboard Welcome page.

### v1.0.17

- **Devices report their screen on registration** — `register_device` (service + WebSocket) now accepts and stores each device's `client_width`, `client_height`, `client_orientation`, and `client_form_factor`, exposed on the device registry via `sensor.teds_settings`. The Ted's Cards frontend reports these automatically (and re-reports, throttled, when the screen changes). Pairs with Ted's Cards v1.0.80+.

### v1.0.16

- **Repeating alarm/timer sounds actually repeat now** — the engine re-plays the sound every mp3-length (announce players re-announce; others re-play) instead of relying on `media_player.repeat_set`, which most players ignore for a one-shot media URL — so a repeating alert previously played only **once** on those players. It keeps looping until dismissed or the notification times out.

### v1.0.15

- **Alarms / Timers dashboard settings** — added `alarms_dashboard` (default `[root]/alarms-timers?tab=alarms`) and `timers_dashboard` (default `[root]/alarms-timers?tab=timers`) to the settings store, so the Alarms/Timers navbar status items know where to navigate. Pairs with Ted's Cards v1.0.74+.

### v1.0.14

- **New default navigation settings** — the **Home dashboard** now defaults to `[root]/home` (was `[root]/home-tablet`) and **Auto-return home after** defaults to `0` (never). Pairs with Ted's Cards v1.0.73+.

### v1.0.13

- **Pause-and-resume playback** — alerts no longer talk over (or clobber) whatever's playing. Announce-capable media players get a native `announce` (duck/pause the current media, play the alert, auto-resume); other players (BrowserMod, Squeezelite, …) snapshot their volume + current media, play the alert, then restore and resume it. Stopping is **immediate** (`media_stop`, not at the end of the current loop), and players are always returned to their prior state. Repeating alarm/timer sounds now loop for the **actual length of the sound** — announce players re-announce each length; others loop natively via `repeat_set` — so the old fixed 6-second interval and `*_alert_max_repeats` settings are gone (repeat is now just an on/off flag, bounded by the notification's timeout). Sound length is read with `mutagen`.
- **One place drives every sound** — sound-triggering is centralized in the notification pipeline and mapped by **source + severity**, so alarms/timers use their own alert sounds while everything else uses the per-severity notification sound. New per-severity notification sounds: `notification_sound_info` / `_success` / `_warning` / `_danger` / `_tip` (each falls back to the general `notification_sound`).
- **Notification persistence** — the `notify` service's `sticky` boolean is replaced by a `persistence` field: **`transient`** (toast/sound only, never stored), **`normal`** (stored, auto-cleared when read/dismissed), or **`sticky`** (stored, marked read on interaction and kept until cleared). Pairs with Ted's Cards v1.0.72+.

### v1.0.12

- **Playback falls back to a device's own media player** — `register_device` now accepts a `media_player`, and the playback engine uses it when no per-device or global media player is set, so a device plays alerts on its own client speaker by default. Pairs with Ted's Cards v1.0.71+.

### v1.0.11

- **Settings system + sound playback** — a backend settings store (**global** baseline + **per-device** overrides) with services (`set_setting`, `clear_setting`, `register_device`), a `teds_cards_backend/subscribe_settings` WebSocket command, a device registry, and **`sensor.teds_settings`**. A server-side **playback engine** plays alert sounds for finished timers / ringing alarms / notifications on the firing area's devices' media players (deduped, DND-aware), repeating up to the configured max and cancelling on dismiss. Snooze is now a client-resolved payload on completion notifications (each device uses its own snooze settings). Bundled default sounds are served from `/teds_cards_backend/sounds/` (drop `timer.mp3` / `alarm.mp3` / `notification.mp3` in the `sounds/` folder, or set a custom URL in settings). Pairs with Ted's Cards v1.0.69+.

### v1.0.10

- **Snooze / Dismiss buttons on completion notifications** — finished-timer and ringing-alarm notifications now include action buttons: **Snooze (1min)** / **Dismiss** for timers and **Snooze (9min)** / **Dismiss** for alarms. Snooze starts a new timer for the snooze duration, keeping the original name and room (so alarm snoozes stay area-scoped). Pairs with Ted's Cards v1.0.34+.

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
