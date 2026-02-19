"""Unit tests for data models."""

from datetime import datetime, timezone

from custom_components.reolink_feed.models import DetectionItem


def test_detection_item_roundtrip_dict() -> None:
    item = DetectionItem(
        id="abc",
        start_ts="2026-02-19T12:00:00+00:00",
        end_ts="2026-02-19T12:00:08+00:00",
        duration_s=8,
        label="person",
        source_entity_id="binary_sensor.cam_person",
        camera_name="Front Door",
        snapshot_url="/local/reolink_feed/front/2026-02-19/120000_person.jpg",
        recording={"status": "linked", "local_url": "/local/reolink_feed/front/2026-02-19/120000_person.mp4"},
    )

    data = item.as_dict()
    restored = DetectionItem.from_dict(data)

    assert restored == item
    assert restored.start_dt == datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc)
    assert restored.end_dt == datetime(2026, 2, 19, 12, 0, 8, tzinfo=timezone.utc)


def test_detection_item_from_dict_defaults_recording() -> None:
    restored = DetectionItem.from_dict(
        {
            "id": "abc",
            "start_ts": "2026-02-19T12:00:00+00:00",
            "end_ts": None,
            "duration_s": None,
            "label": "person",
            "source_entity_id": "binary_sensor.cam_person",
            "camera_name": "Front Door",
            "snapshot_url": None,
        }
    )

    assert restored.recording == {"status": "pending"}
    assert restored.end_dt is None

