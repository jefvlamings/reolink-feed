# Repository Guidelines

## Project Structure & Module Organization
- `custom_components/reolink_feed/`: Home Assistant custom integration backend.
  - `__init__.py`: setup/unload + WebSocket command registration.
  - `feed.py`: event listener and detection merge logic.
  - `models.py`, `storage.py`, `const.py`: data model, persistence, constants.
  - `manifest.json`, `strings.json`, `config_flow.py`: HA integration metadata/UI.
- `config/`: Home Assistant runtime config mounted into the container (`/config`).
- `docker-compose.yml`: local development runtime for Home Assistant.
- `PLANS.md`: architecture and v1 scope decisions.
- `README.md`: run/test/use instructions.

## Build, Test, and Development Commands
- `docker compose up -d`: start Home Assistant for local development.
- `docker compose restart homeassistant`: reload after backend code changes.
- `docker compose logs -f homeassistant`: follow runtime logs and integration errors.
- `python3 - <<'PY' ... ast.parse(...)`: quick syntax sanity check for `custom_components/reolink_feed/*.py`.

Example:
```bash
docker compose up -d
docker compose logs -f homeassistant
```

## Coding Style & Naming Conventions
- Language: Python (Home Assistant async style).
- Indentation: 4 spaces; keep code ASCII unless file already requires Unicode.
- Prefer explicit, small functions and typed signatures.
- Naming:
  - modules/files: `snake_case.py`
  - constants: `UPPER_SNAKE_CASE`
  - functions/variables: `snake_case`
  - classes: `PascalCase`
- Follow Home Assistant patterns (`async_*`, config entries, websocket schemas).

## Testing Guidelines
- Current status: no formal automated tests yet.
- Add tests under a future `tests/` directory, mirroring module names (for example, `tests/test_feed.py`).
- Prioritize coverage for burst merge behavior, event transition handling (`off->on`, `on->off`), and storage round-trips.
- For now, validate changes through container logs and manual detection triggers in HA.

## Commit & Pull Request Guidelines
- This workspace may not include accessible Git history; use clear, imperative commit messages.
- Recommended format: `type(scope): short summary` (for example, `feat(feed): add merge window dedup`).
- PRs should include:
  - what changed and why
  - how it was tested (commands + manual steps)
  - config/runtime impact (if any)
  - screenshots only when UI behavior changes

## Security & Configuration Tips
- Do not commit secrets from `config/secrets.yaml` or `.storage` artifacts.
- Keep custom integration code in `custom_components/` only; avoid editing generated runtime files unless debugging.
