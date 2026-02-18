# Reolink Detection Timeline (Home Assistant) — Architecture Decisions (v1)

## Goal
Create a **single chronological timeline** (mixed across cameras) of **Person + Animal** detections for the last **24 hours**, each item showing:
- a **low-resolution snapshot** (still image) for quick scanning
- a **click-through to the associated video recording** exposed by the official Reolink integration via Home Assistant **Media Browser / Media Source**

Target environment:
- Home Assistant running in Docker
- Official Reolink integration
- Reolink RLN-36 NVR running 24/7

---

## High-level approach
Ship as:
1) A **small custom backend integration** (custom component) that:
   - listens to Reolink AI binary sensor state changes (person/animal)
   - creates and stores a normalized detection feed (with snapshot paths + recording links)
   - exposes a tiny WebSocket API for the UI card

2) A **custom Lovelace card** (frontend) that:
   - calls the backend WebSocket endpoints
   - renders a mixed-camera chronological timeline (last 24h)
   - plays/opens the linked recording when an item is clicked

---

## Data sources
### Detection events (source of truth)
Use Home Assistant **state change events** from official Reolink entities:
- `binary_sensor.*_persoon`  → label = `person`
- `binary_sensor.*_dier`     → label = `animal`

Observed behavior: sensors are **short-hold/pulse** (typically ~6–12s on) and may occur in bursts.

Event semantics:
- `off → on`  : detection start (create or merge into existing item)
- `on  → off` : detection end (close item, compute duration, trigger recording resolution)

No reliance on sensor attributes (none present), only timestamps:
- Use event bus timestamps (e.g., `event.time_fired`) for start/end.

### Recordings (video)
Do **not** query the NVR directly.
Link recordings via Home Assistant **Media Source** items exposed by the Reolink integration.

Media Browser structure (confirmed):
```
Media
└── Reolink
    ├── Deurbel
    │   ├── Low resolution
    │   └── High resolution
    └── Camera tuinhuis
        ├── Low resolution
        ├── High resolution
        ├── Telephoto low resolution
        └── Telephoto high resolution
```

Within each (camera / variant):
- folder per day: `YYYY/M/D`
- under each day:
  - event folders: `Motion`, `Vehicle`, `Animal`, `Person`, `Doorbel`
  - plus continuous 5-minute recordings (ignored for v1 except as possible future fallback)
- within `Person` / `Animal`:
  - clip items with names like: `07:05:27 0:00:17 Person`

---

## V1 scope constraints (explicit)
- Timeline mixes all cameras in **one** chronological feed (newest first).
- Only **Person + Animal** detections.
- **Snapshots:** use **low-resolution** stream (preferred low-res still-photo entity).
- **Recordings:** resolve/link from **Low resolution** branch in Media Browser.
- **No telephoto support** (ignore `Telephoto*` folders entirely in v1).

---

## Detection item model
Each feed item represents a (possibly merged) detection burst for a given camera + label.

Suggested JSON shape (stored via HA `Store` in `.storage/`):
```json
{
  "id": "uuid",
  "start_ts": "2026-02-17T17:36:30+01:00",
  "end_ts": "2026-02-17T17:37:06+01:00",
  "duration_s": 36,
  "label": "person",
  "source_entity_id": "binary_sensor.deurbel_persoon",
  "camera_name": "Deurbel",
  "snapshot_url": "/media/local/reolink_feed/deurbel/2026-02-17/173630_person.jpg",
  "recording": {
    "status": "pending|linked|not_found",
    "media_content_id": "media-source://…",
    "resolved_at": "2026-02-17T17:37:20+01:00"
  }
}
```

Notes:
- `camera_name` is the folder name under `Media/Reolink` (e.g., `Deurbel`, `Camera tuinhuis`).
- `snapshot_url` is a stable HA-served URL (under `/media/local/...`).

---

## Burst merging & deduplication
Because AI binary sensors can pulse multiple times within seconds, v1 merges bursts:

- Key: `(camera_name, label)`
- `merge_window_s = 20`

Rules:
- On `off→on`:
  - if last item for key ended within `merge_window_s`, merge by reopening/extending the last item
  - else create a new item
- On `on→off`:
  - close the open item and compute duration

---

## Snapshot capture
- Snapshot is taken on detection start with a small delay to avoid “empty first frame”:
  - `snapshot_delay_s = 1.0`

Storage location:
- `/media/reolink_feed/<camera_slug>/<YYYY-MM-DD>/<HHMMSS>_<label>.jpg`

Mapping from detection binary_sensor → snapshot camera entity:
- resolve via HA device/entity registry: find camera entities on the same device
- prefer low-res still photo entity:
  1) `camera.*foto*vloeiend` (preferred)
  2) other `camera.*foto*` that indicates low/smooth
  3) fallback: any low-res camera entity available

Cache mapping in memory.

---

## Recording linkage (clip appears after detection ends)
Problem:
- detection triggers at start, but the corresponding clip becomes visible in Media Browser **after** detection stops.

Solution:
- Two-phase pipeline:
  1) Create item at `off→on`, mark recording `pending`
  2) On `on→off`, schedule resolution attempts with delay + retries

Defaults:
- `settle_delay_s = 10` (first attempt at `end_ts + 10s`)
- retry schedule: `+10s`, `+30s`, `+60s`, `+120s`, `+300s`
- stop after ~10 minutes → mark `not_found` if still unresolved

Clip resolution window:
- `window_start = start_ts - 10s`
- `window_end   = end_ts + 30s`

Clip selection:
- browse: `Reolink/<Camera>/Low resolution/<Y/M/D>/(Person|Animal)`
- parse each clip item name:
  - `HH:MM:SS` (start)
  - `H:MM:SS` (duration) if present
- choose best match by overlap/nearest start within the window
- store `media_content_id` when found (set status `linked`)

Edge cases:
- if event crosses midnight, also check adjacent day folder(s)
- day folder format uses **no zero padding**: `YYYY/M/D`

---

## Backend ↔ Frontend API (WebSocket)
Expose minimal WS commands from the custom integration:

1) `reolink_feed/list`
- request: `{ "since_hours": 24, "limit": 200, "labels": ["person","animal"] }` (all optional)
- response: `{ "items": [ ... ] }` sorted newest-first

2) `reolink_feed/resolve_recording`
- request: `{ "item_id": "<item_id>" }`
- response: `{ "status": "linked|pending|not_found", "media_content_id": "media-source://…" }`

Frontend behavior:
- render list from `list`
- on click:
  - if `linked` → open/play recording
  - else call `resolve_recording`, then open if linked; otherwise show “clip not ready” + retry

---

## V1 non-goals (explicitly deferred)
- Telephoto variants
- Vehicle and Motion (and other Reolink event types)
- Advanced filtering/grouping (camera grouping, heatmap, etc.)
- Server-side thumbnails extracted from video (snapshot-only for v1)
- Fallback linking to continuous 5-minute recordings (possible v1.1)

---

## Implementation packaging
- Backend: `/config/custom_components/reolink_feed/…`
- Frontend: custom Lovelace card (HACS-compatible later)
- Persistence: HA `Store` in `.storage/` + images under `/media/reolink_feed/…`

---

## Distribution Plan (HACS Custom Repos)
Goal: make installation as close to one-click as possible by linking users directly to GitHub repos via HACS custom repositories.

### Packaging strategy
Use two repos (or one mono-repo with separate release artifacts, but two repos is simpler for users):
1) **Integration repo** (HACS type: `Integration`)
   - contains `custom_components/reolink_feed`
2) **Card repo** (HACS type: `Dashboard`)
   - contains built card asset in `dist/reolink-feed-card.js`

### Required metadata/files
Integration repo:
- root `hacs.json`
- `custom_components/reolink_feed/manifest.json` with real `documentation`, `issue_tracker`, `codeowners`, and semantic `version`
- release tags (`vX.Y.Z`)

Card repo:
- root `hacs.json` (dashboard/plugin category)
- `dist/reolink-feed-card.js`
- README with card config snippet
- release tags (`vX.Y.Z`)

### CI/quality gates
- add `hassfest` workflow for integration repo
- add HACS validation workflow for both repos

### User install flow (custom repo)
1) Open HACS -> Custom repositories
2) Add integration repo as type **Integration**
3) Add card repo as type **Dashboard**
4) Install both, then add card resource and card YAML

### Optional polish
- add My Home Assistant badges in README for faster install/navigation
