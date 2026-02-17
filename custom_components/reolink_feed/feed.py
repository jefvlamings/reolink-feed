"""Event tracking and in-memory feed state."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
import uuid

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import UNDEFINED

from .const import MAX_ITEMS, MERGE_WINDOW_SECONDS, SUPPORTED_SUFFIX_TO_LABEL
from .models import DetectionItem
from .storage import DetectionStore

_LOGGER = logging.getLogger(__name__)


class ReolinkFeedManager:
    """Manage detection lifecycle and persistence."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = DetectionStore(hass)
        self._items: list[DetectionItem] = []
        self._open_item_id_by_key: dict[tuple[str, str], str] = {}
        self._last_closed_item_id_by_key: dict[tuple[str, str], str] = {}
        self._unsub_state_changed: Callable[[], None] | None = None
        self._unsub_delayed_save: Callable[[], None] | None = None

    async def async_start(self) -> None:
        """Load initial state and begin listening."""
        self._items = await self._store.async_load()
        self._rebuild_indexes()
        self._unsub_state_changed = self.hass.bus.async_listen(
            EVENT_STATE_CHANGED, self._async_handle_state_changed
        )

    async def async_stop(self) -> None:
        """Stop listeners and flush any pending save."""
        if self._unsub_state_changed:
            self._unsub_state_changed()
            self._unsub_state_changed = None
        if self._unsub_delayed_save:
            self._unsub_delayed_save()
            self._unsub_delayed_save = None
        await self._store.async_save(self._items)

    def get_items(self) -> list[DetectionItem]:
        """Return newest-first feed items."""
        return self._items

    def _rebuild_indexes(self) -> None:
        self._open_item_id_by_key.clear()
        self._last_closed_item_id_by_key.clear()

        for item in self._items:
            key = (item.camera_name, item.label)
            if item.end_ts is None:
                self._open_item_id_by_key[key] = item.id
            elif key not in self._last_closed_item_id_by_key:
                self._last_closed_item_id_by_key[key] = item.id

    @callback
    def _async_handle_state_changed(self, event: Event[EventStateChangedData]) -> None:
        data = event.data
        entity_id = data["entity_id"]
        if not entity_id.startswith("binary_sensor."):
            return

        label = None
        for suffix, mapped_label in SUPPORTED_SUFFIX_TO_LABEL.items():
            if entity_id.endswith(suffix):
                label = mapped_label
                break
        if label is None:
            return

        old_state = data.get("old_state")
        new_state = data.get("new_state")
        if old_state is None or new_state is None:
            return

        from_state = old_state.state if old_state.state is not UNDEFINED else None
        to_state = new_state.state if new_state.state is not UNDEFINED else None

        camera_name = _camera_name_from_state(entity_id, new_state.name)
        key = (camera_name, label)
        fired_at = event.time_fired

        if from_state == "off" and to_state == "on":
            self._handle_detection_start(key, entity_id, camera_name, label, fired_at)
            return

        if from_state == "on" and to_state == "off":
            self._handle_detection_end(key, fired_at)

    def _handle_detection_start(
        self,
        key: tuple[str, str],
        entity_id: str,
        camera_name: str,
        label: str,
        fired_at: datetime,
    ) -> None:
        if key in self._open_item_id_by_key:
            return

        last_closed = self._get_item_by_id(self._last_closed_item_id_by_key.get(key))
        if (
            last_closed is not None
            and last_closed.end_dt is not None
            and fired_at - last_closed.end_dt <= timedelta(seconds=MERGE_WINDOW_SECONDS)
        ):
            last_closed.end_ts = None
            last_closed.duration_s = None
            last_closed.recording = {"status": "pending"}
            self._open_item_id_by_key[key] = last_closed.id
            self._last_closed_item_id_by_key.pop(key, None)
            self._schedule_save()
            return

        item = DetectionItem(
            id=str(uuid.uuid4()),
            start_ts=fired_at.isoformat(),
            end_ts=None,
            duration_s=None,
            label=label,
            source_entity_id=entity_id,
            camera_name=camera_name,
            snapshot_url=None,
            recording={"status": "pending"},
        )
        self._items.insert(0, item)
        self._open_item_id_by_key[key] = item.id
        if len(self._items) > MAX_ITEMS:
            self._items = self._items[:MAX_ITEMS]
        self._schedule_save()

    def _handle_detection_end(self, key: tuple[str, str], fired_at: datetime) -> None:
        item = self._get_item_by_id(self._open_item_id_by_key.get(key))
        if item is None:
            return
        item.end_ts = fired_at.isoformat()
        item.duration_s = max(0, int((fired_at - item.start_dt).total_seconds()))
        self._open_item_id_by_key.pop(key, None)
        self._last_closed_item_id_by_key[key] = item.id
        self._schedule_save()

    def _get_item_by_id(self, item_id: str | None) -> DetectionItem | None:
        if not item_id:
            return None
        for item in self._items:
            if item.id == item_id:
                return item
        return None

    def _schedule_save(self) -> None:
        if self._unsub_delayed_save is not None:
            self._unsub_delayed_save()
            self._unsub_delayed_save = None

        @callback
        def _save_callback(_now: datetime) -> None:
            self._unsub_delayed_save = None
            self.hass.async_create_task(self._store.async_save(self._items))

        self._unsub_delayed_save = async_call_later(self.hass, 1.0, _save_callback)


def _camera_name_from_state(entity_id: str, friendly_name: str | None) -> str:
    if friendly_name:
        normalized = friendly_name.strip()
        for suffix in (" persoon", " dier", " person", " animal"):
            if normalized.lower().endswith(suffix):
                return normalized[: -len(suffix)].strip()
        return normalized

    object_id = entity_id.split(".", 1)[1]
    for suffix in SUPPORTED_SUFFIX_TO_LABEL:
        if object_id.endswith(suffix):
            object_id = object_id[: -len(suffix)]
            break
    return object_id.replace("_", " ").strip().title()
