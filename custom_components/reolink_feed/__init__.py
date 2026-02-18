"""Reolink feed integration."""

from __future__ import annotations

from dataclasses import dataclass

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .feed import ReolinkFeedManager


@dataclass(slots=True)
class ReolinkFeedData:
    """Runtime data stored on the config entry."""

    manager: ReolinkFeedManager


ReolinkFeedConfigEntry = ConfigEntry[ReolinkFeedData]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up integration from YAML (none)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ReolinkFeedConfigEntry) -> bool:
    """Set up Reolink feed from a config entry."""
    manager = ReolinkFeedManager(hass)
    await manager.async_start()
    entry.runtime_data = ReolinkFeedData(manager=manager)
    _async_register_ws_commands(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ReolinkFeedConfigEntry) -> bool:
    """Unload the config entry."""
    await entry.runtime_data.manager.async_stop()
    return True


@callback
def _async_register_ws_commands(hass: HomeAssistant) -> None:
    if hass.data.get(f"{DOMAIN}_ws_registered"):
        return
    websocket_api.async_register_command(hass, ws_list_items)
    hass.data[f"{DOMAIN}_ws_registered"] = True


@websocket_api.websocket_command(
    {
        "type": "reolink_feed/list",
        vol.Optional("since_hours", default=24): cv.positive_int,
        vol.Optional("limit", default=200): cv.positive_int,
        vol.Optional("labels", default=["person", "animal"]): [cv.string],
    }
)
@websocket_api.async_response
async def ws_list_items(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Return timeline items newest-first."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], "not_loaded", "reolink_feed is not loaded")
        return

    entry: ReolinkFeedConfigEntry = entries[0]
    items = entry.runtime_data.manager.get_items()

    since_hours = msg["since_hours"]
    limit = msg["limit"]
    labels = set(msg["labels"])

    now_ts = dt_util.utcnow().timestamp()
    since_seconds = since_hours * 3600
    filtered = []
    for item in items:
        if item.label not in labels:
            continue
        age_seconds = now_ts - item.start_dt.timestamp()
        if age_seconds <= since_seconds:
            filtered.append(item.as_dict())
        if len(filtered) >= limit:
            break

    connection.send_result(msg["id"], {"items": filtered})
