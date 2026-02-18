# Reolink Feed

Home Assistant custom integration for a mixed-camera detection timeline based on Reolink AI events.

## Project Goal

Build a single chronological feed (last 24h) of:
- `person` detections
- `animal` detections

Each feed item should contain:
- snapshot image (low resolution)
- link to the matching Reolink recording in Home Assistant Media Source

Architecture is documented in `PLANS.md`.

## Repository Layout

- `custom_components/reolink_feed`: custom integration backend (current focus)
- `config`: Home Assistant config mounted into the Docker container
- `docker-compose.yml`: local HA runtime
- `PLANS.md`: architecture and scope decisions

## Prerequisites

- Docker + Docker Compose
- Reolink integration configured in Home Assistant
- Reolink AI binary sensors available (Dutch names currently matched):
  - `binary_sensor.*_persoon`
  - `binary_sensor.*_dier`

## Run Locally

1. Start Home Assistant:

```bash
docker compose up -d
```

2. Restart after code changes:

```bash
docker compose restart homeassistant
```

3. Tail logs:

```bash
docker compose logs -f homeassistant
```

## Open in Browser

With `network_mode: host`, open:

- `http://localhost:8123`

On first startup, complete Home Assistant onboarding if needed. Then add the custom integration:

1. `Settings -> Devices & Services`
2. `Add Integration`
3. Search for `Reolink Feed`

## Lovelace Card (start)

Card file:
- `config/www/reolink-feed-card.js`

Add it as a Lovelace resource:
- URL: `/local/reolink-feed-card.js`
- Type: `module`

After editing card JS, bump the resource version to force reload:

```bash
./scripts/bump-card-resource-version.sh
```

Then add a manual card:

```yaml
type: custom:reolink-feed-card
title: Reolink Feed
since_hours: 24
limit: 100
labels:
  - person
  - animal
cameras:
  - Deurbel
  - Tuinhuis
refresh_seconds: 20
```

## Current Status (V1 in progress)

Implemented:
- custom integration scaffold with config flow
- event listener for Reolink AI binary sensors (`_persoon`, `_dier`)
- burst merge logic (`merge_window_s = 20`)
- persistent item storage via HA `Store`
- WebSocket endpoint:
  - `reolink_feed/list`

Not yet implemented:
- snapshot capture and media file writing
- recording link resolution (`reolink_feed/resolve_recording`)
- Lovelace timeline card frontend

## Testing

There are no automated tests yet.

### Manual test flow

1. Ensure integration is loaded in Home Assistant.
2. Trigger a person/animal detection on a Reolink camera.
3. Confirm no errors in logs.
4. Inspect `.storage` for persisted feed data in the HA config volume.

### Mock test flow (no real camera trigger needed)

Use the Home Assistant service `reolink_feed.mock_detection` from Developer Tools.

Example service data:

```yaml
entity_id: binary_sensor.deurbel_persoon
camera_name: Deurbel
label: person
duration_s: 8
create_dummy_snapshot: true
```

This creates a synthetic timeline item and writes a dummy snapshot file to:

- `config/media/reolink_feed/<camera_slug>/<YYYY-MM-DD>/<HHMMSS>_person_mock.svg`

### Syntax sanity check

```bash
python3 - <<'PY'
import ast
from pathlib import Path
for p in sorted(Path("custom_components/reolink_feed").glob("*.py")):
    ast.parse(p.read_text(), filename=str(p))
    print("OK", p)
PY
```

## WebSocket API (current)

Command:

- `reolink_feed/list`
- `reolink_feed/resolve_recording`

Request fields:
- `since_hours` (optional, default `24`)
- `limit` (optional, default `200`)
- `labels` (optional, default `["person", "animal"]`)

Response:
- `{ "items": [...] }` sorted newest first

`reolink_feed/resolve_recording` request:
- `{ "item_id": "<item_id>" }`

`reolink_feed/resolve_recording` response:
- `{ "status": "linked|pending|not_found", "media_content_id": "...", "resolved_at": "..." }`

## V1 Scope

- one mixed-camera timeline
- only person + animal
- low-resolution snapshots
- recording links from Reolink `Low resolution` media tree
- no telephoto support in V1

## TODO Roadmap

1. Snapshot capture pipeline
2. Camera entity mapping from detection sensor to low-res snapshot camera
3. Recording resolver with retry/backoff window
4. `reolink_feed/resolve_recording` WebSocket command
5. Custom Lovelace timeline card
6. HACS packaging + release metadata
7. Automated tests for merge logic and resolver matching
