"""Reolink feed integration."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
import json
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import voluptuous as vol

from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import (
    CONF_RESOURCE_TYPE_WS,
    CONF_URL,
    LOVELACE_DATA,
)
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ENTITY_ID, CONF_ID, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    CARD_FILENAME,
    CARD_URL_PATH,
    CONF_ENABLED_LABELS,
    CONF_MAX_DETECTIONS,
    CONF_MAX_STORAGE_GB,
    CONF_RETENTION_HOURS,
    DEFAULT_RETENTION_HOURS,
    DEFAULT_MAX_STORAGE_GB,
    DEFAULT_MAX_DETECTIONS,
    DEFAULT_ENABLED_DETECTION_LABELS,
    DOMAIN,
    LEGACY_LABEL_ALIASES,
    LIST_ITEMS_LIMIT,
    MAX_MAX_DETECTIONS,
    MAX_MAX_STORAGE_GB,
    MAX_RETENTION_HOURS,
    MIN_MAX_DETECTIONS,
    MIN_MAX_STORAGE_GB,
    MIN_RETENTION_HOURS,
    SUPPORTED_DETECTION_LABELS,
    normalize_detection_label,
)
from .feed import ReolinkFeedManager

_LOGGER = logging.getLogger(__name__)
_LOCAL_CARD_URL_PATH = "/local/reolink-feed-card.js"
_INTEGRATION_VERSION_CACHE_KEY = f"{DOMAIN}_integration_version"


@dataclass(slots=True)
class ReolinkFeedData:
    """Runtime data stored on the config entry."""

    manager: ReolinkFeedManager
    options_unsub: Callable[[], None] | None = None


ReolinkFeedConfigEntry = ConfigEntry[ReolinkFeedData]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up integration from YAML (none)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ReolinkFeedConfigEntry) -> bool:
    """Set up Reolink feed from a config entry."""
    manager = ReolinkFeedManager(
        hass,
        _enabled_labels_from_entry(entry),
        _retention_hours_from_entry(entry),
        _max_detections_from_entry(entry),
        _max_storage_gb_from_entry(entry),
    )
    await manager.async_start()
    options_unsub = entry.add_update_listener(_async_reload_entry_on_update)
    entry.runtime_data = ReolinkFeedData(manager=manager, options_unsub=options_unsub)
    await _async_register_card_resource(hass)
    await _async_ensure_lovelace_card_resource(hass)
    _async_register_ws_commands(hass)
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ReolinkFeedConfigEntry) -> bool:
    """Unload the config entry."""
    if entry.runtime_data.options_unsub:
        entry.runtime_data.options_unsub()
    await entry.runtime_data.manager.async_stop()
    if not hass.config_entries.async_entries(DOMAIN):
        hass.services.async_remove(DOMAIN, "mock_detection")
    return True


async def _async_reload_entry_on_update(hass: HomeAssistant, entry: ReolinkFeedConfigEntry) -> None:
    """Reload config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_register_card_resource(hass: HomeAssistant) -> None:
    """Expose the bundled Lovelace card JavaScript as a static URL."""
    if hass.data.get(f"{DOMAIN}_card_registered"):
        return

    card_file = Path(__file__).parent / "frontend" / CARD_FILENAME
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_PATH, str(card_file), cache_headers=False)]
    )
    hass.data[f"{DOMAIN}_card_registered"] = True


async def _async_ensure_lovelace_card_resource(hass: HomeAssistant) -> None:
    """Ensure Lovelace storage resources include the bundled card."""
    if not await _async_try_add_lovelace_card_resource(hass):
        @callback
        def _on_started(_event) -> None:
            hass.async_create_task(_async_try_add_lovelace_card_resource(hass))

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_started)


async def _async_try_add_lovelace_card_resource(hass: HomeAssistant) -> bool:
    """Add the card resource in Lovelace storage mode if needed."""
    lovelace_data = hass.data.get(LOVELACE_DATA)
    if lovelace_data is None:
        return False

    resources = lovelace_data.resources
    if not isinstance(resources, ResourceStorageCollection):
        _LOGGER.debug("Lovelace is not in storage resource mode; skipping card resource auto-add")
        return True

    await resources.async_get_info()
    integration_version = await _async_integration_version(hass)
    target_url = _card_resource_url(integration_version)
    existing_card_item: dict | None = None
    for item in resources.async_items() or []:
        url = str(item.get(CONF_URL, ""))
        base_url = url.split("?", 1)[0]
        if base_url == _LOCAL_CARD_URL_PATH:
            _LOGGER.debug("Local dev card resource detected; skipping auto-add for %s", CARD_URL_PATH)
            return True
        if base_url == CARD_URL_PATH:
            existing_card_item = item
            break

    if existing_card_item is None:
        await resources.async_create_item(
            {CONF_RESOURCE_TYPE_WS: "module", CONF_URL: target_url}
        )
        _LOGGER.info("Added Lovelace resource for Reolink Feed card: %s", target_url)
        return True

    if _resource_version_from_url(str(existing_card_item.get(CONF_URL, ""))) == integration_version:
        return True

    item_id = existing_card_item.get(CONF_ID)
    if not isinstance(item_id, str) or not item_id:
        return True
    await resources.async_update_item(item_id, {CONF_URL: target_url})
    _LOGGER.info("Updated Lovelace resource for Reolink Feed card: %s", target_url)
    return True


def _integration_version() -> str:
    manifest_path = Path(__file__).parent / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return "dev"
    version = manifest.get("version")
    return str(version) if version else "dev"


async def _async_integration_version(hass: HomeAssistant) -> str:
    if cached := hass.data.get(_INTEGRATION_VERSION_CACHE_KEY):
        return str(cached)
    version = await hass.async_add_executor_job(_integration_version)
    hass.data[_INTEGRATION_VERSION_CACHE_KEY] = version
    return version


def _card_resource_url(version: str) -> str:
    return f"{CARD_URL_PATH}?v={version}"


def _resource_version_from_url(url: str) -> str | None:
    query = parse_qs(urlsplit(url).query)
    value = query.get("v")
    if not value:
        return None
    return value[0]


@callback
def _async_register_ws_commands(hass: HomeAssistant) -> None:
    if hass.data.get(f"{DOMAIN}_ws_registered"):
        return
    websocket_api.async_register_command(hass, ws_list_items)
    websocket_api.async_register_command(hass, ws_resolve_recording)
    websocket_api.async_register_command(hass, ws_rebuild_from_history)
    websocket_api.async_register_command(hass, ws_delete_item)
    hass.data[f"{DOMAIN}_ws_registered"] = True


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "mock_detection"):
        return

    async def _handle_mock_detection(call) -> None:
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            return
        entry: ReolinkFeedConfigEntry = entries[0]
        await entry.runtime_data.manager.async_create_mock_detection(
            source_entity_id=call.data[CONF_ENTITY_ID],
            camera_name=call.data["camera_name"],
            label=normalize_detection_label(call.data["label"]),
            duration_s=call.data["duration_s"],
            create_dummy_snapshot=call.data["create_dummy_snapshot"],
        )

    hass.services.async_register(
        DOMAIN,
        "mock_detection",
        _handle_mock_detection,
        schema=vol.Schema(
            {
                vol.Required(CONF_ENTITY_ID): cv.entity_id,
                vol.Required("camera_name"): cv.string,
                vol.Optional("label", default="person"): vol.In(
                    list(SUPPORTED_DETECTION_LABELS) + list(LEGACY_LABEL_ALIASES)
                ),
                vol.Optional("duration_s", default=8): cv.positive_int,
                vol.Optional("create_dummy_snapshot", default=True): cv.boolean,
            }
        ),
    )


@websocket_api.websocket_command(
    {
        "type": "reolink_feed/list",
        vol.Optional("labels"): [cv.string],
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
    await entry.runtime_data.manager.async_migrate_legacy_snapshot_urls()
    await entry.runtime_data.manager.async_prune_expired_items()
    items = entry.runtime_data.manager.get_items()

    enabled_labels = entry.runtime_data.manager.get_enabled_labels()
    requested = {
        normalize_detection_label(label)
        for label in (msg.get("labels") or [])
        if normalize_detection_label(label) in SUPPORTED_DETECTION_LABELS
    }
    labels = requested or enabled_labels

    now_ts = dt_util.utcnow().timestamp()
    since_seconds = entry.runtime_data.manager.get_retention_hours() * 3600
    filtered = []
    for item in items:
        if item.label not in labels:
            continue
        age_seconds = now_ts - item.start_dt.timestamp()
        if age_seconds <= since_seconds:
            filtered.append(item.as_dict())
        if len(filtered) >= LIST_ITEMS_LIMIT:
            break

    connection.send_result(
        msg["id"],
        {
            "items": filtered,
            "enabled_labels": sorted(enabled_labels),
            "retention_hours": entry.runtime_data.manager.get_retention_hours(),
        },
    )


@websocket_api.websocket_command(
    {
        "type": "reolink_feed/resolve_recording",
        vol.Required("item_id"): cv.string,
        vol.Optional("final_attempt", default=False): cv.boolean,
    }
)
@websocket_api.async_response
async def ws_resolve_recording(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Resolve a recording for one item."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], "not_loaded", "reolink_feed is not loaded")
        return

    entry: ReolinkFeedConfigEntry = entries[0]
    try:
        recording = await entry.runtime_data.manager.async_resolve_recording(
            msg["item_id"], final_attempt=msg["final_attempt"]
        )
    except ValueError:
        connection.send_error(msg["id"], "not_found", "item id not found")
        return

    connection.send_result(msg["id"], recording)


@websocket_api.websocket_command(
    {
        "type": "reolink_feed/rebuild_from_history",
        vol.Optional("per_entity_changes", default=400): cv.positive_int,
    }
)
@websocket_api.async_response
async def ws_rebuild_from_history(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Rebuild feed items from recorder history for Reolink sensors."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], "not_loaded", "reolink_feed is not loaded")
        return

    entry: ReolinkFeedConfigEntry = entries[0]
    try:
        result = await entry.runtime_data.manager.async_rebuild_from_history(
            per_entity_changes=msg["per_entity_changes"],
        )
    except RuntimeError as err:
        connection.send_error(msg["id"], "rebuild_failed", str(err))
        return

    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        "type": "reolink_feed/delete_item",
        vol.Required("item_id"): cv.string,
    }
)
@websocket_api.async_response
async def ws_delete_item(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Delete one feed item and its snapshot."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], "not_loaded", "reolink_feed is not loaded")
        return

    entry: ReolinkFeedConfigEntry = entries[0]
    try:
        await entry.runtime_data.manager.async_delete_item(msg["item_id"])
    except ValueError:
        connection.send_error(msg["id"], "not_found", "item id not found")
        return

    connection.send_result(msg["id"], {"ok": True})


def _enabled_labels_from_entry(entry: ReolinkFeedConfigEntry) -> set[str]:
    raw = entry.options.get(CONF_ENABLED_LABELS)
    selected: list[str]
    if isinstance(raw, list):
        selected = [normalize_detection_label(value) for value in raw]
    else:
        selected = list(DEFAULT_ENABLED_DETECTION_LABELS)
    normalized = {value for value in selected if value in SUPPORTED_DETECTION_LABELS}
    if not normalized:
        return set(DEFAULT_ENABLED_DETECTION_LABELS)
    return normalized


def _retention_hours_from_entry(entry: ReolinkFeedConfigEntry) -> int:
    raw = entry.options.get(CONF_RETENTION_HOURS, DEFAULT_RETENTION_HOURS)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_RETENTION_HOURS
    return max(MIN_RETENTION_HOURS, min(MAX_RETENTION_HOURS, value))


def _max_detections_from_entry(entry: ReolinkFeedConfigEntry) -> int:
    raw = entry.options.get(CONF_MAX_DETECTIONS, DEFAULT_MAX_DETECTIONS)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_MAX_DETECTIONS
    return max(MIN_MAX_DETECTIONS, min(MAX_MAX_DETECTIONS, value))


def _max_storage_gb_from_entry(entry: ReolinkFeedConfigEntry) -> float:
    raw = entry.options.get(CONF_MAX_STORAGE_GB, DEFAULT_MAX_STORAGE_GB)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_MAX_STORAGE_GB
    return max(MIN_MAX_STORAGE_GB, min(MAX_MAX_STORAGE_GB, value))
