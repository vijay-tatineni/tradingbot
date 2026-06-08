# Single-Codebase Consolidation Plan — IBKR + IG

> **Design/planning only.** Both checkouts inspected **read-only**; neither modified. No
> services changed/restarted, no merges, no deploys. Secrets redacted (only `.env` key
> *names* and config *port/broker* fields shown). Part of
> `design/dynamic-universe-hybrid-v1`; master index
> `experiments/dynamic_universe_hybrid_v1_DRAFT.md`. This is **Task 3**.

---

## 1. Verified inventory (read-only)

### 1.1 Git state of each checkout

| | `/root/trading` (IBKR) | `/root/trading-ig` (IG) |
|---|---|---|
| Branch | `design/dynamic-universe-hybrid-v1` | **`claude-strategy`** |
| HEAD | `8acd1b8` | **`d95d258`** |
| Remote | `git@github.com:vijay-tatineni/tradingbot.git` | same remote |
| Working tree | **clean** | **dirty** — see 1.2 |

The two checkouts are on **different branches at different points** of the same repo. The
IBKR side carries the full modern line (regime/shadow/overlays/degradation + breakout +
hard-disabled invariant + this design work). The IG side is pinned on the older
`claude-strategy` line **plus uncommitted local edits and rsync'd-but-untracked feature
dirs** — the classic manual-sync drift.

### 1.2 IG checkout uncommitted working-tree changes (verified)

Modified (tracked): `CLAUDE.md`, `api_server.py`, `bot/alerts.py`, `bot/brokers/ig.py`,
`bot/dashboard.py`, `bot/layer1.py`, `bot/plugins/base_plugin.py`, `instruments_ig.json`,
`main.py`, `tests/test_ig_broker.py`, `web/dashboard.html`, `web/instruments.html`.

Untracked (rsync'd in, not committed on `claude-strategy`): `bot/calendar_ui/`,
`bot/degradation/`, `bot/overlays/`, `bot/regime/`, `bot/shadow/`, `bot/strategies/`,
`docs/`, `logs/`.

**Implication:** the IG checkout's true running state is *not* any single commit — it is
`d95d258` + uncommitted edits + untracked files. This must be **frozen and captured**
(C0) before consolidation, or work is lost / irreproducible.

### 1.3 Directory divergence (`diff -rq`, excluding `.git`,`__pycache__`,`*.db`,`*.log`,`logs`,`backups`,`results`)

* **Only in `/root/trading` (21):** dominated by **branch-age artifacts** — `TASK_SPEC_*.md`
  (12 files), `.claude`, `HANDOVER.md`, the new `experiments/` + `docs/` design files, etc.
  These are *not* IG-relevant differences; they are "IBKR side is on a newer branch."
* **Only in `/root/trading-ig` (1):** `TASK_SPEC_IG_INSTANCE.md` (the IG-instance runbook).
* **Differing shared files (27):** incl. `api_server.py`, `bot/config.py`, `bot/layer1.py`,
  `main.py`, `bot/dashboard.py`, `instruments.json`, `instruments_ig.json`, `CLAUDE.md`,
  `backtest/simulator.py`, `bot/regime/cost_tracker.py`, several `tests/*`, all `web/*.html`.

**Key reading:** the divergence is **dominated by branch age**, not by intentional
per-broker logic. The genuinely *broker-specific* differences are small and well-contained:
`bot/brokers/ig.py` (IG adapter, with uncommitted IG-side edits), the config files, and the
service invocation flags. Everything else differs only because the IG side is stale.

### 1.4 Service units (verified; secrets redacted)

| Service | ExecStart | WorkingDir | Started |
|---|---|---|---|
| `cogniflowai-api` | `python3 /root/trading/api_server.py` | `/root/trading` | 2026-06-08 09:53 (my prior deploy) |
| `cogniflowai-bot` | `python3 /root/trading/main.py` | `/root/trading` | 2026-06-08 09:53 |
| `cogniflowai-ig-api` | `python3 /root/trading-ig/api_server.py --config /root/trading-ig/instruments_ig.json` | `/root/trading-ig` | 2026-06-03 06:22 |
| `cogniflowai-ig-bot` | `python3 /root/trading-ig/main.py --broker ig --config /root/trading-ig/instruments_ig.json` | `/root/trading-ig` | 2026-06-03 06:22 |

**Decisive fact:** broker selection is **already configuration/CLI-driven** — the IG bot runs
the *same entrypoints* with `--broker ig --config instruments_ig.json`, dispatched by
`create_broker(broker_type, cfg)` (`bot/brokers/__init__.py:11`). The single-codebase
*mechanism already exists*; the only thing missing is that the two trees aren't the same
checkout. **Consolidation is therefore a deployment/topology change, not a re-architecture.**

### 1.5 Config / port / env divergence (verified, redacted)

* `instruments.json` (IBKR): `broker=ibkr`, `port=4000` (IB Gateway socket), `web_dir=web`.
* `instruments_ig.json` (IG): `broker=ig`, `port=None` (IG is REST — no socket port).
* Listeners: IBKR api `127.0.0.1:8081` (nginx **8082**); IG api `127.0.0.1:8084` (nginx
  **8083**). Distinct ports — required so both can run on one host.
* `.env` **key set is identical** across both checkouts (14 keys incl. both `IB_*` and `IG_*`
  + shared `JWT_SECRET`, `ANTHROPIC_API_KEY`, `FINNHUB_API_KEY`, `TELEGRAM_*`). Values differ
  per environment but the **schema is the same** → a single `.env` schema with per-instance
  overrides is feasible. (Note: a shared `JWT_SECRET`/`API_TOKEN` across instances is a
  security decision flagged in §5.)

### 1.6 Already-planned consolidation

`docs/TECH_DEBT.md` already records this as **PR7 (commit `7a9dd06`)**: "consolidate to a
single source … eliminating the rsync ritual entirely," and CLAUDE.md's "After Every Commit
That Touches a Running Service" section documents the current rsync+restart IG ritual. This
plan formalises and de-risks that pending work.

---

## 2. Target architecture (one repo, config-driven gateways)

```text
ONE repository / ONE tested release commit
  shared: strategy engine · canonical instrument registry · dynamic-universe state engine ·
          global risk engine · broker-neutral order-intent model · idempotency & reconciliation ·
          logging · monitoring · migrations · safety invariants (hard-disabled guard)
  broker adapters: bot/brokers/ibkr.py · bot/brokers/ig.py  (unchanged interface, BaseBroker)
  gateway/account config: EXTERNAL config files + env, NOT separate source trees
       instance "ibkr": --broker ibkr --config <ibkr config>   (port 4000 socket, nginx 8082)
       instance "ig"  : --broker ig   --config <ig   config>   (REST, nginx 8083)
  one deployment process; N systemd instances from the SAME checkout
```

Realisation: a single checkout (e.g. `/root/trading`) on one release commit; the IG systemd
units change only their `WorkingDirectory`/paths to point at the same checkout with the IG
config. Use **systemd templated units** (`cogniflowai@ibkr`, `cogniflowai@ig`) or keep two
named units that both exec from the one checkout. **No code change to `BaseBroker` or the
adapters is required** (task prohibits it anyway) — the factory + `--broker`/`--config`
already provide the seam.

---

## 3. Consolidation constraints preserved (task requirement)

| Constraint | How the plan preserves it |
|---|---|
| Deployed hard-disabled invariant | The canonical checkout is the IBKR side, which **already contains** `validate_hard_disabled_instruments` (`72e00cb`). IG instance inherits it automatically once it runs from that checkout — **strengthening** IG (which lacks it today on `claude-strategy`). |
| XAUUSD/XAGUSD safety state | Unchanged in `instruments.json`; IG config validated by the same guard post-consolidation. |
| Current position management | No checkout switch until positions reconciled; IG `positions`-equivalent state preserved (its own DBs copied, not regenerated). |
| Current DB contents | Per-instance DBs (`positions.db`, `regime.db`, etc.) are **data, not code** — kept per-instance (separate data dirs) or migrated with backups; never regenerated. |
| API compatibility | API routes unchanged; only paths/units move. Dashboards keep their nginx ports (8082/8083). |
| Roll back both gateways | Every phase keeps the old IG checkout intact until C8; rollback = repoint units back. |
| No duplicate positions | Old + new IG must never both place orders simultaneously (C7 stop-the-old-before-start-the-new). |
| No automatic cross-broker fallback | Each instance has exactly one `--broker`; no fallback path introduced. |
| No mirrored execution | Instances run independent configs; no shared order queue. |

---

## 4. Phased migration plan (C0–C8)

For each: **entry criteria · files affected · tests · deployment risk · rollback · proof**.
No consolidation is performed in this task — this is the plan only.

### C0 — Inventory & freeze
* **Entry:** approval to begin consolidation planning execution (separate from this design).
* **Files:** none changed. **Capture** IG checkout's dirty state: `git -C /root/trading-ig
  stash list`/`diff` saved to a patch, untracked dirs archived, `.env`/DBs backed up with
  SHA-256.
* **Tests:** none (capture only).
* **Risk:** none. **Rollback:** n/a.
* **Proof:** a stored bundle reproducing the exact IG running state (commit + patch +
  untracked archive + DB/.env hashes).

### C1 — Identify canonical source tree
* **Entry:** C0 bundle exists.
* **Decision:** canonical = `/root/trading` modern line (it has the safety invariant + all
  feature dirs committed). IG side contributes only its genuine deltas (`bot/brokers/ig.py`
  edits, IG config, `TASK_SPEC_IG_INSTANCE.md`).
* **Files:** none changed yet.
* **Tests:** enumerate the IG-side deltas to port (diff C0 patch vs canonical).
* **Risk:** none. **Rollback:** n/a. **Proof:** a written delta list (small, per §1.3).

### C2 — Extract configuration differences
* **Entry:** C1 delta list.
* **Files:** define an external gateway/account config model — per-instance config file
  (`instruments_ig.json` already is one) + per-instance `.env`/env-overrides; document the
  port map (8082/8083) and IB socket port (4000) vs IG REST.
* **Tests:** config-load test per instance (`bot/config.Config` constructs cleanly for both
  broker types); hard-disabled validator passes on the IG config.
* **Risk:** low (config only). **Rollback:** discard config draft.
* **Proof:** both configs load under one codebase; guard returns `[]` for both.

### C3 — Reconcile shared-code divergence
* **Entry:** C2 done.
* **Files:** the genuine IG deltas — primarily `bot/brokers/ig.py` (port the uncommitted IG
  edits onto the canonical adapter), plus any IG-only fixes in `bot/layer1.py`/`api_server.py`
  that are *not* already superseded on the modern line. Most "differences" are branch-age and
  are resolved by simply adopting the canonical (newer) version.
* **Tests:** full suite on the canonical checkout **including** `tests/test_ig_broker.py`;
  IG adapter unit tests must pass without live calls (mock IG service).
* **Risk:** medium (merging adapter edits). **Rollback:** revert the reconciliation commit.
* **Proof:** one checkout, full suite green, IG adapter tests green.

### C4 — Add gateway/account configuration model
* **Entry:** C3 green.
* **Files:** systemd unit definitions (templated `cogniflowai@.service` or repointed IG
  units) referencing the single checkout + per-instance config; deployment script that starts
  N instances from one commit.
* **Tests:** dry-run unit files (`systemd-analyze verify`); instance starts in a staging
  user/port without touching live.
* **Risk:** medium (unit changes). **Rollback:** keep old unit files; revert.
* **Proof:** both instances start from the one checkout in staging, correct ports.

### C5 — Run both gateway test suites from one codebase
* **Entry:** C4 staging start works.
* **Files:** CI/test config only.
* **Tests:** the **entire** suite once (not per-tree); IG + IBKR paths both covered; the
  deployed hard-disabled tests included (eliminates the TECH_DEBT "IG-side test runs"
  redundancy).
* **Risk:** low. **Rollback:** n/a. **Proof:** single green suite covering both gateways.

### C6 — Shadow dual-gateway startup/reconciliation
* **Entry:** C5 green.
* **Files:** none to live; run the consolidated checkout in **shadow** (read-only reconcile)
  alongside the still-live IG checkout.
* **Tests:** both-broker startup reconciliation (design DRAFT §9) observes IBKR + IG positions
  read-only; assert it would pause-on-mismatch, never force-close, never duplicate.
* **Risk:** low (read-only). **Rollback:** stop shadow. **Proof:** reconciliation report shows
  IG positions correctly observed from the consolidated checkout with zero write actions.

### C7 — Migrate IG service to consolidated checkout
* **Entry:** C6 clean for a defined observation window; operator go.
* **Files:** IG systemd units repointed to the consolidated checkout + IG config; **stop the
  old IG bot before starting the new one** (no overlap → no duplicate orders).
* **Tests:** post-switch: IG service `active`, fresh cycle, hard-disabled guard passes on IG
  config, IG positions reconcile identically, no duplicate/мirrored orders.
* **Risk:** **high** (live IG execution path moves). **Rollback:** repoint IG units back to
  `/root/trading-ig` (intact from C0) + restore IG `.env`/DB backups + restart; verify prior
  healthy state. (Mirrors the verified IBKR rollback discipline from the safety deploy.)
* **Proof:** IG runs from the consolidated checkout, one tested commit, positions intact, no
  duplicates.

### C8 — Verify & retire old IG checkout
* **Entry:** C7 stable for an agreed window.
* **Files:** archive `/root/trading-ig` (do not delete until archived); remove from
  deployment.
* **Tests:** confirm no service references the old path; both instances run from one commit.
* **Risk:** low. **Rollback:** un-archive (kept until confidence high).
* **Proof:** `systemctl show … -p WorkingDirectory` for all four units points at the single
  checkout; rsync ritual retired (closes TECH_DEBT PR7).

---

## 5. Risks & security notes

* **IG dirty-state loss** — the uncommitted IG edits are the highest-risk artifact; C0 freeze
  is mandatory before anything else.
* **Shared `JWT_SECRET` / `API_TOKEN`** across instances (verified same key set) — a single
  leaked token would span both dashboards. Consolidation is the moment to decide per-instance
  secrets (operator decision). **Never log secrets** (mirror the deployed `HARD_DISABLED_REJECT`
  audit which logs route+symbol only).
* **Duplicate execution window** at C7 — strictly stop-old-before-start-new.
* **IG inherits the hard-disabled guard** post-consolidation — a *safety improvement*, but the
  IG config must first pass `validate_hard_disabled_instruments` (verify in C2) or the IG bot
  will (correctly) refuse to start.
* **Per-instance DBs are data** — must be backed up and moved with hashes, never regenerated.

---

## 6. Scope confirmation

No file in either checkout was modified; no service changed/restarted; no merge/deploy; no
broker calls. Consolidation **timing** is an operator decision (operator-decisions doc); this
document is the grounded plan only. Operator approval required before executing C0+.
