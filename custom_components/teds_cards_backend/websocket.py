"""WebSocket API for Ted's Cards Backend.

Non-admin HA users (e.g. kiosk / Wallpanel users) are not allowed to
`subscribe_events` for custom event types, so cards cannot listen to
`teds_cards_backend_notification` directly. This command lets any authenticated
user subscribe to notifications via a dedicated, non-admin command instead.
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_NOTIFICATION, EVENT_SETTINGS

_REGISTERED = f"{DOMAIN}_ws_registered"


def _manager(hass: HomeAssistant):
    """The single TedsManager for the integration (first config entry)."""
    return next(iter((hass.data.get(DOMAIN) or {}).values()), None)


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the WebSocket commands once."""
    if hass.data.get(_REGISTERED):
        return
    websocket_api.async_register_command(hass, handle_subscribe_notifications)
    websocket_api.async_register_command(hass, handle_subscribe_settings)
    websocket_api.async_register_command(hass, handle_register_device)
    hass.data[_REGISTERED] = True


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/subscribe_notifications"}
)
@callback
def handle_subscribe_notifications(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Forward backend notification events to the subscribing connection."""

    @callback
    def forward(event: Event) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], event.data))

    connection.subscriptions[msg["id"]] = hass.bus.async_listen(
        EVENT_NOTIFICATION, forward
    )
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/subscribe_settings"}
)
@callback
def handle_subscribe_settings(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Push the current settings snapshot, then forward settings updates."""

    @callback
    def forward(event: Event) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], event.data))

    connection.subscriptions[msg["id"]] = hass.bus.async_listen(EVENT_SETTINGS, forward)
    connection.send_result(msg["id"])
    mgr = _manager(hass)
    if mgr:
        connection.send_message(
            websocket_api.event_message(msg["id"], mgr.settings_payload())
        )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/register_device",
        vol.Required("device_id"): str,
        vol.Optional("area"): vol.Any(None, str),
        vol.Optional("name"): vol.Any(None, str),
        vol.Optional("media_player"): vol.Any(None, str),
    }
)
@websocket_api.async_response
async def handle_register_device(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Let a (non-admin) device register its id + area for settings targeting."""
    mgr = _manager(hass)
    if mgr:
        await mgr.register_device(
            msg["device_id"], msg.get("area"), msg.get("name"), msg.get("media_player")
        )
    connection.send_result(msg["id"])

