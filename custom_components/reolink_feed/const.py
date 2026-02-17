"""Constants for the Reolink feed integration."""

from __future__ import annotations

DOMAIN = "reolink_feed"
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.items"
MERGE_WINDOW_SECONDS = 20
MAX_ITEMS = 1000
SUPPORTED_SUFFIX_TO_LABEL: dict[str, str] = {
    "_persoon": "person",
    "_dier": "animal",
}
