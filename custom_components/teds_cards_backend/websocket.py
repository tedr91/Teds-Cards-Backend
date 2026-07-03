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

from .const import DOMAIN, EVENT_NOTIFICATION

_REGISTERED = f"{DOMAIN}_ws_registered"


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the WebSocket commands once."""
    if hass.data.get(_REGISTERED):
        return
    websocket_api.async_register_command(hass, handle_subscribe_notifications)
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
