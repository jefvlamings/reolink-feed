"""Event tracking and in-memory feed state."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from pathlib import Path
import uuid

from homeassistant.components.camera import async_get_image
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import UNDEFINED
from homeassistant.util import slugify

from .const import (
    MAX_ITEMS,
    MERGE_WINDOW_SECONDS,
    SNAPSHOT_DELAY_SECONDS,
    SUPPORTED_SUFFIX_TO_LABEL,
)
from .models import DetectionItem
from .storage import DetectionStore

_LOGGER = logging.getLogger(__name__)


class ReolinkFeedManager:
    """Manage detection lifecycle and persistence."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = DetectionStore(hass)
        self._media_source_id, media_root = _resolve_media_target(hass)
        self._media_root = Path(media_root)
        self._items: list[DetectionItem] = []
        self._open_item_id_by_key: dict[tuple[str, str], str] = {}
        self._last_closed_item_id_by_key: dict[tuple[str, str], str] = {}
        self._snapshot_camera_by_sensor: dict[str, str | None] = {}
        self._unsub_snapshot_timers: dict[str, Callable[[], None]] = {}
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
        for unsub in self._unsub_snapshot_timers.values():
            unsub()
        self._unsub_snapshot_timers.clear()
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

    async def async_create_mock_detection(
        self,
        source_entity_id: str,
        camera_name: str,
        label: str,
        duration_s: int = 8,
        create_dummy_snapshot: bool = True,
    ) -> DetectionItem:
        """Create a synthetic detection for local development/testing."""
        ended = datetime.now().astimezone()
        started = ended - timedelta(seconds=max(1, duration_s))
        item = DetectionItem(
            id=str(uuid.uuid4()),
            start_ts=started.isoformat(),
            end_ts=ended.isoformat(),
            duration_s=max(1, duration_s),
            label=label,
            source_entity_id=source_entity_id,
            camera_name=camera_name,
            snapshot_url=None,
            recording={"status": "pending"},
        )

        if create_dummy_snapshot:
            snapshot_url = await self._async_write_dummy_snapshot(item)
            item.snapshot_url = snapshot_url

        self._items.insert(0, item)
        if len(self._items) > MAX_ITEMS:
            self._items = self._items[:MAX_ITEMS]
        self._last_closed_item_id_by_key[(camera_name, label)] = item.id
        self._schedule_save()
        return item

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
        absolute = self._media_root / relative

        try:
            await self.hass.async_add_executor_job(_write_snapshot_file, absolute, image.content)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Failed to persist snapshot %s: %s", absolute, err)
            return

        item.snapshot_url = f"/media/{self._media_source_id}/{relative.as_posix()}"
        self._schedule_save()

    async def _async_write_dummy_snapshot(self, item: DetectionItem) -> str:
        started_local = item.start_dt.astimezone()
        camera_slug = slugify(item.camera_name) or "camera"
        day_folder = started_local.strftime("%Y-%m-%d")
        filename = f"{started_local.strftime('%H%M%S')}_{item.label}_mock.svg"
        relative = Path("reolink_feed") / camera_slug / day_folder / filename
        absolute = self._media_root / relative
        await self.hass.async_add_executor_job(
            _write_dummy_svg_file, absolute, item.camera_name, item.label, item.start_ts
        )
        return f"/media/{self._media_source_id}/{relative.as_posix()}"

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


def _resolve_media_target(hass: HomeAssistant) -> tuple[str, str]:
    media_dirs = hass.config.media_dirs
    if "local" in media_dirs:
        return ("local", media_dirs["local"])
    if media_dirs:
        source_id, root = next(iter(media_dirs.items()))
        return (source_id, root)
    return ("local", hass.config.path("media"))


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
