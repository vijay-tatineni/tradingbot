# Calendar Storage Decision

## Decision
Calendar data (macro events) is stored in **SQLite only**.

## Rationale
- Overlays read calendar data at decision time; SQLite provides atomic reads
- Calendar UI (§15.4) writes to the same table; single source of truth
- No file-based calendar storage (YAML, JSON) — avoids sync issues between files and DB

## Tables
- `macro_events` — central bank meetings, employment reports, CPI releases

## DB → Overlay Bridge

The calendar UI writes to `macro_events` in `regime.db`. The overlay registry (`bot/overlays/registry.py:active_overlays()`) reads from the same table and provides the events to `MacroLockoutOverlay.check()` via the `ctx` dict. Without this bridge, calendar entries would have no effect on trading.

The bridge is initialized via `init_overlay_registry(db_path)` in `main.py`, which gives the registry the path to `regime.db`. On each call to `active_overlays()`, the registry reads today's macro events from the DB, converts them to datetime objects, and injects them into ctx before passing to each overlay's `check()` method.

## Earnings overlay
Deferred. See §3.1 tech debt log entry in spec for revisit criteria.

## Schema
See `bot/overlays/calendar_db.py` for CREATE TABLE statements.

## Enabling the Calendar UI

The calendar UI is disabled by default (`enable_calendar_ui: false`).

### 1. Enable the feature flag

In `config.yaml` (or your instance-specific config):

```yaml
settings:
  feature_flags:
    enable_calendar_ui: true
```

### 2. Verify nginx route

The nginx config (`/etc/nginx/sites-enabled/trading`) must include:

```nginx
location @login_redirect_calendar {
    return 302 /login.html?redirect=calendar;
}

location = /calendar.html {
    auth_request /api/auth/verify;
    error_page 401 = @login_redirect_calendar;
    root /root/trading/web;
}
```

After adding, test and reload:

```bash
nginx -t && nginx -s reload
```

### 3. Restart the API server

```bash
screen -S api -X quit
screen -dmS api bash -c 'cd /root/trading && python3 api_server.py'
```

### 4. Verify

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8082/calendar.html
# Expected: 302 (redirect to login)
```
