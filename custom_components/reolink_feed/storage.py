"""Storage helpers for Reolink feed items."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION
from .models import DetectionItem


class DetectionStore:
    """Persist and load timeline items using Home Assistant storage."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)

    async def async_load(self) -> list[DetectionItem]:
        data = await self._store.async_load()
        if not data:
            return []
        raw_items = data.get("items", [])
        return [DetectionItem.from_dict(item) for item in raw_items]

    async def async_save(self, items: Iterable[DetectionItem]) -> None:
        await self._store.async_save({"items": [item.as_dict() for item in items]})
