"""Constants for the Reolink feed integration."""

from __future__ import annotations

DOMAIN = "reolink_feed"
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.items"
MERGE_WINDOW_SECONDS = 20
SNAPSHOT_DELAY_SECONDS = 1.0
RECORDING_RETRY_DELAYS_SECONDS: tuple[int, ...] = (10, 30, 60, 120, 300)
RECORDING_WINDOW_START_PAD_SECONDS = 10
RECORDING_WINDOW_END_PAD_SECONDS = 30
RECORDING_DEFAULT_CLIP_DURATION_SECONDS = 30
MAX_ITEMS = 1000
SUPPORTED_SUFFIX_TO_LABEL: dict[str, str] = {
    "_persoon": "person",
    "_dier": "animal",
}
