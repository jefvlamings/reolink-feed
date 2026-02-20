"""Event tracking and in-memory feed state."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, timedelta, tzinfo
import logging
from pathlib import Path
import re
import shutil
from typing import Any
import uuid

from aiohttp import ClientError
from homeassistant.components.camera import async_get_image
from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.media_source import async_resolve_media
from homeassistant.components.media_player import BrowseError
from homeassistant.components.media_source import async_browse_media
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.typing import UNDEFINED
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import (
    CLEANUP_INTERVAL_SECONDS,
    DEFAULT_ENABLED_DETECTION_LABELS,
    DEFAULT_MAX_DETECTIONS,
    DEFAULT_MAX_STORAGE_GB,
    DEFAULT_RETENTION_HOURS,
    MAX_MAX_DETECTIONS,
    MAX_MAX_STORAGE_GB,
    MAX_RETENTION_HOURS,
    MERGE_WINDOW_SECONDS,
    MIN_MAX_DETECTIONS,
    MIN_MAX_STORAGE_GB,
    MIN_RETENTION_HOURS,
    RECORDING_DEFAULT_CLIP_DURATION_SECONDS,
    RECORDING_RETRY_DELAYS_SECONDS,
    RECORDING_WINDOW_END_PAD_SECONDS,
    RECORDING_WINDOW_START_PAD_SECONDS,
    SNAPSHOT_DELAY_SECONDS,
    SUPPORTED_DETECTION_LABELS,
    SUPPORTED_SUFFIX_TO_LABEL,
    normalize_detection_label,
)
from .models import DetectionItem
from .storage import DetectionStore

_LOGGER = logging.getLogger(__name__)
_CLIP_TITLE_PATTERN = re.compile(
    r"^(?P<start>\d{1,2}:\d{2}:\d{2})(?:\s+(?P<duration>\d+:\d{2}:\d{2}))?"
)
_CACHE_DOWNLOAD_RETRY_DELAYS_SECONDS: tuple[float, ...] = (0.0, 2.0, 5.0)


class ReolinkFeedManager:
    """Manage detection lifecycle and persistence."""

    def __init__(
        self,
        hass: HomeAssistant,
        enabled_labels: set[str] | None = None,
        retention_hours: int | None = None,
        max_detections: int | None = None,
        max_storage_gb: float | None = None,
    ) -> None:
        self.hass = hass
        self._store = DetectionStore(hass)
        self._www_root = Path(hass.config.path("www"))
        self._items: list[DetectionItem] = []
        self._item_by_id: dict[str, DetectionItem] = {}
        self._open_item_id_by_key: dict[tuple[str, str], str] = {}
        self._last_closed_item_id_by_key: dict[tuple[str, str], str] = {}
        self._asset_size_by_item_id: dict[str, int] = {}
        self._total_asset_size_bytes = 0
        self._snapshot_camera_by_sensor: dict[str, str | None] = {}
        self._label_by_sensor: dict[str, str | None] = {}
        self._unsub_snapshot_timers: dict[str, Callable[[], None]] = {}
        self._unsub_recording_timers: dict[str, list[Callable[[], None]]] = {}
        self._unsub_state_changed: Callable[[], None] | None = None
        self._unsub_delayed_save: Callable[[], None] | None = None
        self._unsub_cleanup_timer: Callable[[], None] | None = None
        self._enabled_labels = self._normalize_enabled_labels(enabled_labels)
        self._retention_hours = self._normalize_retention_hours(retention_hours)
        self._max_detections = self._normalize_max_detections(max_detections)
        self._max_storage_gb = self._normalize_max_storage_gb(max_storage_gb)
        self._max_storage_bytes = int(self._max_storage_gb * 1024 * 1024 * 1024)

    async def async_start(self) -> None:
        """Load initial state and begin listening."""
        self._items = await self._store.async_load()
        changed = self._normalize_item_labels()
        if self._normalize_item_recordings():
            changed = True
        if await self._async_migrate_snapshot_paths():
            changed = True
        if await self.async_prune_expired_items():
            changed = True
        trimmed = await self._async_trim_to_max_detections()
        if trimmed:
            changed = True
        await self._async_sync_storage_usage_index()
        storage_trimmed = await self._async_enforce_storage_limit()
        if storage_trimmed:
            changed = True
        self._rebuild_indexes()
        self._unsub_state_changed = self.hass.bus.async_listen(
            EVENT_STATE_CHANGED, self._async_handle_state_changed
        )
        self._schedule_cleanup()
        if changed:
            await self._store.async_save(self._items)

    def _trim_to_max_detections(self) -> list[DetectionItem]:
        if len(self._items) <= self._max_detections:
            return []
        removed = self._items[self._max_detections :]
        self._items = self._items[: self._max_detections]
        return removed

    async def _async_trim_to_max_detections(self) -> int:
        removed = self._trim_to_max_detections()
        if not removed:
            return 0
        removed_count = await self._async_remove_items(removed)
        _LOGGER.info(
            "Trimmed %s reolink feed items above max_detections=%s",
            removed_count,
            self._max_detections,
        )
        return removed_count

    async def _async_enforce_storage_limit(self) -> int:
        if self._max_storage_bytes <= 0 or not self._items:
            return 0

        await self._async_sync_storage_usage_index()
        total_size = self._total_asset_size_bytes

        if total_size <= self._max_storage_bytes:
            return 0

        kept = list(self._items)
        removed: list[DetectionItem] = []
        while total_size > self._max_storage_bytes and kept:
            candidate = kept.pop()  # oldest-first removal
            removed.append(candidate)
            total_size -= self._asset_size_by_item_id.get(candidate.id, 0)

        if not removed:
            return 0

        self._items = kept
        removed_count = await self._async_remove_items(removed)
        _LOGGER.info(
            "Trimmed %s reolink feed items to enforce max_storage_gb=%s",
            removed_count,
            self._max_storage_gb,
        )
        return removed_count

    async def _async_sync_storage_usage_index(self) -> None:
        """Sync cached asset sizes for current items without full rescans."""
        current_ids = {item.id for item in self._items}
        for item_id in list(self._asset_size_by_item_id):
            if item_id in current_ids:
                continue
            self._total_asset_size_bytes -= self._asset_size_by_item_id.pop(item_id, 0)
        if self._total_asset_size_bytes < 0:
            self._total_asset_size_bytes = 0

        for item in self._items:
            if item.id in self._asset_size_by_item_id:
                continue
            paths = [
                *_snapshot_paths_for_item(self.hass, self._www_root, item),
                *_cached_recording_paths_for_item(self._www_root, item),
            ]
            item_size = await self.hass.async_add_executor_job(_sum_existing_file_sizes, paths)
            self._asset_size_by_item_id[item.id] = item_size
            self._total_asset_size_bytes += item_size

    async def _async_refresh_item_asset_size(self, item: DetectionItem) -> int:
        """Refresh one item's cached asset size and return the latest size."""
        paths = [
            *_snapshot_paths_for_item(self.hass, self._www_root, item),
            *_cached_recording_paths_for_item(self._www_root, item),
        ]
        item_size = await self.hass.async_add_executor_job(_sum_existing_file_sizes, paths)
        previous = self._asset_size_by_item_id.get(item.id, 0)
        self._asset_size_by_item_id[item.id] = item_size
        self._total_asset_size_bytes += item_size - previous
        if self._total_asset_size_bytes < 0:
            self._total_asset_size_bytes = 0
        return item_size

    async def _async_remove_items(
        self, items: list[DetectionItem], *, save: bool = True
    ) -> int:
        """Remove items, clean up timers/assets, rebuild indexes, optionally persist."""
        if not items:
            return 0

        unique_items: list[DetectionItem] = []
        seen_ids: set[str] = set()
        for item in items:
            if item.id in seen_ids:
                continue
            seen_ids.add(item.id)
            unique_items.append(item)

        for item_id in seen_ids:
            self._cancel_recording_resolution(item_id)
            snapshot_unsub = self._unsub_snapshot_timers.pop(item_id, None)
            if snapshot_unsub:
                snapshot_unsub()
            self._total_asset_size_bytes -= self._asset_size_by_item_id.pop(item_id, 0)
        if self._total_asset_size_bytes < 0:
            self._total_asset_size_bytes = 0

        self._items = [item for item in self._items if item.id not in seen_ids]
        await self._async_delete_snapshots_for_items(unique_items)
        self._rebuild_indexes()
        if save:
            await self._store.async_save(self._items)
        return len(unique_items)

    def _normalize_item_labels(self) -> bool:
        changed = False
        for item in self._items:
            normalized = normalize_detection_label(item.label)
            if normalized != item.label:
                item.label = normalized
                changed = True
        return changed

    def _normalize_item_recordings(self) -> bool:
        changed = False
        for item in self._items:
            recording = item.recording or {}
            if "media_content_id" in recording:
                recording = dict(recording)
                recording.pop("media_content_id", None)
                item.recording = recording
                changed = True
        return changed

    async def async_stop(self) -> None:
        """Stop listeners and flush any pending save."""
        if self._unsub_state_changed:
            self._unsub_state_changed()
            self._unsub_state_changed = None
        if self._unsub_delayed_save:
            self._unsub_delayed_save()
            self._unsub_delayed_save = None
        if self._unsub_cleanup_timer:
            self._unsub_cleanup_timer()
            self._unsub_cleanup_timer = None
        for unsub in self._unsub_snapshot_timers.values():
            unsub()
        self._unsub_snapshot_timers.clear()
        for timer_unsubs in self._unsub_recording_timers.values():
            for unsub in timer_unsubs:
                unsub()
        self._unsub_recording_timers.clear()
        await self._store.async_save(self._items)

    def get_items(self) -> list[DetectionItem]:
        """Return newest-first feed items."""
        return self._items

    def get_enabled_labels(self) -> set[str]:
        """Return backend-enabled detection labels."""
        return set(self._enabled_labels)

    def get_retention_hours(self) -> int:
        """Return retention window in hours."""
        return self._retention_hours

    def get_max_detections(self) -> int:
        """Return max retained detections."""
        return self._max_detections

    def get_max_storage_gb(self) -> float:
        """Return max local asset storage in GB."""
        return self._max_storage_gb

    def _normalize_enabled_labels(self, enabled_labels: set[str] | None) -> set[str]:
        if not enabled_labels:
            return set(DEFAULT_ENABLED_DETECTION_LABELS)
        normalized = {normalize_detection_label(label) for label in enabled_labels}
        normalized = {label for label in normalized if label in SUPPORTED_DETECTION_LABELS}
        return normalized or set(DEFAULT_ENABLED_DETECTION_LABELS)

    def _normalize_retention_hours(self, retention_hours: int | None) -> int:
        if retention_hours is None:
            return DEFAULT_RETENTION_HOURS
        try:
            value = int(retention_hours)
        except (TypeError, ValueError):
            value = DEFAULT_RETENTION_HOURS
        return max(MIN_RETENTION_HOURS, min(MAX_RETENTION_HOURS, value))

    def _normalize_max_detections(self, max_detections: int | None) -> int:
        if max_detections is None:
            return DEFAULT_MAX_DETECTIONS
        try:
            value = int(max_detections)
        except (TypeError, ValueError):
            value = DEFAULT_MAX_DETECTIONS
        return max(MIN_MAX_DETECTIONS, min(MAX_MAX_DETECTIONS, value))

    def _normalize_max_storage_gb(self, max_storage_gb: float | None) -> float:
        if max_storage_gb is None:
            return float(DEFAULT_MAX_STORAGE_GB)
        try:
            value = float(max_storage_gb)
        except (TypeError, ValueError):
            value = float(DEFAULT_MAX_STORAGE_GB)
        return max(MIN_MAX_STORAGE_GB, min(MAX_MAX_STORAGE_GB, value))

    def _label_is_enabled(self, label: str | None) -> bool:
        return normalize_detection_label(label) in self._enabled_labels

    async def async_migrate_legacy_snapshot_urls(self) -> bool:
        """Public migration trigger for legacy snapshot URLs."""
        changed = await self._async_migrate_snapshot_paths()
        if changed:
            await self._store.async_save(self._items)
        return changed

    async def async_enforce_storage_limit(self) -> int:
        """Public trigger for local storage limit enforcement."""
        return await self._async_enforce_storage_limit()

    async def async_prune_expired_items(self) -> int:
        """Drop items older than retention window and remove local assets."""
        cutoff = dt_util.utcnow() - timedelta(hours=self._retention_hours)
        kept: list[DetectionItem] = []
        removed: list[DetectionItem] = []
        for item in self._items:
            if item.start_dt.astimezone(dt_util.UTC) < cutoff:
                removed.append(item)
            else:
                kept.append(item)

        if not removed:
            return 0

        self._items = kept
        removed_count = await self._async_remove_items(removed)
        _LOGGER.info("Pruned %s expired reolink feed items", removed_count)
        return removed_count

    async def async_delete_item(self, item_id: str) -> None:
        """Delete one feed item and local assets."""
        item = self._get_item_by_id(item_id)
        if item is None:
            raise ValueError(f"Unknown item id: {item_id}")

        await self._async_remove_items([item])

    def _schedule_cleanup(self) -> None:
        if self._unsub_cleanup_timer is not None:
            self._unsub_cleanup_timer()
            self._unsub_cleanup_timer = None

        @callback
        def _cleanup_callback(_now: datetime) -> None:
            self._unsub_cleanup_timer = None
            self.hass.async_create_task(self._async_run_scheduled_cleanup())

        self._unsub_cleanup_timer = async_call_later(
            self.hass, float(CLEANUP_INTERVAL_SECONDS), _cleanup_callback
        )

    async def _async_run_scheduled_cleanup(self) -> None:
        try:
            await self.async_prune_expired_items()
            await self._async_enforce_storage_limit()
        finally:
            self._schedule_cleanup()

    async def async_rebuild_from_history(
        self, *, per_entity_changes: int = 400
    ) -> dict[str, int]:
        """Rebuild items from Reolink detection binary sensor history."""
        entity_ids = self._collect_reolink_detection_entities()
        rebuilt = await self._build_items_from_history(entity_ids, per_entity_changes)
        merged, added_count, merged_count, resolve_item_ids = _merge_rebuilt_with_existing_items(
            self._items, rebuilt
        )
        merged.sort(key=lambda item: item.start_dt, reverse=True)
        self._items = merged
        await self._async_trim_to_max_detections()
        self._rebuild_indexes()
        await self._store.async_save(self._items)

        resolvable_ids = {
            item.id
            for item in self._items
            if item.id in resolve_item_ids and item.end_ts is not None
        }
        pending_rebuild_items = [item for item in self._items if item.id in resolvable_ids]
        await self._async_cache_recordings_for_items(pending_rebuild_items)
        await self._store.async_save(self._items)
        return {
            "entity_count": len(entity_ids),
            "item_count": len(self._items),
            "added_count": added_count,
            "merged_count": merged_count,
        }

    def _rebuild_indexes(self) -> None:
        self._item_by_id.clear()
        self._open_item_id_by_key.clear()
        self._last_closed_item_id_by_key.clear()

        for item in self._items:
            self._item_by_id[item.id] = item
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

        label = self._resolve_detection_label(entity_id)
        if label is None or not self._label_is_enabled(label):
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
            self._cancel_recording_resolution(last_closed.id)
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
        self._item_by_id[item.id] = item
        self._open_item_id_by_key[key] = item.id
        removed = self._trim_to_max_detections()
        if removed:
            self._rebuild_indexes()
            self.hass.async_create_task(self._async_remove_items(removed, save=False))
        self._schedule_save()
        self._schedule_snapshot_capture(item.id, entity_id)

    def _handle_detection_end(self, key: tuple[str, str], fired_at: datetime) -> None:
        item = self._get_item_by_id(self._open_item_id_by_key.get(key))
        if item is None:
            return
        item.end_ts = fired_at.isoformat()
        item.duration_s = max(0, int((fired_at - item.start_dt).total_seconds()))
        self._open_item_id_by_key.pop(key, None)
        self._last_closed_item_id_by_key[key] = item.id
        self._schedule_save()
        self._schedule_recording_resolution(item.id)

    def _get_item_by_id(self, item_id: str | None) -> DetectionItem | None:
        if not item_id:
            return None
        return self._item_by_id.get(item_id)

    def _schedule_save(self) -> None:
        if self._unsub_delayed_save is not None:
            self._unsub_delayed_save()
            self._unsub_delayed_save = None

        @callback
        def _save_callback(_now: datetime) -> None:
            self._unsub_delayed_save = None
            self.hass.async_create_task(self._store.async_save(self._items))

        self._unsub_delayed_save = async_call_later(self.hass, 1.0, _save_callback)

    async def async_create_mock_detection(
        self,
        source_entity_id: str,
        camera_name: str,
        label: str,
        duration_s: int = 8,
        create_dummy_snapshot: bool = True,
    ) -> DetectionItem:
        """Create a synthetic detection for local development/testing."""
        normalized_label = normalize_detection_label(label)
        if normalized_label not in SUPPORTED_DETECTION_LABELS:
            raise ValueError(f"Unsupported label: {label}")
        if not self._label_is_enabled(normalized_label):
            raise ValueError(f"Label is disabled by options: {normalized_label}")

        ended = datetime.now().astimezone()
        started = ended - timedelta(seconds=max(1, duration_s))
        item = DetectionItem(
            id=str(uuid.uuid4()),
            start_ts=started.isoformat(),
            end_ts=ended.isoformat(),
            duration_s=max(1, duration_s),
            label=normalized_label,
            source_entity_id=source_entity_id,
            camera_name=camera_name,
            snapshot_url=None,
            recording={"status": "pending"},
        )

        if create_dummy_snapshot:
            snapshot_url = await self._async_write_dummy_snapshot(item)
            item.snapshot_url = snapshot_url

        self._items.insert(0, item)
        await self._async_trim_to_max_detections()
        await self._async_enforce_storage_limit()
        self._last_closed_item_id_by_key[(camera_name, normalized_label)] = item.id
        self._schedule_save()
        self._schedule_recording_resolution(item.id)
        return item

    async def async_resolve_recording(self, item_id: str, *, final_attempt: bool = False) -> dict:
        """Attempt to link an item to a Reolink clip."""
        item = self._get_item_by_id(item_id)
        if item is None:
            raise ValueError(f"Unknown item id: {item_id}")

        recording = item.recording or {"status": "pending"}
        local_url = recording.get("local_url")
        if recording.get("status") == "linked" and isinstance(local_url, str) and local_url:
            return recording

        _LOGGER.debug(
            "Resolving recording for item %s (camera=%s label=%s start=%s end=%s final_attempt=%s)",
            item.id,
            item.camera_name,
            item.label,
            item.start_ts,
            item.end_ts,
            final_attempt,
        )
        media_content_id = await self._async_find_recording_media_content_id(item)
        cache_error: str | None = None
        if media_content_id:
            _LOGGER.debug("Matched media content id for item %s: %s", item.id, media_content_id)
            local_url, cache_error = (
                await self._async_cache_recording_file(item, media_content_id)
                if item.end_ts is not None
                else (None, "open_item")
            )
            if local_url:
                item.recording = {
                    "status": "linked",
                    "local_url": local_url,
                    "resolved_at": datetime.now().astimezone().isoformat(),
                }
                self._cancel_recording_resolution(item.id)
                self._schedule_save()
                _LOGGER.debug("Recording cached for item %s: %s", item.id, local_url)
                return item.recording
            _LOGGER.debug(
                "Failed to cache matched media content for item %s (media_content_id=%s reason=%s)",
                item.id,
                media_content_id,
                cache_error or "-",
            )

        if final_attempt:
            if media_content_id:
                item.recording = {
                    "status": "download_failed",
                    "error": cache_error or "download_failed",
                }
            else:
                item.recording = {"status": "not_found"}
            self._cancel_recording_resolution(item.id)
            self._schedule_save()
            _LOGGER.debug("Recording resolve final result for item %s: %s", item.id, item.recording)
            return item.recording

        item.recording = {"status": "pending"}
        self._schedule_save()
        return item.recording

    def _schedule_snapshot_capture(self, item_id: str, source_entity_id: str) -> None:
        existing = self._unsub_snapshot_timers.pop(item_id, None)
        if existing is not None:
            existing()

        @callback
        def _capture_callback(_now: datetime) -> None:
            self._unsub_snapshot_timers.pop(item_id, None)
            self.hass.async_create_task(
                self._async_capture_snapshot(item_id, source_entity_id)
            )

        self._unsub_snapshot_timers[item_id] = async_call_later(
            self.hass, SNAPSHOT_DELAY_SECONDS, _capture_callback
        )

    def _schedule_recording_resolution(self, item_id: str) -> None:
        self._cancel_recording_resolution(item_id)
        timer_unsubs: list[Callable[[], None]] = []

        for index, delay_s in enumerate(RECORDING_RETRY_DELAYS_SECONDS):

            @callback
            def _resolve_callback(_now: datetime, idx: int = index) -> None:
                is_final = idx == len(RECORDING_RETRY_DELAYS_SECONDS) - 1
                self.hass.async_create_task(
                    self.async_resolve_recording(item_id, final_attempt=is_final)
                )

            timer_unsubs.append(async_call_later(self.hass, float(delay_s), _resolve_callback))

        self._unsub_recording_timers[item_id] = timer_unsubs

    def _cancel_recording_resolution(self, item_id: str) -> None:
        timer_unsubs = self._unsub_recording_timers.pop(item_id, [])
        for unsub in timer_unsubs:
            unsub()

    async def _async_capture_snapshot(self, item_id: str, source_entity_id: str) -> None:
        item = self._get_item_by_id(item_id)
        if item is None or item.snapshot_url is not None:
            return

        snapshot_camera = self._resolve_snapshot_camera(source_entity_id)
        if snapshot_camera is None:
            _LOGGER.warning(
                "No snapshot camera found for %s; skipping snapshot", source_entity_id
            )
            return

        try:
            image = await async_get_image(self.hass, snapshot_camera, timeout=10)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Snapshot capture failed for %s via %s: %s",
                source_entity_id,
                snapshot_camera,
                err,
            )
            return

        started_local = item.start_dt.astimezone()
        camera_slug = slugify(item.camera_name) or "camera"
        day_folder = started_local.strftime("%Y-%m-%d")
        filename = f"{started_local.strftime('%H%M%S')}_{item.label}.jpg"
        relative = Path("reolink_feed") / camera_slug / day_folder / filename
        absolute = self._www_root / relative

        try:
            await self.hass.async_add_executor_job(_write_snapshot_file, absolute, image.content)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Failed to persist snapshot %s: %s", absolute, err)
            return

        item.snapshot_url = f"/local/{relative.as_posix()}"
        self._schedule_save()
        await self._async_refresh_item_asset_size(item)
        await self._async_enforce_storage_limit()

    async def _async_find_recording_media_content_id(self, item: DetectionItem) -> str | None:
        label_title = _recording_label_title(item.label)
        if label_title is None:
            return None

        # Reolink clip titles are wall-clock times in local camera timezone.
        start_dt = item.start_dt.astimezone()
        end_dt = (item.end_dt or item.start_dt).astimezone()
        window_start = start_dt - timedelta(seconds=RECORDING_WINDOW_START_PAD_SECONDS)
        window_end = end_dt + timedelta(seconds=RECORDING_WINDOW_END_PAD_SECONDS)

        day_candidates = {
            window_start.date(),
            start_dt.date(),
            end_dt.date(),
            window_end.date(),
        }
        _LOGGER.debug(
            "Browse lookup for item %s (camera=%s label=%s days=%s window=%s..%s)",
            item.id,
            item.camera_name,
            item.label,
            sorted(str(day) for day in day_candidates),
            window_start.isoformat(),
            window_end.isoformat(),
        )
        try:
            root = await async_browse_media(self.hass, "media-source://reolink")
        except (BrowseError, HomeAssistantError) as err:
            _LOGGER.debug("Unable to browse reolink media root: %s", err)
            return None

        camera_node = _select_camera_node(root.children or [], item.camera_name)
        if camera_node is None or not camera_node.media_content_id:
            _LOGGER.debug("No camera node found for item %s (camera=%s)", item.id, item.camera_name)
            return None
        _LOGGER.debug(
            "Selected camera node for item %s: title=%s media_content_id=%s",
            item.id,
            getattr(camera_node, "title", ""),
            camera_node.media_content_id,
        )

        try:
            resolution_root = await async_browse_media(self.hass, camera_node.media_content_id)
        except (BrowseError, HomeAssistantError) as err:
            _LOGGER.debug(
                "Unable to browse reolink camera node for item %s (%s): %s",
                item.id,
                camera_node.media_content_id,
                err,
            )
            return None

        resolution_node = _select_low_resolution_node(resolution_root.children or [])
        if resolution_node is None or not resolution_node.media_content_id:
            _LOGGER.debug("No low-resolution node found for item %s", item.id)
            return None
        _LOGGER.debug(
            "Selected resolution node for item %s: title=%s media_content_id=%s",
            item.id,
            getattr(resolution_node, "title", ""),
            resolution_node.media_content_id,
        )

        try:
            days_root = await async_browse_media(self.hass, resolution_node.media_content_id)
        except (BrowseError, HomeAssistantError) as err:
            _LOGGER.debug(
                "Unable to browse reolink day root for item %s (%s): %s",
                item.id,
                resolution_node.media_content_id,
                err,
            )
            return None

        day_nodes = _select_day_nodes(days_root.children or [], day_candidates)
        _LOGGER.debug("Found %s candidate day nodes for item %s", len(day_nodes), item.id)
        best: tuple[int, int, int, float, float, float, str] | None = None
        for day_node, day in day_nodes:
            if not day_node.media_content_id:
                continue
            try:
                day_listing = await async_browse_media(self.hass, day_node.media_content_id)
            except (BrowseError, HomeAssistantError) as err:
                _LOGGER.debug(
                    "Unable to browse reolink day listing for item %s (%s): %s",
                    item.id,
                    day_node.media_content_id,
                    err,
                )
                continue

            file_nodes: list[Any] = []
            children = day_listing.children or []
            matching_event_dirs = [
                child
                for child in children
                if child.can_expand and (child.title or "").strip().lower() == label_title.lower()
            ]
            if matching_event_dirs:
                for event_dir in matching_event_dirs:
                    if not event_dir.media_content_id:
                        continue
                    try:
                        event_listing = await async_browse_media(
                            self.hass, event_dir.media_content_id
                        )
                    except (BrowseError, HomeAssistantError) as err:
                        _LOGGER.debug(
                            "Unable to browse reolink event folder for item %s (%s): %s",
                            item.id,
                            event_dir.media_content_id,
                            err,
                        )
                        continue
                    file_nodes.extend(event_listing.children or [])
            else:
                file_nodes.extend(children)

            for child in file_nodes:
                if not child.media_content_id or child.can_expand:
                    continue
                clip = _clip_bounds_from_title(day, child.title or "", start_dt.tzinfo)
                if clip is None:
                    continue
                clip_start, clip_end = clip
                overlap = _overlap_seconds(window_start, window_end, clip_start, clip_end)
                start_distance = abs((clip_start - start_dt).total_seconds())
                contains_start = int(clip_start <= start_dt <= clip_end)
                contains_interval = int(clip_start <= start_dt and clip_end >= end_dt)
                early_start_seconds = max(0.0, (start_dt - clip_start).total_seconds())
                early_start_bonus = int(0.0 <= early_start_seconds <= 10.0)
                late_start_seconds = max(0.0, (clip_start - start_dt).total_seconds())
                score = (
                    contains_start,
                    contains_interval,
                    early_start_bonus,
                    overlap,
                    -late_start_seconds,
                    -start_distance,
                    child.media_content_id,
                )
                _LOGGER.debug(
                    "Clip candidate for item %s: title=%s start=%s end=%s contains_start=%s contains_interval=%s early_bonus=%s overlap=%.1f late_start=%.1f start_delta=%.1f",
                    item.id,
                    child.title or "",
                    clip_start.isoformat(),
                    clip_end.isoformat(),
                    contains_start,
                    contains_interval,
                    early_start_bonus,
                    overlap,
                    late_start_seconds,
                    start_distance,
                )
                if best is None or score > best:
                    best = score

        if best is None:
            _LOGGER.debug("No matching clip candidate found for item %s", item.id)
            return None
        if best[3] <= 0:
            # If no overlap, only accept a near-start match within padded window.
            nearest = -best[5]
            max_nearest = RECORDING_WINDOW_START_PAD_SECONDS + RECORDING_WINDOW_END_PAD_SECONDS
            if nearest > max_nearest:
                _LOGGER.debug(
                    "Best clip rejected for item %s: overlap=0 nearest_start=%.1fs max_nearest=%ss",
                    item.id,
                    nearest,
                    max_nearest,
                )
                return None
        _LOGGER.debug(
            "Selected clip for item %s: media_content_id=%s contains_start=%s contains_interval=%s early_bonus=%s overlap=%.1fs late_start=%.1fs start_delta=%.1fs",
            item.id,
            best[6],
            best[0],
            best[1],
            best[2],
            best[3],
            -best[4],
            -best[5],
        )
        return best[6]

    async def _async_cache_recordings_for_items(self, items: list[DetectionItem]) -> None:
        eligible_items = [
            item
            for item in items
            if item.end_ts is not None and not (item.recording or {}).get("local_url")
        ]
        if not eligible_items:
            return
        # Reolink playback/download frequently returns "busy" under parallel load.
        semaphore = asyncio.Semaphore(1)

        async def _cache_one(item: DetectionItem) -> None:
            async with semaphore:
                await self.async_resolve_recording(item.id, final_attempt=True)

        await asyncio.gather(*[_cache_one(item) for item in eligible_items])

    async def _async_cache_recording_file(
        self, item: DetectionItem, media_content_id: str
    ) -> tuple[str | None, str | None]:
        relative = _recording_relative_path_for_item(item)
        absolute = self._www_root / relative
        if absolute.exists():
            await self._async_refresh_item_asset_size(item)
            return f"/local/{relative.as_posix()}", None

        try:
            resolved = await async_resolve_media(
                self.hass, media_content_id, item.source_entity_id
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to resolve media for local cache (%s): %s", item.id, err)
            return None, "resolve_failed"

        media_url = self._make_absolute_ha_url(self._sign_ha_path_if_needed(resolved.url))
        if not media_url:
            return None, "invalid_url"

        session = async_get_clientsession(self.hass, verify_ssl=False)
        payload: bytes | None = None
        last_error: str | None = None
        for attempt, delay_s in enumerate(_CACHE_DOWNLOAD_RETRY_DELAYS_SECONDS, start=1):
            if delay_s > 0:
                await asyncio.sleep(delay_s)
            try:
                async with session.get(media_url, timeout=120) as response:
                    content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                    # Reolink playback proxies often return octet-stream for MP4 downloads.
                    # Reject clear HTML/text error payloads, but accept binary stream content types.
                    is_text_or_html = content_type.startswith("text/") or "html" in content_type
                    if response.status >= 400 or is_text_or_html:
                        last_error = (
                            f"http_{response.status}"
                            if response.status >= 400
                            else f"content_type_{content_type or 'unknown'}"
                        )
                        _LOGGER.debug(
                            "Recording cache download failed (%s) attempt %s/%s: HTTP %s content-type=%s",
                            item.id,
                            attempt,
                            len(_CACHE_DOWNLOAD_RETRY_DELAYS_SECONDS),
                            response.status,
                            content_type or "-",
                        )
                        continue
                    payload = await response.read()
            except (ClientError, TimeoutError) as err:
                last_error = "request_error"
                _LOGGER.debug(
                    "Recording cache download error (%s) attempt %s/%s: %s",
                    item.id,
                    attempt,
                    len(_CACHE_DOWNLOAD_RETRY_DELAYS_SECONDS),
                    err,
                )
                continue
            if payload:
                break

        if not payload:
            return None, last_error or "empty_payload"

        try:
            await self.hass.async_add_executor_job(_write_snapshot_file, absolute, payload)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to persist recording cache (%s): %s", item.id, err)
            return None, "persist_failed"
        await self._async_refresh_item_asset_size(item)
        await self._async_enforce_storage_limit()
        return f"/local/{relative.as_posix()}", None

    def _sign_ha_path_if_needed(self, url: str) -> str:
        if not url.startswith("/"):
            return url
        if "authSig=" in url:
            return url
        return async_sign_path(self.hass, url, timedelta(days=7), use_content_user=True)

    def _make_absolute_ha_url(self, url: str) -> str | None:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if not url.startswith("/"):
            return None
        try:
            base = get_url(self.hass, require_ssl=False)
        except NoURLAvailableError:
            base = "http://127.0.0.1:8123"
        return f"{base}{url}"

    async def _async_write_dummy_snapshot(self, item: DetectionItem) -> str:
        started_local = item.start_dt.astimezone()
        camera_slug = slugify(item.camera_name) or "camera"
        day_folder = started_local.strftime("%Y-%m-%d")
        filename = f"{started_local.strftime('%H%M%S')}_{item.label}_mock.svg"
        relative = Path("reolink_feed") / camera_slug / day_folder / filename
        absolute = self._www_root / relative
        await self.hass.async_add_executor_job(
            _write_dummy_svg_file, absolute, item.camera_name, item.label, item.start_ts
        )
        return f"/local/{relative.as_posix()}"

    async def _async_migrate_snapshot_paths(self) -> bool:
        """Migrate legacy /media/local snapshot URLs to /local."""
        changed = False
        legacy_prefix = "/media/local/reolink_feed/"
        new_prefix = "/local/reolink_feed/"
        new_root = self._www_root / "reolink_feed"
        legacy_roots = _candidate_legacy_snapshot_roots(self.hass)

        for item in self._items:
            snapshot_url = item.snapshot_url
            if not snapshot_url or not snapshot_url.startswith(legacy_prefix):
                continue

            relative = snapshot_url[len(legacy_prefix) :]
            item.snapshot_url = f"{new_prefix}{relative}"
            changed = True

            dst = new_root / relative
            if dst.exists():
                continue
            src = next((root / relative for root in legacy_roots if (root / relative).exists()), None)
            if src is None:
                continue
            try:
                await self.hass.async_add_executor_job(_copy_file, src, dst)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Failed to migrate snapshot file %s -> %s: %s", src, dst, err)

        return changed

    async def _async_delete_snapshots_for_items(self, items: list[DetectionItem]) -> None:
        paths: set[Path] = set()
        for item in items:
            for path in _snapshot_paths_for_item(self.hass, self._www_root, item):
                paths.add(path)
            for path in _cached_recording_paths_for_item(self._www_root, item):
                paths.add(path)
        if not paths:
            return
        await self.hass.async_add_executor_job(_delete_files_and_empty_parents, sorted(paths))

    def _resolve_snapshot_camera(self, source_entity_id: str) -> str | None:
        if source_entity_id in self._snapshot_camera_by_sensor:
            return self._snapshot_camera_by_sensor[source_entity_id]

        ent_reg = er.async_get(self.hass)
        source_entry = ent_reg.async_get(source_entity_id)
        if source_entry is None or source_entry.device_id is None:
            self._snapshot_camera_by_sensor[source_entity_id] = None
            return None

        candidates = []
        for entry in er.async_entries_for_device(ent_reg, source_entry.device_id):
            if not entry.entity_id.startswith("camera."):
                continue
            if entry.disabled_by is not None:
                continue
            if self.hass.states.get(entry.entity_id) is None:
                continue
            candidates.append(entry.entity_id)

        if not candidates:
            self._snapshot_camera_by_sensor[source_entity_id] = None
            return None

        candidates.sort(key=_camera_preference_score)
        selected = candidates[0]
        self._snapshot_camera_by_sensor[source_entity_id] = selected
        return selected

    def _resolve_detection_label(self, entity_id: str) -> str | None:
        if entity_id in self._label_by_sensor:
            return self._label_by_sensor[entity_id]

        ent_reg = er.async_get(self.hass)
        entry = ent_reg.async_get(entity_id)
        if entry is not None:
            # Reolink translation keys / unique IDs are stable across HA UI languages.
            translation_key = (entry.translation_key or "").lower()
            if translation_key in SUPPORTED_DETECTION_LABELS:
                self._label_by_sensor[entity_id] = translation_key
                return translation_key
            normalized_key = normalize_detection_label(translation_key)
            if normalized_key in SUPPORTED_DETECTION_LABELS:
                self._label_by_sensor[entity_id] = normalized_key
                return normalized_key

            unique_id = (entry.unique_id or "").lower()
            for suffix, mapped_label in SUPPORTED_SUFFIX_TO_LABEL.items():
                if unique_id.endswith(suffix):
                    self._label_by_sensor[entity_id] = mapped_label
                    return mapped_label

        # Fallback for setups where registry metadata is missing.
        object_id = entity_id.split(".", 1)[1].lower()
        for suffix, mapped_label in SUPPORTED_SUFFIX_TO_LABEL.items():
            if object_id.endswith(suffix):
                self._label_by_sensor[entity_id] = mapped_label
                return mapped_label

        self._label_by_sensor[entity_id] = None
        return None

    async def _async_resolve_recordings_immediately(self, item_ids: list[str]) -> None:
        if not item_ids:
            return

        semaphore = asyncio.Semaphore(4)

        async def _resolve_item(item_id: str) -> None:
            async with semaphore:
                try:
                    await self.async_resolve_recording(item_id, final_attempt=True)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("Recording resolve failed for rebuilt item %s: %s", item_id, err)

        await asyncio.gather(*[_resolve_item(item_id) for item_id in item_ids])

    def _collect_reolink_detection_entities(self) -> list[str]:
        ent_reg = er.async_get(self.hass)
        entity_ids: list[str] = []
        seen: set[str] = set()

        for entry in self.hass.config_entries.async_entries("reolink"):
            for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
                if not entity.entity_id.startswith("binary_sensor."):
                    continue
                if entity.disabled_by is not None:
                    continue
                label = self._resolve_detection_label(entity.entity_id)
                if not self._label_is_enabled(label):
                    continue
                if entity.entity_id in seen:
                    continue
                seen.add(entity.entity_id)
                entity_ids.append(entity.entity_id)

        if entity_ids:
            return sorted(entity_ids)

        # Fallback for setups where registry links are incomplete.
        for entity in ent_reg.entities.values():
            if not entity.entity_id.startswith("binary_sensor."):
                continue
            if entity.disabled_by is not None:
                continue
            if (entity.platform or "").lower() != "reolink":
                continue
            label = self._resolve_detection_label(entity.entity_id)
            if not self._label_is_enabled(label):
                continue
            if entity.entity_id in seen:
                continue
            seen.add(entity.entity_id)
            entity_ids.append(entity.entity_id)
        return sorted(entity_ids)

    async def _build_items_from_history(
        self, entity_ids: list[str], per_entity_changes: int
    ) -> list[DetectionItem]:
        if not entity_ids:
            return []

        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import get_last_state_changes
        except Exception as err:  # noqa: BLE001
            raise RuntimeError(f"Recorder history is unavailable: {err}") from err

        since_dt = datetime.now().astimezone() - timedelta(hours=self._retention_hours)
        built_items: list[DetectionItem] = []
        recorder = get_instance(self.hass)

        for entity_id in entity_ids:
            label = self._resolve_detection_label(entity_id)
            if not self._label_is_enabled(label):
                continue

            try:
                by_entity = await recorder.async_add_executor_job(
                    get_last_state_changes, self.hass, max(2, per_entity_changes), entity_id
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Failed to read recorder history for %s: %s", entity_id, err)
                continue

            states = list(by_entity.get(entity_id, [])) if isinstance(by_entity, dict) else []
            built_items.extend(
                _build_detection_items_for_entity(entity_id, label, states, since_dt)
            )

        built_items = _merge_detection_items(built_items)
        built_items.sort(key=lambda item: item.start_dt, reverse=True)
        return built_items


def _build_detection_items_for_entity(
    entity_id: str, label: str, states: list[Any], since_dt: datetime
) -> list[DetectionItem]:
    if not states:
        return []

    timeline = sorted(
        (state for state in states if _state_changed_at(state) is not None),
        key=lambda state: _state_changed_at(state) or since_dt,
    )
    if not timeline:
        return []

    items: list[DetectionItem] = []
    active_start: datetime | None = None
    camera_name: str | None = None

    for state in timeline:
        changed_at = _state_changed_at(state)
        if changed_at is None:
            continue
        state_value = (getattr(state, "state", "") or "").lower()
        if state_value == "on":
            active_start = changed_at
            camera_name = _camera_name_from_state(entity_id, getattr(state, "name", None))
            continue
        if state_value != "off":
            continue
        if active_start is None:
            continue
        if changed_at <= since_dt:
            active_start = None
            continue

        started = active_start
        ended = changed_at
        if ended <= started:
            active_start = None
            continue
        if ended < since_dt:
            active_start = None
            continue

        resolved_camera = camera_name or _camera_name_from_state(
            entity_id, getattr(state, "name", None)
        )
        duration_s = max(1, int((ended - started).total_seconds()))
        items.append(
            DetectionItem(
                id=str(uuid.uuid4()),
                start_ts=started.isoformat(),
                end_ts=ended.isoformat(),
                duration_s=duration_s,
                label=label,
                source_entity_id=entity_id,
                camera_name=resolved_camera,
                snapshot_url=None,
                recording={"status": "pending"},
            )
        )
        active_start = None

    return items


def _state_changed_at(state: Any) -> datetime | None:
    changed = getattr(state, "last_changed", None) or getattr(state, "last_updated", None)
    if changed is None:
        return None
    if changed.tzinfo is None:
        return changed.replace(tzinfo=dt_util.UTC)
    return changed


def _merge_detection_items(items: list[DetectionItem]) -> list[DetectionItem]:
    if not items:
        return []

    merged: list[DetectionItem] = []
    for item in sorted(items, key=lambda value: value.start_dt):
        if item.end_dt is None:
            continue
        if not merged:
            merged.append(item)
            continue

        prev = merged[-1]
        if prev.end_dt is None:
            merged.append(item)
            continue

        same_group = (
            prev.camera_name == item.camera_name
            and prev.label == item.label
            and prev.source_entity_id == item.source_entity_id
        )
        gap = (item.start_dt - prev.end_dt).total_seconds()
        if not same_group or gap > MERGE_WINDOW_SECONDS:
            merged.append(item)
            continue

        if item.end_dt > prev.end_dt:
            prev.end_ts = item.end_ts
        prev.duration_s = max(1, int((prev.end_dt - prev.start_dt).total_seconds()))
        prev.recording = {"status": "pending"}

    return merged


def _merge_rebuilt_with_existing_items(
    existing_items: list[DetectionItem], rebuilt_items: list[DetectionItem]
) -> tuple[list[DetectionItem], int, int, set[str]]:
    merged_items = list(existing_items)
    added_count = 0
    merged_count = 0
    resolve_item_ids: set[str] = set()

    for rebuilt in rebuilt_items:
        match_index = _find_matching_item_index(merged_items, rebuilt)
        if match_index is None:
            merged_items.append(rebuilt)
            added_count += 1
            if not _recording_is_linked(rebuilt.recording):
                resolve_item_ids.add(rebuilt.id)
            continue

        existing = merged_items[match_index]
        merged_items[match_index] = _merge_existing_item(existing, rebuilt)
        merged_count += 1
        if not _recording_is_linked(merged_items[match_index].recording):
            resolve_item_ids.add(merged_items[match_index].id)

    return merged_items, added_count, merged_count, resolve_item_ids


def _find_matching_item_index(items: list[DetectionItem], candidate: DetectionItem) -> int | None:
    best: tuple[float, int] | None = None
    candidate_end = candidate.end_dt or candidate.start_dt

    for index, existing in enumerate(items):
        if existing.label != candidate.label:
            continue
        if existing.source_entity_id != candidate.source_entity_id:
            continue

        existing_end = existing.end_dt or existing.start_dt
        if not _events_overlap_or_close(
            existing.start_dt,
            existing_end,
            candidate.start_dt,
            candidate_end,
            MERGE_WINDOW_SECONDS,
        ):
            continue

        start_delta = abs((existing.start_dt - candidate.start_dt).total_seconds())
        end_delta = abs((existing_end - candidate_end).total_seconds())
        score = start_delta + end_delta
        if best is None or score < best[0]:
            best = (score, index)

    return best[1] if best is not None else None


def _merge_existing_item(existing: DetectionItem, rebuilt: DetectionItem) -> DetectionItem:
    start_dt = min(existing.start_dt, rebuilt.start_dt)
    existing_end = existing.end_dt
    rebuilt_end = rebuilt.end_dt
    end_dt: datetime | None
    if existing_end is None or rebuilt_end is None:
        end_dt = existing_end or rebuilt_end
    else:
        end_dt = max(existing_end, rebuilt_end)

    if end_dt is not None:
        duration_s: int | None = max(1, int((end_dt - start_dt).total_seconds()))
        end_ts: str | None = end_dt.isoformat()
    else:
        duration_s = None
        end_ts = None

    merged_recording = _merge_recording(existing.recording, rebuilt.recording)
    return DetectionItem(
        id=existing.id,
        start_ts=start_dt.isoformat(),
        end_ts=end_ts,
        duration_s=duration_s,
        label=existing.label,
        source_entity_id=existing.source_entity_id,
        camera_name=existing.camera_name or rebuilt.camera_name,
        snapshot_url=existing.snapshot_url,
        recording=merged_recording,
    )


def _merge_recording(existing: dict[str, Any], rebuilt: dict[str, Any]) -> dict[str, Any]:
    if _recording_is_linked(existing):
        return existing
    if _recording_is_linked(rebuilt):
        return rebuilt
    return {"status": "pending"}


def _recording_is_linked(recording: dict[str, Any] | None) -> bool:
    return (recording or {}).get("status") == "linked"


def _events_overlap_or_close(
    a_start: datetime,
    a_end: datetime,
    b_start: datetime,
    b_end: datetime,
    tolerance_seconds: int,
) -> bool:
    tolerance = timedelta(seconds=tolerance_seconds)
    return a_start <= b_end + tolerance and b_start <= a_end + tolerance


def _write_snapshot_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _write_dummy_svg_file(path: Path, camera_name: str, label: str, start_ts: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='640' height='360'>"
        "<defs><linearGradient id='bg' x1='0' y1='0' x2='1' y2='1'>"
        "<stop offset='0%' stop-color='#1d3557'/>"
        "<stop offset='100%' stop-color='#457b9d'/>"
        "</linearGradient></defs>"
        "<rect width='100%' height='100%' fill='url(#bg)'/>"
        f"<text x='24' y='72' font-size='34' fill='white'>Mock {label.title()} Detection</text>"
        f"<text x='24' y='126' font-size='24' fill='#f1faee'>{camera_name}</text>"
        f"<text x='24' y='170' font-size='18' fill='#f1faee'>{start_ts}</text>"
        "</svg>"
    )
    path.write_text(svg, encoding="utf-8")


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _delete_files_and_empty_parents(paths: list[Path]) -> None:
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        try:
            path.unlink()
        except OSError:
            continue
        _delete_empty_parents(path.parent)


def _sum_existing_file_sizes(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        try:
            if path.exists() and path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _delete_empty_parents(start_dir: Path) -> None:
    current = start_dir
    for _ in range(4):
        if not current.exists() or not current.is_dir():
            return
        if current.name in {"www", "media"}:
            return
        try:
            current.rmdir()
        except OSError:
            return
        if current.name == "reolink_feed":
            return
        current = current.parent


def _snapshot_paths_for_item(hass: HomeAssistant, www_root: Path, item: DetectionItem) -> list[Path]:
    snapshot_url = item.snapshot_url
    if not snapshot_url:
        return []

    paths: list[Path] = []
    if snapshot_url.startswith("/local/reolink_feed/"):
        relative = snapshot_url.removeprefix("/local/")
        paths.append(www_root / relative)
    elif snapshot_url.startswith("/media/local/reolink_feed/"):
        relative = snapshot_url.removeprefix("/media/local/reolink_feed/")
        for root in _candidate_legacy_snapshot_roots(hass):
            paths.append(root / relative)
    return paths


def _cached_recording_paths_for_item(www_root: Path, item: DetectionItem) -> list[Path]:
    recording = item.recording or {}
    local_url = recording.get("local_url")
    if not isinstance(local_url, str):
        return []
    if not local_url.startswith("/local/reolink_feed/"):
        return []
    relative = local_url.removeprefix("/local/")
    return [www_root / relative]


def _recording_relative_path_for_item(item: DetectionItem) -> Path:
    started_local = item.start_dt.astimezone()
    camera_slug = slugify(item.camera_name) or "camera"
    day_folder = started_local.strftime("%Y-%m-%d")
    filename = f"{started_local.strftime('%H%M%S')}_{item.label}.mp4"
    return Path("reolink_feed") / camera_slug / day_folder / filename


def _candidate_legacy_snapshot_roots(hass: HomeAssistant) -> list[Path]:
    roots: list[Path] = []
    roots.append(Path(hass.config.path("media")) / "reolink_feed")
    for media_dir in hass.config.media_dirs.values():
        roots.append(Path(media_dir) / "reolink_feed")
    roots.append(Path("/media") / "reolink_feed")

    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return deduped


def _camera_preference_score(entity_id: str) -> tuple[int, str]:
    object_id = entity_id.split(".", 1)[1].lower()
    if "telephoto" in object_id:
        return (100, object_id)
    if "foto" in object_id and "vloeiend" in object_id:
        return (0, object_id)
    if "foto" in object_id and ("low" in object_id or "vloeiend" in object_id):
        return (1, object_id)
    if "foto" in object_id:
        return (2, object_id)
    if "low" in object_id or "sub" in object_id or "vloeiend" in object_id:
        return (3, object_id)
    return (10, object_id)


def _clip_bounds_from_title(
    day: date, title: str, tzinfo_value: tzinfo | None
) -> tuple[datetime, datetime] | None:
    match = _CLIP_TITLE_PATTERN.match(title.strip())
    if not match:
        return None
    start_token = match.group("start")
    duration_token = match.group("duration")
    try:
        start_time = datetime.strptime(start_token, "%H:%M:%S").time()
    except ValueError:
        return None

    clip_start = datetime.combine(day, start_time, tzinfo=tzinfo_value)
    duration_seconds = (
        _duration_token_to_seconds(duration_token)
        if duration_token
        else RECORDING_DEFAULT_CLIP_DURATION_SECONDS
    )
    clip_end = clip_start + timedelta(seconds=max(1, duration_seconds))
    return clip_start, clip_end


def _duration_token_to_seconds(token: str) -> int:
    parts = token.split(":")
    if len(parts) != 3:
        return RECORDING_DEFAULT_CLIP_DURATION_SECONDS
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(parts[2])
    except ValueError:
        return RECORDING_DEFAULT_CLIP_DURATION_SECONDS
    return max(1, hours * 3600 + minutes * 60 + seconds)


def _overlap_seconds(
    window_start: datetime, window_end: datetime, clip_start: datetime, clip_end: datetime
) -> float:
    start = max(window_start, clip_start)
    end = min(window_end, clip_end)
    return max(0.0, (end - start).total_seconds())


def _recording_label_title(label: str) -> str | None:
    mapping = {
        "person": "Person",
        "pet": "Pet",
        "vehicle": "Vehicle",
        "motion": "Motion",
        "visitor": "Visitor",
    }
    return mapping.get(label)


def _select_camera_node(children: list[Any], camera_name: str) -> Any | None:
    target = camera_name.strip().lower()
    best: tuple[int, Any] | None = None
    for child in children:
        title = (getattr(child, "title", "") or "").strip().lower()
        if not title:
            continue
        if title == target:
            return child
        if target in title or title in target:
            score = 1
        else:
            score = 10
        if best is None or score < best[0]:
            best = (score, child)
    return best[1] if best else None


def _select_low_resolution_node(children: list[Any]) -> Any | None:
    best: tuple[int, Any] | None = None
    for child in children:
        title = (getattr(child, "title", "") or "").lower()
        identifier = (getattr(child, "media_content_id", "") or "").lower()
        if "telephoto" in title or "autotrack_" in identifier:
            score = 100
        elif "low resolution" in title:
            score = 0
        elif "low" in title or "fluent" in title or "|sub" in identifier:
            score = 1
        else:
            score = 10
        if best is None or score < best[0]:
            best = (score, child)
    return best[1] if best else None


def _select_day_nodes(children: list[Any], wanted_days: set[date]) -> list[tuple[Any, date]]:
    result: list[tuple[Any, date]] = []
    for child in children:
        parsed = _parse_day_from_media_node(child)
        if parsed is None:
            continue
        if parsed in wanted_days:
            result.append((child, parsed))
    return result


def _parse_day_from_media_node(node: Any) -> date | None:
    media_id = (getattr(node, "media_content_id", "") or "")
    title = (getattr(node, "title", "") or "")

    if "DAY|" in media_id:
        try:
            identifier = media_id.split("media-source://reolink/", 1)[1]
            parts = identifier.split("|")
            if parts[0] == "DAY" and len(parts) >= 7:
                return date(int(parts[4]), int(parts[5]), int(parts[6].split("/")[0]))
        except (IndexError, ValueError):
            pass

    match = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", title)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def _camera_name_from_state(entity_id: str, friendly_name: str | None) -> str:
    if friendly_name:
        normalized = friendly_name.strip()
        for suffix in (
            " persoon",
            " dier",
            " bezoeker",
            " voertuig",
            " beweging",
            " person",
            " animal",
            " pet",
            " visitor",
            " vehicle",
            " motion",
        ):
            if normalized.lower().endswith(suffix):
                return normalized[: -len(suffix)].strip()
        return normalized

    object_id = entity_id.split(".", 1)[1]
    for suffix in SUPPORTED_SUFFIX_TO_LABEL:
        if object_id.endswith(suffix):
            object_id = object_id[: -len(suffix)]
            break
    return object_id.replace("_", " ").strip().title()
