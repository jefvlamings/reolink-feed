#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
One-time migration for Reolink Feed asset layout.

Moves legacy assets:
  /local/reolink_feed/<camera>/<day>/...jpg  -> /local/reolink_feed/<item_id>/snapshot.jpg
  /local/reolink_feed/<camera>/<day>/...mp4  -> /local/reolink_feed/<item_id>/video.mp4

Also updates /config/.storage/reolink_feed.items and optionally fills recording.media_title
from clip_start_ts/clip_end_ts when missing.

Usage:
  ./scripts/migrate-reolink-feed-item-layout.sh [--config /config] [--apply]

Defaults:
  --config /config
  dry-run (no writes) unless --apply is set
EOF
}

CONFIG_DIR="/config"
APPLY="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_DIR="${2:-}"
      shift 2
      ;;
    --apply)
      APPLY="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

STORAGE_FILE="${CONFIG_DIR}/.storage/reolink_feed.items"
WWW_ROOT="${CONFIG_DIR}/www"

if [[ ! -f "${STORAGE_FILE}" ]]; then
  echo "Storage file not found: ${STORAGE_FILE}" >&2
  exit 1
fi
if [[ ! -d "${WWW_ROOT}" ]]; then
  echo "WWW root not found: ${WWW_ROOT}" >&2
  exit 1
fi

if [[ "${APPLY}" == "true" ]]; then
  ts="$(date +%Y%m%d_%H%M%S)"
  backup="${STORAGE_FILE}.bak.${ts}"
  cp "${STORAGE_FILE}" "${backup}"
  echo "Backup created: ${backup}"
else
  echo "Running in dry-run mode. No files will be modified."
fi

python3 - "${STORAGE_FILE}" "${WWW_ROOT}" "${APPLY}" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

storage_file = Path(sys.argv[1])
www_root = Path(sys.argv[2])
apply = sys.argv[3].lower() == "true"

data = json.loads(storage_file.read_text(encoding="utf-8"))
items = data.get("data", {}).get("items", [])

label_title = {
    "person": "Person",
    "pet": "Pet",
    "vehicle": "Vehicle",
    "motion": "Motion",
    "visitor": "Visitor",
}


def duration_token(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}"


def maybe_move_local_url(local_url: str, item_id: str, target_name: str) -> tuple[str, bool]:
    if not local_url.startswith("/local/reolink_feed/"):
        return local_url, False
    desired = f"/local/reolink_feed/{item_id}/{target_name}"
    if local_url == desired:
        return local_url, False
    src_rel = local_url.removeprefix("/local/")
    dst_rel = desired.removeprefix("/local/")
    src = www_root / src_rel
    dst = www_root / dst_rel
    moved = False
    if src.exists() and src.is_file():
        if apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.replace(dst)
        moved = True
    elif dst.exists() and dst.is_file():
        moved = True
    return (desired if moved else local_url), moved


changed = 0
snapshot_moves = 0
video_moves = 0
media_title_updates = 0

for item in items:
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id:
        continue

    snapshot_url = item.get("snapshot_url")
    if isinstance(snapshot_url, str) and snapshot_url:
        new_snapshot_url, moved = maybe_move_local_url(snapshot_url, item_id, "snapshot.jpg")
        if moved:
            snapshot_moves += 1
        if new_snapshot_url != snapshot_url:
            item["snapshot_url"] = new_snapshot_url
            changed += 1

    recording = item.get("recording")
    if not isinstance(recording, dict):
        continue

    local_url = recording.get("local_url")
    if isinstance(local_url, str) and local_url:
        new_local_url, moved = maybe_move_local_url(local_url, item_id, "video.mp4")
        if moved:
            video_moves += 1
        if new_local_url != local_url:
            recording["local_url"] = new_local_url
            changed += 1

    if not recording.get("media_title"):
        clip_start_ts = recording.get("clip_start_ts")
        clip_end_ts = recording.get("clip_end_ts")
        label = str(item.get("label") or "").lower()
        if isinstance(clip_start_ts, str) and isinstance(clip_end_ts, str):
            try:
                clip_start = datetime.fromisoformat(clip_start_ts)
                clip_end = datetime.fromisoformat(clip_end_ts)
                duration_s = max(1, int((clip_end - clip_start).total_seconds()))
                title = f"{clip_start.strftime('%H:%M:%S')} {duration_token(duration_s)} {label_title.get(label, label.title() or 'Person')}"
                recording["media_title"] = title
                changed += 1
                media_title_updates += 1
            except ValueError:
                pass

print(
    f"Scanned {len(items)} items. Changes: {changed}, "
    f"snapshot moves: {snapshot_moves}, video moves: {video_moves}, "
    f"media_title updates: {media_title_updates}"
)

if apply and changed > 0:
    storage_file.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Updated storage file: {storage_file}")
elif apply:
    print("No storage updates needed.")
else:
    print("Dry-run complete. Re-run with --apply to persist changes.")
PY

if [[ "${APPLY}" == "true" ]]; then
  cat <<EOF
Done.
Next steps:
  1) Restart Home Assistant
  2) Open the card and verify old snapshots/videos still resolve
EOF
fi
