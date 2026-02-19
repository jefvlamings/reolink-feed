"""Constants for the Reolink feed integration."""

from __future__ import annotations

DOMAIN = "reolink_feed"
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.items"
CARD_FILENAME = "reolink-feed-card.js"
CARD_URL_PATH = f"/{DOMAIN}/{CARD_FILENAME}"
CONF_ENABLED_LABELS = "enabled_labels"
RETENTION_HOURS = 24
CLEANUP_INTERVAL_SECONDS = 3600
LIST_ITEMS_LIMIT = 200
MERGE_WINDOW_SECONDS = 20
SNAPSHOT_DELAY_SECONDS = 1.0
RECORDING_RETRY_DELAYS_SECONDS: tuple[int, ...] = (10, 30, 60, 120, 300)
RECORDING_WINDOW_START_PAD_SECONDS = 10
RECORDING_WINDOW_END_PAD_SECONDS = 30
RECORDING_DEFAULT_CLIP_DURATION_SECONDS = 30
MAX_ITEMS = 1000
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
