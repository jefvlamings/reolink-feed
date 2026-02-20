"""Unit tests for feed helper functions."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from custom_components.reolink_feed.const import MERGE_WINDOW_SECONDS, RECORDING_DEFAULT_CLIP_DURATION_SECONDS
from custom_components.reolink_feed.feed import (
    _build_linked_recording,
    _build_detection_items_for_entity,
    _camera_name_from_state,
    _clip_bounds_from_title,
    _duration_token_to_seconds,
    _event_playback_offset_seconds,
    _find_matching_item_index,
    _merge_existing_item,
    _merge_rebuilt_with_existing_items,
    _recording_needs_event_timing,
    _events_overlap_or_close,
    _merge_detection_items,
    _overlap_seconds,
    _parse_day_from_media_node,
    _recording_label_title,
    _recording_relative_path_for_item,
    _should_force_recording_download,
    _select_day_nodes,
    _select_low_resolution_node,
)
from custom_components.reolink_feed.models import DetectionItem


def test_duration_token_to_seconds_parses_and_falls_back() -> None:
    assert _duration_token_to_seconds("00:00:11") == 11
    assert _duration_token_to_seconds("01:02:03") == 3723
    assert _duration_token_to_seconds("bad") == RECORDING_DEFAULT_CLIP_DURATION_SECONDS


def test_events_overlap_or_close_with_tolerance() -> None:
    a_start = datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc)
    a_end = datetime(2026, 2, 19, 12, 0, 10, tzinfo=timezone.utc)
    b_start = datetime(2026, 2, 19, 12, 0, 20, tzinfo=timezone.utc)
    b_end = datetime(2026, 2, 19, 12, 0, 30, tzinfo=timezone.utc)

    assert _events_overlap_or_close(a_start, a_end, b_start, b_end, 10)
    assert not _events_overlap_or_close(a_start, a_end, b_start, b_end, 9)


def test_parse_day_from_media_node_supports_media_id_and_title() -> None:
    from_media_id = SimpleNamespace(media_content_id="media-source://reolink/DAY|x|x|x|2026|2|19", title="")
    from_title = SimpleNamespace(media_content_id="", title="2026/2/18")

    assert str(_parse_day_from_media_node(from_media_id)) == "2026-02-19"
    assert str(_parse_day_from_media_node(from_title)) == "2026-02-18"


def test_camera_name_from_state_removes_detection_suffix() -> None:
    assert _camera_name_from_state("binary_sensor.front_person", "Front Person") == "Front"
    assert _camera_name_from_state("binary_sensor.achterdeur_dier", "Achterdeur Dier") == "Achterdeur"
    assert _camera_name_from_state("binary_sensor.garage", None) == "Garage"


def test_merge_detection_items_merges_small_gap_events() -> None:
    start = datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc)
    first_end = start + timedelta(seconds=10)
    second_start = first_end + timedelta(seconds=MERGE_WINDOW_SECONDS - 1)
    second_end = second_start + timedelta(seconds=8)

    first = DetectionItem(
        id="a",
        start_ts=start.isoformat(),
        end_ts=first_end.isoformat(),
        duration_s=10,
        label="person",
        source_entity_id="binary_sensor.cam_person",
        camera_name="Front",
        snapshot_url=None,
        recording={"status": "pending"},
    )
    second = DetectionItem(
        id="b",
        start_ts=second_start.isoformat(),
        end_ts=second_end.isoformat(),
        duration_s=8,
        label="person",
        source_entity_id="binary_sensor.cam_person",
        camera_name="Front",
        snapshot_url=None,
        recording={"status": "pending"},
    )

    merged = _merge_detection_items([first, second])
    assert len(merged) == 1
    assert merged[0].start_ts == first.start_ts
    assert merged[0].end_ts == second.end_ts
    assert merged[0].duration_s == int((second_end - start).total_seconds())


def test_build_detection_items_for_entity_builds_on_off_pairs() -> None:
    start = datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(seconds=12)
    since_dt = start - timedelta(minutes=5)

    states = [
        SimpleNamespace(state="on", last_changed=start, last_updated=start, name="Front Person"),
        SimpleNamespace(state="off", last_changed=end, last_updated=end, name="Front Person"),
    ]

    items = _build_detection_items_for_entity("binary_sensor.front_person", "person", states, since_dt)

    assert len(items) == 1
    assert items[0].label == "person"
    assert items[0].camera_name == "Front"
    assert items[0].duration_s == 12


def test_merge_existing_item_keeps_snapshot_and_extends_duration() -> None:
    start = datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc)
    mid = start + timedelta(seconds=10)
    end = start + timedelta(seconds=20)
    existing = DetectionItem(
        id="existing",
        start_ts=start.isoformat(),
        end_ts=mid.isoformat(),
        duration_s=10,
        label="person",
        source_entity_id="binary_sensor.front_person",
        camera_name="Front",
        snapshot_url="/local/reolink_feed/front/snap.jpg",
        recording={"status": "pending"},
    )
    rebuilt = DetectionItem(
        id="rebuilt",
        start_ts=start.isoformat(),
        end_ts=end.isoformat(),
        duration_s=20,
        label="person",
        source_entity_id="binary_sensor.front_person",
        camera_name="Front",
        snapshot_url=None,
        recording={"status": "linked", "local_url": "/local/reolink_feed/front/clip.mp4"},
    )

    merged = _merge_existing_item(existing, rebuilt)

    assert merged.id == "existing"
    assert merged.snapshot_url == "/local/reolink_feed/front/snap.jpg"
    assert merged.end_ts == end.isoformat()
    assert merged.duration_s == 20
    assert merged.recording["status"] == "linked"


def test_find_matching_item_index_happy_path() -> None:
    start = datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc)
    existing = DetectionItem(
        id="a",
        start_ts=start.isoformat(),
        end_ts=(start + timedelta(seconds=10)).isoformat(),
        duration_s=10,
        label="person",
        source_entity_id="binary_sensor.front_person",
        camera_name="Front",
        snapshot_url=None,
        recording={"status": "pending"},
    )
    candidate = DetectionItem(
        id="b",
        start_ts=(start + timedelta(seconds=2)).isoformat(),
        end_ts=(start + timedelta(seconds=12)).isoformat(),
        duration_s=10,
        label="person",
        source_entity_id="binary_sensor.front_person",
        camera_name="Front",
        snapshot_url=None,
        recording={"status": "pending"},
    )

    assert _find_matching_item_index([existing], candidate) == 0


def test_merge_rebuilt_with_existing_items_adds_and_merges() -> None:
    start = datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc)
    existing = DetectionItem(
        id="existing",
        start_ts=start.isoformat(),
        end_ts=(start + timedelta(seconds=10)).isoformat(),
        duration_s=10,
        label="person",
        source_entity_id="binary_sensor.front_person",
        camera_name="Front",
        snapshot_url="/local/reolink_feed/front/snap.jpg",
        recording={"status": "pending"},
    )
    rebuilt_matching = DetectionItem(
        id="rebuilt_match",
        start_ts=(start + timedelta(seconds=1)).isoformat(),
        end_ts=(start + timedelta(seconds=15)).isoformat(),
        duration_s=14,
        label="person",
        source_entity_id="binary_sensor.front_person",
        camera_name="Front",
        snapshot_url=None,
        recording={"status": "pending"},
    )
    rebuilt_new = DetectionItem(
        id="rebuilt_new",
        start_ts=(start + timedelta(minutes=1)).isoformat(),
        end_ts=(start + timedelta(minutes=1, seconds=7)).isoformat(),
        duration_s=7,
        label="pet",
        source_entity_id="binary_sensor.front_pet",
        camera_name="Front",
        snapshot_url=None,
        recording={"status": "pending"},
    )

    merged_items, added_count, merged_count, resolve_ids = _merge_rebuilt_with_existing_items(
        [existing], [rebuilt_matching, rebuilt_new]
    )

    assert len(merged_items) == 2
    assert added_count == 1
    assert merged_count == 1
    assert "existing" in resolve_ids
    assert "rebuilt_new" in resolve_ids


def test_clip_and_overlap_happy_path() -> None:
    clip = _clip_bounds_from_title(date(2026, 2, 19), "12:00:00 00:00:30", timezone.utc)
    assert clip is not None
    clip_start, clip_end = clip
    assert clip_start.isoformat() == "2026-02-19T12:00:00+00:00"
    assert clip_end.isoformat() == "2026-02-19T12:00:30+00:00"

    overlap = _overlap_seconds(
        datetime(2026, 2, 19, 12, 0, 10, tzinfo=timezone.utc),
        datetime(2026, 2, 19, 12, 0, 40, tzinfo=timezone.utc),
        clip_start,
        clip_end,
    )
    assert overlap == 20.0


def test_label_title_and_resolution_selection_happy_path() -> None:
    assert _recording_label_title("person") == "Person"
    assert _recording_label_title("pet") == "Pet"


def test_event_playback_offset_seconds_clamps_to_clip_bounds() -> None:
    clip_start = datetime(2026, 2, 20, 8, 23, 38, tzinfo=timezone.utc)
    clip_end = datetime(2026, 2, 20, 8, 23, 59, tzinfo=timezone.utc)

    assert _event_playback_offset_seconds(clip_start - timedelta(seconds=4), clip_start, clip_end) == 0.0
    assert _event_playback_offset_seconds(clip_start + timedelta(seconds=4), clip_start, clip_end) == 4.0
    # Clamp near the tail to avoid seeking beyond available media.
    assert _event_playback_offset_seconds(clip_end + timedelta(seconds=9), clip_start, clip_end) == 20.0


def test_build_linked_recording_includes_clip_timing_metadata() -> None:
    event_start = datetime(2026, 2, 20, 8, 23, 42, tzinfo=timezone.utc)
    clip_start = datetime(2026, 2, 20, 8, 23, 38, tzinfo=timezone.utc)
    clip_end = datetime(2026, 2, 20, 8, 23, 59, tzinfo=timezone.utc)

    recording = _build_linked_recording(
        "/local/reolink_feed/camera/2026-02-20/082338_person.mp4",
        clip_start=clip_start,
        clip_end=clip_end,
        event_start=event_start,
        media_content_id="media-source://reolink/FILE|abc",
        source_url="http://localhost:8123/api/reolink/video/abc",
        media_title="10:25:58 0:00:30 Person",
    )

    assert recording["status"] == "linked"
    assert recording["local_url"].endswith(".mp4")
    assert recording["clip_start_ts"] == clip_start.isoformat()
    assert recording["clip_end_ts"] == clip_end.isoformat()
    assert recording["start_offset_s"] == 4.0
    assert recording["media_content_id"] == "media-source://reolink/FILE|abc"
    assert recording["source_url"] == "http://localhost:8123/api/reolink/video/abc"
    assert recording["media_title"] == "10:25:58 0:00:30 Person"


def test_recording_needs_event_timing_detection() -> None:
    assert _recording_needs_event_timing(None)
    assert _recording_needs_event_timing({"status": "pending"})
    assert _recording_needs_event_timing({"status": "linked", "local_url": "/local/x.mp4"})
    assert not _recording_needs_event_timing(
        {"status": "linked", "local_url": "/local/x.mp4", "start_offset_s": 3.2}
    )
    assert _recording_needs_event_timing(
        {"status": "linked", "local_url": "/local/x.mp4", "start_offset_s": 3.2},
        force_refresh=True,
    )


def test_should_force_recording_download_only_for_manual_linked_reset() -> None:
    assert not _should_force_recording_download(None, final_attempt=False)
    assert not _should_force_recording_download({"status": "pending"}, final_attempt=True)
    assert not _should_force_recording_download({"status": "linked"}, final_attempt=True)
    assert not _should_force_recording_download(
        {"status": "linked", "local_url": "/local/x.mp4"}, final_attempt=False
    )
    assert _should_force_recording_download(
        {"status": "linked", "local_url": "/local/x.mp4"}, final_attempt=True
    )


def test_recording_relative_path_uses_item_id_folder_layout() -> None:
    event_start = datetime(2026, 2, 20, 9, 26, 2, tzinfo=timezone.utc)
    item = DetectionItem(
        id="x",
        start_ts=event_start.isoformat(),
        end_ts=(event_start + timedelta(seconds=21)).isoformat(),
        duration_s=21,
        label="person",
        source_entity_id="binary_sensor.deurbel_person",
        camera_name="Deurbel",
        snapshot_url=None,
        recording={"status": "pending"},
    )
    relative = _recording_relative_path_for_item(item)
    assert relative.as_posix() == "reolink_feed/x/video.mp4"

    nodes = [
        SimpleNamespace(title="High Resolution", media_content_id="media-source://reolink/CAM|main"),
        SimpleNamespace(title="Low Resolution", media_content_id="media-source://reolink/CAM|sub"),
    ]
    selected = _select_low_resolution_node(nodes)
    assert selected is not None
    assert selected.title == "Low Resolution"

    day_nodes = [
        SimpleNamespace(media_content_id="media-source://reolink/DAY|x|x|x|2026|2|18", title=""),
        SimpleNamespace(media_content_id="media-source://reolink/DAY|x|x|x|2026|2|19", title=""),
    ]
    selected_days = _select_day_nodes(day_nodes, {date(2026, 2, 19)})
    assert len(selected_days) == 1
    assert selected_days[0][1] == date(2026, 2, 19)
