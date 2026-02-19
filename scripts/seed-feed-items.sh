#!/usr/bin/env bash
set -euo pipefail

COUNT="${1:-10}"
STORAGE_FILE="${2:-config/.storage/reolink_feed.items}"
RESTART_AFTER="${3:-yes}"

if ! [[ "$COUNT" =~ ^[0-9]+$ ]] || [ "$COUNT" -lt 1 ]; then
  echo "Usage: $0 [count>=1] [storage_file] [restart yes|no]" >&2
  exit 1
fi

python3 - "$COUNT" "$STORAGE_FILE" <<'PY'
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

count = int(sys.argv[1])
storage_file = Path(sys.argv[2])
now = datetime.now(timezone.utc)

labels = ("person", "visitor")
cameras = ("Deurbel", "Tuinhuis")
sources = {
    "Deurbel": {
        "person": "binary_sensor.deurbel_person",
        "visitor": "binary_sensor.deurbel_visitor",
    },
    "Tuinhuis": {
        "person": "binary_sensor.tuinhuis_person",
        "visitor": "binary_sensor.tuinhuis_visitor",
    },
}

items = []
for idx in range(count):
    label = labels[idx % len(labels)]
    camera = cameras[idx % len(cameras)]
    source_entity_id = sources[camera][label]
    started = now - timedelta(minutes=(idx * 3) + 1, seconds=idx)
    duration_s = 6 + (idx % 10)
    ended = started + timedelta(seconds=duration_s)
    items.append(
        {
            "id": str(uuid.uuid4()),
            "start_ts": started.isoformat(),
            "end_ts": ended.isoformat(),
            "duration_s": duration_s,
            "label": label,
            "source_entity_id": source_entity_id,
            "camera_name": camera,
            "snapshot_url": None,
            "recording": {"status": "not_found"},
        }
    )

items.sort(key=lambda item: item["start_ts"], reverse=True)

storage_file.parent.mkdir(parents=True, exist_ok=True)
if storage_file.exists():
    try:
        payload = json.loads(storage_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = {}
else:
    payload = {}

payload.setdefault("version", 1)
payload.setdefault("minor_version", 1)
payload.setdefault("key", "reolink_feed.items")
payload["data"] = {"items": items}

storage_file.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")
print(f"Wrote {len(items)} seeded feed items to {storage_file}")
PY

if [ "$RESTART_AFTER" = "yes" ]; then
  echo "Restarting Home Assistant container..."
  docker compose restart homeassistant
  echo "Done. Home Assistant restarted."
else
  echo "Skipped container restart. Restart manually to load seeded items."
fi

