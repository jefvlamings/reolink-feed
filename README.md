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
- Reolink AI binary sensors available (`person` / `animal`, language-agnostic mapping via entity registry metadata)

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
- `custom_components/reolink_feed/frontend/reolink-feed-card.js` (source of truth)

Add it as a Lovelace resource:
- URL: `/reolink_feed/reolink-feed-card.js`
- Type: `module`
- In storage mode dashboards, the integration auto-adds this resource.

For local development with `/local/reolink-feed-card.js`, run:

```bash
./scripts/bump-card-resource-version.sh
```

This syncs the bundled card file to `config/www/reolink-feed-card.js`, bumps the resource `?v=` value, and restarts Home Assistant.

Then add a manual card:

```yaml
type: custom:reolink-feed-card
labels:
  - person
  - animal
cameras:
  - Deurbel
  - Tuinhuis
page_size: 20
```

## Current Status (V1 in progress)

Implemented:
- custom integration scaffold with config flow
- event listener for Reolink AI binary sensors (language-agnostic label mapping)
- burst merge logic (`merge_window_s = 20`)
- persistent item storage via HA `Store`
- snapshot capture + snapshot URL persistence
- recording resolver with retries + clip linking
- WebSocket endpoints:
  - `reolink_feed/list`
  - `reolink_feed/resolve_recording`
  - `reolink_feed/rebuild_from_history`
- mock detection service: `reolink_feed.mock_detection`
- custom Lovelace card UI bundled in integration (`custom_components/reolink_feed/frontend/reolink-feed-card.js`)

Not yet implemented:
- automated tests for backend logic and resolver matching
- production hardening for all Reolink/NVR edge cases

## HACS Packaging (single repo)

This repo is now a HACS `Integration` repo that also ships the custom card.

Files used by HACS:

- root `hacs.json`
- integration code under `custom_components/reolink_feed/`
- bundled card under `custom_components/reolink_feed/frontend/reolink-feed-card.js`

Install flow in HACS:

1. Add this GitHub repository as custom repository type `Integration`.
2. Install `Reolink Feed`.
3. Restart Home Assistant and refresh the browser.
4. If your dashboard is YAML mode, add Lovelace resource URL `/reolink_feed/reolink-feed-card.js` with type `module`.
5. Add card `type: custom:reolink-feed-card`.

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

- `config/www/reolink_feed/<camera_slug>/<YYYY-MM-DD>/<HHMMSS>_person_mock.svg`

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

1. Publish release tags and test HACS install path end-to-end
2. Automated tests for merge logic, snapshot mapping, and resolver matching
3. Frontend UX polish and accessibility pass
4. Optional fallback linking to continuous recordings (v1.1)
