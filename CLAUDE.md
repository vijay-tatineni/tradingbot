# CLAUDE.md — Permanent Project Rules

## UI Requirements (NEVER REMOVE)
- dashboard.html MUST have nav bar with:
  * Instruments Editor link
  * Logged in as: [username]
  * Logout button
- instruments.html MUST have nav bar with:
  * Dashboard link
  * Logged in as: [username]
  * Logout button
- Login page MUST redirect to dashboard after auth
- Port 8080 MUST remain closed (nginx on 8082 only)
- JWT token MUST be checked on every protected page

## Architecture Rules (NEVER CHANGE)
- nginx on port 8082 handles all web traffic
- API server on 127.0.0.1:8081 (localhost only)
- Bot runs in screen session named "bot"
- API runs in screen session named "api"

## Before Every Commit
- Run: pytest tests/ -v (all must pass)
- Check dashboard.html has nav bar
- Check instruments.html has nav bar
- Verify no hardcoded ~/trading/ paths
- Confirm port 8080 is not running
