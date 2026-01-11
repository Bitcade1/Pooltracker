# Project Agent Guide

This repo is a Flask-based production/inventory tracker with a SQLite DB and
Jinja templates. Use these notes to keep changes consistent and safe.

## Core Stack
- `flask_app.py` is the main app. Routes, models, and helpers live here.
- Templates live in `templates/` and styles are mainly in `static/styles.css`
  (some pages include local `<style>` blocks).
- DB is SQLite: `pool_table_tracker.db` in the repo root (may be empty locally).
- API routes live in `api_routes.py`.

## Data & Naming Conventions
- Inventory counts are tracked in `PrintedPartsCount` by `part_name`.
- Laminate colors use these constants (keep in sync):
  - `LAMINATE_COLOR_LABELS`
  - `LAMINATE_PART_NAMES` (e.g., `Laminate - Black`)
  - `LAMINATE_COLOR_KEY_TO_LABEL`
- Top rail pieces use `TopRailPieceCount.part_key` like `black_7_short`.
- Body color is parsed from serial suffixes:
  - `GO` = Grey Oak, `O` = Rustic Oak, `C` = Stone, `RB` = Rustic Black.

## UI Patterns
- For counting pages, prefer the panel layout used in
  `templates/counting_hardware.html`.
- Logs should be collapsible (default hidden).
- Keep inventory tables readable: consistent column widths and alignment.

## Do / Don’t
- Do not edit `Desktop App.py` or `Desktop App OLD.py` (legacy).
- Avoid destructive DB operations; prefer additive migrations or scripts.
- Keep non-ASCII out of files unless already present.
- Don’t remove or overwrite unrelated user changes in a dirty repo.

## Common Changes
- When adding new parts:
  - Update any list of valid parts in `flask_app.py` and `api_routes.py`.
  - Ensure they appear in admin threshold lists (part thresholds).
  - Check counting pages and dashboards for usage calculations.

## Testing / Validation
- No automated test suite is configured.
- For UI changes, run the app and click through affected pages:
  - `python flask_app.py` (or `flask run`) then verify the route(s).
- Validate that inventory counts still update and logs reflect changes.

## Run Locally
- Default run: `python flask_app.py` (Flask app starts with built-in server).
- If using `flask run`, ensure `FLASK_APP=flask_app.py` is set.
- App expects the SQLite DB at `pool_table_tracker.db` in the repo root.

## Database Notes (SQLite)
- Main DB file: `pool_table_tracker.db` in repo root.
- Make backups before structural changes: copy the file with a timestamp.
- Prefer additive migrations or one-off scripts; avoid destructive deletes.
- If adding new parts, seed them by inserting rows into `PrintedPartsCount`.

## Production / Deploy Notes
- Hosting details are not stored in this repo. Fill in below:
  - Host: TBD
  - Service/process name: TBD
  - Restart command: TBD
  - Log location: TBD
- Do not hard-code secrets; keep tokens or credentials out of Git.

## Admin / Login Credentials
- Current login password in code: `"Bitcade"` (see `flask_app.py`).
- Worker accounts are managed via the Admin page.
- Update credentials handling if this is ever exposed outside trusted use.

## API Tokens
- Tokens live in `api_routes.py` under `API_TOKENS`.
- Rotate tokens carefully and update any clients using them.
