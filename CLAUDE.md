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

## Regime Strategy Rules (from claude-strategy branch)
- Do not modify `bot/layer1.py` signal-generation logic when wrapping it for `TripleConfirmationEngine`. Wrap only. Behavioural equivalence verified by existing backtest suite producing bit-identical trade lists.
- Do not hardcode Anthropic API pricing from training data. Look up `claude-sonnet-4-6` pricing from `https://docs.anthropic.com` at implementation time and note source URL and date in code comments.
- When spec text references existing code (e.g., "match sentiment.py's pattern"), verify against the actual code before relying on it. The spec author may have answered from incomplete knowledge of the codebase.
- When extracting wrappers around existing code, find natural seams. Do not wrap orchestrating classes; wrap the cohesive functions inside them. Log all extraction decisions in docs/TECH_DEBT.md.
- External service identifiers (model names, API endpoints, library versions) mentioned in specs must be re-verified against current documentation before use. Specs go stale.

## Before Every Commit
- Run: pytest tests/ -v (all must pass)
- Check dashboard.html has nav bar
- Check instruments.html has nav bar
- Verify no hardcoded ~/trading/ paths
- Confirm port 8080 is not running
