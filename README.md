# Ted's Cards Backend

Home Assistant integration backing **Ted's Cards** alarms & timers — so the Alarm and Timer cards can add/manage them without you creating helpers by hand.

- **Alarms** — label, description, time, repeat days, enabled; fires a `teds_cards_backend_alarm_ringing` event you can automate.
- **Timers** — countdown (h/m/s) + name, view/cancel in-progress, last 5 recent for one-tap restart; fires `teds_cards_backend_timer_finished`.

Install via HACS (custom repository, category **Integration**), restart, then add **Ted's Cards Backend** from Settings → Devices & Services. Pair with the cards in [Teds-Cards](https://github.com/tedr91/Teds-Cards).
