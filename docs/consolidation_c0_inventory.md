# Consolidation Phase C0 — Inventory & Freeze (Workstream B)

> **Read-only** with respect to tracked source files and running services. **Nothing was cleaned,
> reset, checked out, merged, cherry-picked, rsync'd, stashed, committed, or restarted.** No
> service was restarted. Secrets/tokens/account IDs/private URLs are **redacted** — only env-var
> **key names** appear. Inspection date: **2026-06-08 (UTC)**.

## 1. Checkout: `/root/trading` (modern / canonical candidate)

```text
absolute path        /root/trading
remote URL           git@github.com:<owner>/<repo>.git        (host/owner/repo redacted)
current branch       planning/dynamic-universe-provider-c0    (created this task off e8668422)
HEAD commit          e8668422cf7f134493d7a43226d3475ce1d4584a (= design/dynamic-universe-hybrid-v1)
operational branch   breakout-strategy @ ffd6d23 (ancestor of e866842; e866842 adds design docs only)
upstream             origin/breakout-strategy (for breakout-strategy); planning branch pushed this task
git status           clean (no tracked modifications, no untracked source)
tracked modifications none
untracked files       none (source); runtime *.db/*.log are gitignored
runtime              Python 3.12.3
dependency file      requirements.txt  sha256 d862b3a805a5508a24ba1e1efb296bad586bbcfb944365643120a28db8d82be8
service units        cogniflowai-api.service, cogniflowai-bot.service
  cogniflowai-api    ExecStart: /usr/bin/python3 /root/trading/api_server.py    WorkingDirectory /root/trading  (active running)
  cogniflowai-bot    ExecStart: /usr/bin/python3 /root/trading/main.py          WorkingDirectory /root/trading  (active running)
ports                API 127.0.0.1:8081 (localhost only); nginx 8082 (public web); 8080 closed (per CLAUDE.md, confirmed not listening)
config filenames     instruments.json, .env (gitignored), users.json (gitignored)
database filenames   advisor.db backtest.db layer3_silver.db learning_loop.db news.db positions.db regime.db trades.db trading.db
deployment scripts   systemd units under /etc/systemd/system/cogniflowai-*.service
env-var key names    IB_USERNAME IB_PASSWORD ANTHROPIC_API_KEY API_TOKEN TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
                     JWT_SECRET IG_USERNAME IG_PASSWORD IG_API_KEY IG_ACC_TYPE IG_ACC_NUMBER GROQ_API_KEY FINNHUB_API_KEY
submodules           none
```

## 2. Checkout: `/root/trading-ig` (older, dirty — IG instance)

```text
absolute path        /root/trading-ig
remote URL           (same origin family; redacted)
current branch       claude-strategy
HEAD commit          d95d258e122c02aac5a6a53ab50f617ce2c285c0 ("Fix IG broker: market_hours KeyError, login backoff…")
upstream             NONE configured ("fatal: no upstream configured for branch 'claude-strategy'")
git status           DIRTY — 11 tracked-modified, 19 untracked directories (see divergence register)
tracked modifications CLAUDE.md, api_server.py, bot/alerts.py, bot/brokers/ig.py, bot/dashboard.py,
                     bot/layer1.py, bot/plugins/base_plugin.py, instruments_ig.json, main.py,
                     tests/test_ig_broker.py, web/dashboard.html, web/instruments.html
untracked dirs       bot/{calendar_ui,degradation,overlays,regime,shadow,strategies}/, docs/, specs/, logs/,
                     tests/{calendar_ui,degradation,golden,integration,invariants,overlays,regime,
                     regression,shadow,strategies}/
runtime              Python 3.12.3
dependency file      requirements.txt  sha256 d862b3a805a5508a24ba1e1efb296bad586bbcfb944365643120a28db8d82be8  (IDENTICAL to modern)
service units        cogniflowai-ig-api.service, cogniflowai-ig-bot.service
  cogniflowai-ig-api ExecStart: /usr/bin/python3 /root/trading-ig/api_server.py --config /root/trading-ig/instruments_ig.json  (active running)
  cogniflowai-ig-bot ExecStart: /usr/bin/python3 /root/trading-ig/main.py --broker ig --config /root/trading-ig/instruments_ig.json  (active running)
ports                IG API 127.0.0.1:8084 (localhost); nginx 8083 (public web, 0.0.0.0)
config filenames     instruments_ig.json, .env (gitignored), .env.example, users.json (gitignored)
database filenames   advisor.db backtest.db layer3_silver.db learning_loop.db news.db positions.db regime.db trading.db
                     (NOTE: no trades.db; has trading.db — per-checkout runtime state, NOT shared)
deployment scripts   systemd units under /etc/systemd/system/cogniflowai-ig-*.service
env-var key names    (identical key set to modern — values NOT read)
submodules           none
.gitignore (deploy)  *.db, *.log, __pycache__, .venv/venv, .env, .env.local, users.json
```

## 3. Key inventory observations

* **Dependencies identical** — `requirements.txt` byte-identical (same SHA-256) across both
  checkouts; consolidation has **no dependency divergence** to resolve.
* **The IG broker adapter is already present and byte-identical in the modern tree**
  (`bot/brokers/ig.py` diff = 0 lines). The modern tree is already broker-pluggable
  (`main.py --broker ig --config …`), consistent with the design doc.
* **The IG checkout is strictly *behind* the modern tree** on the shared source: `api_server.py`
  (109 modern-only / 0 IG-only lines), `dashboard.py`, and critically `main.py` (IG **lacks** the
  hard-disabled invariant + no-edge guardrail the modern tree carries).
* **Databases are per-checkout runtime state**, not a shared store. Each tree has its own
  `positions.db`, `regime.db`, etc. → classified GENERATED_OR_RUNTIME_STATE; back up, do not merge.
* **Both instances run live** (modern→IBKR, IG→IG broker). See `consolidation_c0_cutover_risk.md`
  for the duplicate-execution analysis (10 instruments enabled in both universes).

## 4. Canonical-source recommendation (evaluation only — NO switch made)

| Criterion | `/root/trading` (breakout-strategy line) | `/root/trading-ig` (claude-strategy) |
|---|---|---|
| Hard-disabled safety invariant | **present & deployed** (`main.py` guardrails) | **ABSENT** |
| Newer shared infrastructure | **yes** (regime/shadow/overlays/degradation/strategies tracked) | stale untracked copies |
| Test coverage | tracked test suites for all subsystems | untracked / partial |
| IG-specific unique changes | `ig.py` already merged-forward; needs `instruments_ig.json` + IG CLAUDE.md/web labels | holds IG universe config |
| Current production deployment | IBKR live | IG live |
| Rollback complexity | clean tree, tagged commits | dirty tree, no upstream |
| Database compatibility | own runtime dbs | own runtime dbs (keep separate) |
| Service topology | api+bot | ig-api+ig-bot (config/CLI-driven) |

**Recommended canonical source tree: `/root/trading` on the `breakout-strategy` line.** It is
strictly newer, already broker-pluggable with the IG adapter merged forward, carries the deployed
hard-disabled safety invariant the IG tree lacks, and has a clean working tree. The IG tree
contributes only **`instruments_ig.json` (IG universe/epics)** and small **local-config** deltas
(IG `CLAUDE.md` ports, web labels) that must be **preserved** (not the source line).

**The canonical-source switch was NOT performed.** This is a recommendation pending approval.
