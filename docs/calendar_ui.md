# Calendar Storage Decision

## Decision
Calendar data (macro events) is stored in **SQLite only**.

## Rationale
- Overlays read calendar data at decision time; SQLite provides atomic reads
- Calendar UI (§15.4) writes to the same table; single source of truth
- No file-based calendar storage (YAML, JSON) — avoids sync issues between files and DB

## Tables
- `macro_events` — central bank meetings, employment reports, CPI releases

## Earnings overlay
Deferred. See §3.1 tech debt log entry in spec for revisit criteria.

## Schema
See `bot/overlays/calendar_db.py` for CREATE TABLE statements.
