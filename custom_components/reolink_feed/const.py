"""Constants for the Reolink feed integration."""

from __future__ import annotations

DOMAIN = "reolink_feed"
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.items"
CARD_FILENAME = "reolink-feed-card.js"
CARD_URL_PATH = f"/{DOMAIN}/{CARD_FILENAME}"
CONF_ENABLED_LABELS = "enabled_labels"
CONF_RETENTION_HOURS = "retention_hours"
CONF_MAX_DETECTIONS = "max_detections"
CONF_REBUILD_NOW = "rebuild_now"
CONF_CACHE_RECORDINGS = "cache_recordings"
CONF_MAX_STORAGE_GB = "max_storage_gb"
DEFAULT_RETENTION_HOURS = 24
MIN_RETENTION_HOURS = 1
MAX_RETENTION_HOURS = 168
DEFAULT_MAX_DETECTIONS = 100
DEFAULT_CACHE_RECORDINGS = False
DEFAULT_MAX_STORAGE_GB = 5.0
MIN_MAX_STORAGE_GB = 0.1
MAX_MAX_STORAGE_GB = 100.0
MIN_MAX_DETECTIONS = 10
MAX_MAX_DETECTIONS = 2000
CLEANUP_INTERVAL_SECONDS = 3600
LIST_ITEMS_LIMIT = 200
MERGE_WINDOW_SECONDS = 20
SNAPSHOT_DELAY_SECONDS = 1.0
RECORDING_RETRY_DELAYS_SECONDS: tuple[int, ...] = (10, 30, 60, 120, 300)
RECORDING_WINDOW_START_PAD_SECONDS = 10
RECORDING_WINDOW_END_PAD_SECONDS = 30
RECORDING_DEFAULT_CLIP_DURATION_SECONDS = 30
SUPPORTED_DETECTION_LABELS: tuple[str, ...] = (
    "person",
    "pet",
    "vehicle",
    "motion",
    "visitor",
)
DEFAULT_ENABLED_DETECTION_LABELS: tuple[str, ...] = ("person", "visitor")
LEGACY_LABEL_ALIASES: dict[str, str] = {
    "animal": "pet",
}
SUPPORTED_SUFFIX_TO_LABEL: dict[str, str] = {
    "_person": "person",
    "_animal": "pet",
    "_pet": "pet",
    "_vehicle": "vehicle",
    "_motion": "motion",
    "_visitor": "visitor",
    "_persoon": "person",
    "_dier": "pet",
    "_voertuig": "vehicle",
    "_beweging": "motion",
    "_bezoeker": "visitor",
}
