# CogniflowAI Trading Bot — Comprehensive Fix Plan
Generated: 2026-03-13

## Overview
Based on external code review, 47 issues identified across 5 priority groups.
Current rating: 4.5/10 for real money. Target: 8.5/10 after all fixes.

---

## Group 1 — Safety Critical 🔴
*Fix before running with any real money*

### 1.1 Fill Price vs Bar Price (CRITICAL)
**Files:** `bot/orders.py`, `bot/layer1.py`, `bot/layer2.py`, `bot/layer3_silver.py`
**Problem:** Bot records bar price at signal time, not actual broker fill price
**Impact:** All trail stops, P&L, learning loop, Telegram alerts based on wrong price
**Fix:**
- In `_wait_for_fill()`, capture `trade.orderStatus.avgFillPrice`
- Return `(True, fill_price)` tuple instead of just `True`
- Pass fill_price to `tracker.on_open()`, `learning_loop.post_trade()`, alerts

### 1.2 Timed-Out Orders Not Cancelled (CRITICAL)
**Files:** `bot/orders.py`
**Problem:** If order not filled in 30s, bot logs failure but leaves order live at IBKR
**Impact:** Ghost orders accumulate, unexpected fills later
**Fix:**
- After 30s timeout, explicitly call `ib.cancelOrder(trade.order)`
- Wait 5s for cancellation confirmation
- Send Telegram alert with order details
- Return `(False, 0)` only after confirmed cancellation

### 1.3 Partial Fills Treated as Success (CRITICAL)
**Files:** `bot/orders.py`
**Problem:** `return True` as soon as `filled > 0`, even if only 1 of 10 shares filled
**Impact:** Tracker thinks full position exists, wrong qty for trail stops
**Fix:**
- Only return `(True, fill_price)` when `filled == totalQuantity`
- On partial fill after timeout: cancel remainder, record actual qty filled
- Update tracker with actual filled qty not configured qty

### 1.4 Short P&L Calculation Wrong (CRITICAL)
**Files:** `bot/portfolio.py`
**Problem:** `(current - avg_cost) * abs(qty)` is correct for longs, wrong for shorts
**Impact:** Shows profit when actually losing on short, vice versa
**Fix:**
```python
if qty > 0:  # long
    unreal_pnl = (current_price - avg_cost) * qty
    pnl_pct = ((current_price - avg_cost) / avg_cost) * 100
elif qty < 0:  # short
    unreal_pnl = (avg_cost - current_price) * abs(qty)
    pnl_pct = ((avg_cost - current_price) / avg_cost) * 100
```

### 1.5 Total P&L Only Checks USD (CRITICAL)
**Files:** `bot/portfolio.py`
**Problem:** `get_total_pnl()` only reads USD UnrealizedPnL tag
**Impact:** SHEL, BARC, ANTO, SGLN, SSLN GBP positions ignored
**Impact:** Emergency stop can trigger too late or not at all
**Fix:**
- Read all currency P&L values from `ib.accountValues()`
- Convert GBP to USD using live FX rate from IBKR
- Sum all currencies for true total P&L

### 1.6 Layer 3 Force-Sell Broken During BST (CRITICAL)
**Files:** `bot/layer3_silver.py`
**Problem:** Force-sell hardcoded at 16:15 UTC, but LSE closes 15:30 UTC during BST
**Impact:** Bot can hold silver overnight during summer months
**Fix:**
- Use `pytz` Europe/London timezone for close time
- Calculate force-sell time as 15 minutes before market close in local time
- Test for both GMT (16:15 UTC) and BST (15:15 UTC) scenarios

### 1.7 Layer 2 Ignores Order Success/Failure (CRITICAL)
**Files:** `bot/layer2.py`
**Problem:** Sets "BOUGHT DIP" action regardless of whether order actually filled
**Impact:** Dashboard shows wrong positions, P&L incorrect
**Fix:**
- Check return value from `self.orders.place()`
- Only set BOUGHT/SOLD action if fill confirmed
- Refresh position from IBKR after order before building row

### 1.8 Layer 2 Returns Stale Position (HIGH)
**Files:** `bot/layer2.py`
**Problem:** Reads `pos` before placing order, returns same value after
**Impact:** Dashboard shows pre-trade position for one full cycle
**Fix:**
- After successful order, call `self.portfolio.get_position(symbol)` again
- Use fresh position in returned row

---

## Group 2 — Security 🔴
*Fix before exposing to internet*

### 2.1 API Server Unauthenticated (CRITICAL)
**Files:** `api_server.py`
**Problem:** Anyone who can reach port 8081 can read/modify bot config
**Impact:** Attacker could disable all instruments, change loss limits, etc.
**Fix:**
- Add `API_TOKEN` to `.env` file
- Add token check middleware to Flask:
```python
@app.before_request
def check_auth():
    token = request.headers.get('X-API-Token')
    if token != os.getenv('API_TOKEN'):
        return jsonify({'error': 'Unauthorized'}), 401
```
- Update `instruments.html` to send token in all requests
- Bind server to `127.0.0.1` not `0.0.0.0`

### 2.2 Credentials in docker-compose.yml (CRITICAL)
**Files:** `docker-compose.yml`
**Problem:** IB Gateway username/password stored in plain text in committed file
**Fix:**
- Move credentials to `.env` file
- Reference in docker-compose: `${IB_USERNAME}`, `${IB_PASSWORD}`
- Add `.env` to `.gitignore`
- Create `.env.example` with placeholder values

### 2.3 CORS Too Permissive (HIGH)
**Files:** `api_server.py`
**Problem:** `CORS(app)` allows requests from any origin
**Fix:**
- Restrict to specific origin: `CORS(app, origins=['http://188.166.150.137:8080'])`
- Or restrict to localhost only since API is internal

### 2.4 No Config Validation Before Save (HIGH)
**Files:** `api_server.py`
**Problem:** Malformed but valid JSON can be saved, breaking bot startup
**Fix:**
- Validate required fields before saving:
  - `settings` section with all required keys
  - `layer1_active` is a list
  - Each instrument has symbol, name, sec_type, exchange, currency, qty
- Return 400 with specific error if validation fails

---

## Group 3 — Market Hours DST Fixes 🟡

### 3.1 LSE Hours Wrong During BST (HIGH)
**Files:** `bot/market_hours.py`
**Problem:** Hardcoded `LSE_OPEN = (8, 0)` and `LSE_CLOSE = (16, 30)` in UTC
**Impact:** During BST (last Sun Mar - last Sun Oct), LSE is 07:00-15:30 UTC
**Fix:**
```python
import pytz
from datetime import datetime

def is_lse_open(self) -> bool:
    london = pytz.timezone('Europe/London')
    now_london = datetime.now(london)
    if now_london.weekday() >= 5:
        return False
    market_open  = now_london.replace(hour=8,  minute=0,  second=0)
    market_close = now_london.replace(hour=16, minute=30, second=0)
    return market_open <= now_london < market_close
```

### 3.2 US Hours Wrong During EDT (HIGH)
**Files:** `bot/market_hours.py`
**Problem:** Hardcoded `US_OPEN = (14, 30)` and `US_CLOSE = (21, 0)` in UTC
**Impact:** During EDT (Mar-Nov), US market is 13:30-20:00 UTC
**Fix:**
```python
def is_us_open(self) -> bool:
    eastern = pytz.timezone('America/New_York')
    now_eastern = datetime.now(eastern)
    if now_eastern.weekday() >= 5:
        return False
    market_open  = now_eastern.replace(hour=9,  minute=30, second=0)
    market_close = now_eastern.replace(hour=16, minute=0,  second=0)
    return market_open <= now_eastern < market_close
```

### 3.3 EUR Instruments Use Wrong Hours (MEDIUM)
**Files:** `bot/market_hours.py`
**Problem:** SU (Schneider Electric) uses London time approximation
**Fix:**
- Add `CET` timezone support for EUR instruments
- XETRA/Euronext: 09:00-17:30 CET

### 3.4 No Holiday Calendar (MEDIUM)
**Files:** `bot/market_hours.py`
**Problem:** Bot will try to trade on bank holidays
**Fix:**
- Add `holidays` Python package
- Check UK bank holidays for LSE
- Check US federal holidays for NYSE/NASDAQ
- Log when skipping due to holiday

---

## Group 4 — State Consistency 🟡

### 4.1 Tracker Reconciliation Every Cycle (HIGH)
**Files:** `bot/layer1.py`
**Problem:** Local tracker can drift from IBKR reality
**Fix:**
- At start of every cycle, call `reconcile_with_ibkr()`
- Get live positions from IBKR
- Add positions IBKR has that tracker doesn't know about
- Remove positions tracker has that IBKR doesn't
- Log all discrepancies

### 4.2 Emergency Stop Incomplete (HIGH)
**Files:** `bot/layer1.py`
**Problem:** `_close_all()` only closes Layer 1 instruments
**Impact:** Layer 2 and Layer 3 positions remain open after emergency stop
**Fix:**
- Pass Layer 2 and Layer 3 instrument lists to `_close_all()`
- Or use IBKR positions directly: close everything IBKR shows as open
- Clear all tracker states after closing
- Clear all Layer 3 state from DB

### 4.3 Short Position Tracker (MEDIUM)
**Files:** `bot/position_tracker.py`, `bot/layer1.py`
**Problem:** Tracker is long-only, breaks for short positions
**Fix:**
- Add `side` field to `PositionState` ('LONG' or 'SHORT')
- Invert peak/stop logic for shorts:
  - Track lowest price (not highest) as peak
  - Trail stop sits ABOVE entry (not below)
  - Exit when price rises above stop (not falls below)

### 4.4 Short Entry From Flat Missing (MEDIUM)
**Files:** `bot/layer1.py`
**Problem:** No `elif result.signal == -1` path when `pos == 0`
**Fix:**
```python
elif result.signal == -1 and not inst.get('long_only', True):
    # Open fresh short from flat
    allowed = all(p.pre_trade(inst, -1, result.confidence) for p in self.plugins)
    if allowed:
        action = self.orders.handle_signal(inst, result.signal, result.confidence, pos)
        if 'SHORTED' in action:
            fill_price = ...  # from order
            self.tracker.on_open(symbol, fill_price, -inst['qty'],
                                trail_stop_pct, currency, side='SHORT')
```

### 4.5 Daily Telegram Summary Sends Multiple Times (LOW)
**Files:** `bot/watchdog.py`
**Problem:** At 1-minute cycles, daily summary can send multiple times at 21:00
**Fix:**
- Track `last_summary_date` in watchdog
- Only send if `last_summary_date != today`
- Update `last_summary_date` after sending

### 4.6 Learning Loop Retrain Timing Wrong (LOW)
**Files:** `bot/plugins/learning_loop.py`
**Problem:** `cycle % 672 == 0` assumes 15-minute cycles
**Impact:** At 1-minute cycles, retrains every 11.2 hours not weekly
**Fix:**
- Use real time: `datetime.utcnow() - last_retrain_time > timedelta(days=7)`
- Store `last_retrain_time` in database

---

## Group 5 — Code Quality 🟢

### 5.1 Hardcoded Paths (HIGH)
**Files:** `bot/config.py`, `api_server.py`, `bot/logger.py`,
          `bot/position_tracker.py`, `bot/plugins/learning_loop.py`
**Fix:**
```python
# In each file, replace ~/trading/... with:
from pathlib import Path
BASE_DIR = Path(__file__).parent.parent
CONFIG_FILE = BASE_DIR / 'instruments.json'
LOG_FILE    = BASE_DIR / 'portfolio_bot.log'
DB_FILE     = BASE_DIR / 'trades.db'
```

### 5.2 requirements.txt Missing (HIGH)
**Fix:** Create with pinned versions:
```
ib_insync==0.9.86
pandas>=1.5.0
numpy>=1.23.0
flask>=3.0.0
flask-cors>=4.0.0
python-dotenv>=1.0.0
anthropic>=0.18.0
pytz>=2023.3
holidays>=0.46
```

### 5.3 No .env.example (HIGH)
**Fix:** Create `.env.example`:
```
# IB Gateway credentials
IB_USERNAME=your_username
IB_PASSWORD=your_password
IB_ACCOUNT=DUQ123456

# Telegram alerts
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# API security
API_TOKEN=generate_a_random_token_here

# Anthropic (optional - for sentiment gate)
ANTHROPIC_API_KEY=your_key_here
```

### 5.4 Startup Validation (HIGH)
**Files:** `main.py`
**Fix:** Add `validate_environment()`:
```python
def validate_environment():
    checks = [
        ('instruments.json', lambda: CONFIG_FILE.exists()),
        ('web directory writable', lambda: os.access(WEB_DIR, os.W_OK)),
        ('database accessible', lambda: test_db_connection()),
        ('TELEGRAM_TOKEN set', lambda: bool(os.getenv('TELEGRAM_TOKEN'))),
        ('IBKR reachable', lambda: test_ibkr_connection()),
    ]
    failed = []
    for name, check in checks:
        try:
            if check():
                log(f"  ✅ {name}")
            else:
                log(f"  ❌ {name}", "ERROR")
                failed.append(name)
        except Exception as e:
            log(f"  ❌ {name}: {e}", "ERROR")
            failed.append(name)
    if failed:
        log(f"Startup failed: {failed}", "ERROR")
        sys.exit(1)
```

### 5.5 Stale Data Check (MEDIUM)
**Files:** `bot/data.py`
**Problem:** Bot trusts latest bar without checking freshness
**Fix:**
```python
# Check bar is recent (within 2 trading days)
last_bar_date = df['date'].iloc[-1]
if (pd.Timestamp.now() - pd.Timestamp(last_bar_date)).days > 2:
    log(f"Stale data for {contract.symbol}: last bar {last_bar_date}", "WARN")
    return None
```

### 5.6 No Unit Tests (MEDIUM)
**Fix:** Create `tests/` directory with:
- `test_indicators.py` — test Alligator, MA200, W%R, RSI, ADX calculations
- `test_signals.py` — test triple confirmation logic
- `test_market_hours.py` — test DST handling
- `test_portfolio.py` — test long/short P&L calculations
- `test_position_tracker.py` — test trail stop logic

### 5.7 Broad Exception Handlers (LOW)
**Fix:** Replace generic `except Exception` with specific exceptions:
```python
# Instead of:
except Exception as e:
    log(f"Error: {e}", "WARN")
    return None

# Use:
except ConnectionError as e:
    log(f"Connection lost: {e}", "WARN")
    return None
except ValueError as e:
    log(f"Invalid data: {e}", "WARN")
    return None
```

### 5.8 Stale Docstrings (LOW)
**Files:** `bot/logger.py`, `main.py`, `bot/layer2.py`, `bot/layer3_silver.py`
**Fix:** Update all docstrings to match current behaviour

### 5.9 .gitignore Fixes (LOW)
**Fix:**
- Remove `.env.example` from `.gitignore` (it should be tracked)
- Add `backups/` to `.gitignore`
- Add `*.db` to `.gitignore`
- Add `web/data.json` to `.gitignore`

### 5.10 Indicator Edge Cases (LOW)
**Files:** `bot/indicators.py`
**Fix:**
```python
# Williams %R zero denominator
if (high_max == low_min).any():
    wr = wr.replace([np.inf, -np.inf], -50)
    wr = wr.fillna(-50)

# ADX zero ATR
if atr == 0:
    return ADXResult(value=0, trend='NONE')
```

---

## Implementation Order

### Phase 1 — Stop Bot, Apply Critical Fixes (Day 1)
```
1.1 Fill price accuracy
1.2 Order timeout cancellation
1.3 Partial fill handling
1.4 Short P&L fix
1.5 Multi-currency P&L
1.6 Layer 3 BST fix
1.7 Layer 2 fill check
1.8 Layer 2 stale position
2.1 API authentication
2.2 Credentials to .env
```

### Phase 2 — Market Hours + State (Day 2)
```
2.3 CORS restriction
2.4 Config validation
3.1 LSE DST fix
3.2 US EDT fix
3.3 EUR hours fix
4.1 Tracker reconciliation
4.2 Emergency stop fix
4.5 Daily summary dedup
4.6 Retrain timing fix
```

### Phase 3 — Code Quality (Day 3)
```
5.1 Hardcoded paths
5.2 requirements.txt
5.3 .env.example
5.4 Startup validation
5.5 Stale data check
5.7 Exception handlers
5.8 Docstrings
5.9 .gitignore
```

### Phase 4 — Advanced (Day 4+)
```
3.4 Holiday calendar
4.3 Short tracker
4.4 Short entry from flat
5.6 Unit tests
5.10 Indicator edge cases
```

---

## Files To Be Modified

| File | Issues | Priority |
|------|--------|----------|
| bot/orders.py | 1.1, 1.2, 1.3 | 🔴 Critical |
| bot/portfolio.py | 1.4, 1.5 | 🔴 Critical |
| bot/layer1.py | 1.7, 4.1, 4.2, 4.4 | 🔴 Critical |
| bot/layer2.py | 1.7, 1.8 | 🔴 Critical |
| bot/layer3_silver.py | 1.6 | 🔴 Critical |
| api_server.py | 2.1, 2.3, 2.4, 5.1 | 🔴 Critical |
| docker-compose.yml | 2.2 | 🔴 Critical |
| bot/market_hours.py | 3.1, 3.2, 3.3, 3.4 | 🟡 High |
| bot/position_tracker.py | 4.3, 5.1 | 🟡 High |
| bot/watchdog.py | 4.5 | 🟡 Medium |
| bot/plugins/learning_loop.py | 4.6, 5.1 | 🟡 Medium |
| bot/config.py | 5.1 | 🟢 Normal |
| bot/logger.py | 5.1, 5.8 | 🟢 Normal |
| bot/data.py | 5.5 | 🟢 Normal |
| bot/indicators.py | 5.10 | 🟢 Normal |
| main.py | 5.4, 5.8 | 🟢 Normal |
| requirements.txt | 5.2 | 🟢 Normal |
| .env.example | 5.3 | 🟢 Normal |
| .gitignore | 5.9 | 🟢 Normal |

---

## Expected Outcome

After Phase 1+2: **7.5/10** for real money
After Phase 3:   **8.0/10** for real money
After Phase 4:   **8.5/10** for real money

Remaining 1.5 points requires:
- Real backtesting framework
- Proper position sizing model
- Slippage/spread modelling
- Full holiday calendar
- Comprehensive test suite
