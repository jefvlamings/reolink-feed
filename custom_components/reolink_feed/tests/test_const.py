"""Unit tests for const helpers."""

from custom_components.reolink_feed.const import normalize_detection_label


def test_normalize_detection_label_alias_and_casing() -> None:
    assert normalize_detection_label("animal") == "pet"
    assert normalize_detection_label(" AniMal ") == "pet"
    assert normalize_detection_label("PERSON") == "person"


def test_normalize_detection_label_handles_empty_values() -> None:
    assert normalize_detection_label("") == ""
    assert normalize_detection_label(None) == ""

