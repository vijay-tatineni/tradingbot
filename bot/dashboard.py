"""
bot/dashboard.py  — v7.0
Writes data.json and dashboard.html every cycle.
Font: JetBrains Mono (Google Fonts CDN + local woff2 fallback)
Subtitle colour: blue (was green — avoids confusion with buy signals)
Entry price: bright white
"""

import json, os, datetime, sqlite3
from pathlib import Path
from bot.config import Config
from bot.currency import convert_pnl_to_base, is_pence_instrument
from bot.logger import log

_PNL_DB = str(Path(__file__).parent.parent / 'positions.db')

def _pnl_connect(db_path=None):
    conn = sqlite3.connect(db_path or _PNL_DB)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("""CREATE TABLE IF NOT EXISTS pnl_cache (
        currency TEXT PRIMARY KEY,
        pnl_value REAL NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    return conn

def _load_pnl_cache(db_path=None):
    try:
        conn = _pnl_connect(db_path)
        rows = conn.execute("SELECT currency, pnl_value FROM pnl_cache").fetchall()
        conn.close()
        pnl_by_ccy = {r[0]: r[1] for r in rows}
        return {"total_pnl": sum(pnl_by_ccy.values()), "pnl_by_currency": pnl_by_ccy}
    except Exception:
        return {"total_pnl": 0, "pnl_by_currency": {}}

def _save_pnl_cache(total_pnl, pnl_by_ccy, db_path=None):
    try:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = _pnl_connect(db_path)
        for ccy, val in pnl_by_ccy.items():
            if val != 0:
                conn.execute(
                    "INSERT OR REPLACE INTO pnl_cache (currency, pnl_value, updated_at) VALUES (?, ?, ?)",
                    (ccy, val, now))
        conn.commit()
        conn.close()
    except Exception:
        pass


class Dashboard:

    def __init__(self, cfg: Config):
        self.cfg      = cfg
        self.data_dir = cfg.web_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self._write_html()
        _pnl_connect()
        log(f"P&L cache: SQLite table in {_PNL_DB}")
        log(f"Dashboard ready: {self.data_dir}/dashboard.html")

    def update(self, cycle, signal_rows, accum_rows,
               total_pnl, lse_open, us_open):
        pos_list = [
            {'symbol': r['symbol'], 'qty': r['pos'],
             'avg_cost': r.get('avg_cost', 0),
             'currency': r.get('currency', 'USD'),
             'unreal_pnl': r.get('unreal_pnl', 0),
             'pnl_pct': r.get('pnl_pct', 0)}
            for r in signal_rows if r.get('pos', 0) != 0
        ]
        pnl_by_ccy = {}
        for r in signal_rows:
            pos = r.get('pos', 0)
            if pos == 0:
                continue
            ccy = r.get('currency', 'USD')
            pnl_val = r.get('unreal_pnl', 0)
            if pnl_val == 0:
                # Fallback: compute from last known price vs entry
                price = r.get('price', 0)
                avg_cost = r.get('avg_cost', 0)
                if price > 0 and avg_cost > 0:
                    raw_pnl = (price - avg_cost) * pos
                    if is_pence_instrument(ccy):
                        raw_pnl = convert_pnl_to_base(raw_pnl, ccy)
                    pnl_val = round(raw_pnl, 2)
            pnl_by_ccy[ccy] = round(pnl_by_ccy.get(ccy, 0) + pnl_val, 2)
        pnl_by_ccy = {k: v for k, v in pnl_by_ccy.items() if v != 0}

        has_open_positions = any(r.get('pos', 0) != 0 for r in signal_rows)

        cached = _load_pnl_cache()
        cached_pnl = cached.get("pnl_by_currency", {})

        for ccy, val in pnl_by_ccy.items():
            if val != 0:
                cached_pnl[ccy] = val

        pnl_by_ccy = {k: v for k, v in cached_pnl.items() if v != 0}

        if pnl_by_ccy:
            total_pnl = sum(pnl_by_ccy.values())

        if pnl_by_ccy or has_open_positions:
            _save_pnl_cache(total_pnl, pnl_by_ccy)

        data = {
            'last_updated':        datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
            'check_interval_mins': self.cfg.check_interval_mins,
            'cycle':               cycle,
            'total_pnl':           round(total_pnl, 2),
            'pnl_by_currency':     pnl_by_ccy,
            'risk_pct':            round(min(abs(total_pnl / self.cfg.portfolio_loss_limit) * 100, 100), 1),
            'loss_limit':          self.cfg.portfolio_loss_limit,
            'lse_open':            lse_open,
            'us_open':             us_open,
            'positions':           pos_list,
            'signals':             signal_rows + self._disabled_instrument_rows(),
            'accum':               accum_rows,
            'config_file':         self.cfg.path,
            'instrument_count':    {'active': len(self.cfg.active_instruments), 'accum': len(self.cfg.accum_instruments)},
            'broker_name':         getattr(self.cfg, 'broker_type', 'ibkr').upper(),
        }
        path = os.path.join(self.data_dir, 'data.json')
        tmp_path = path + '.tmp'
        with open(tmp_path, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
        log(f"Dashboard updated — cycle #{cycle}")

    def _disabled_instrument_rows(self) -> list:
        """Generate greyed-out rows for disabled instruments."""
        rows = []
        for inst in self.cfg._raw.get('layer1_active', []):
            if inst.get('enabled', True):
                continue
            rows.append({
                'symbol':     inst['symbol'],
                'name':       inst['name'],
                'flag':       inst.get('flag', ''),
                'market':     '--',
                'price':      0.0,
                'alligator':  '--',
                'direction':  '--',
                'ma200':      '--',
                'wr':         0.0,
                'rsi':        50.0,
                'confidence': '--',
                'signal':     'DISABLED',
                'pos':        0,
                'avg_cost':   0,
                'unreal_pnl': 0,
                'pnl_pct':    0,
                'currency':   inst.get('currency', 'USD'),
                'stop_level': 0,
                'peak_price': 0,
                'watching':   0,
                'action':     'DISABLED',
                'reason':     inst.get('disabled_reason', ''),
                'disabled':   True,
            })
        return rows

    def _write_html(self):
        interval = self.cfg.check_interval_mins
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Trading Bot Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@100..800&display=swap" rel="stylesheet">
<style>
@font-face {{
  font-family: 'JetBrains Mono';
  src: url('JetBrainsMono.woff2') format('woff2');
  font-weight: 100 900;
  font-style: normal;
}}
:root {{
  --bg:#0d1117; --bg2:#161b22; --bg3:#1c2128;
  --green:#22c55e; --red:#ef4444; --gold:#f5c842;
  --blue:#38bdf8; --muted:#8b949e; --border:#30363d;
  --text:#ffffff; --navy:#0f3460;
}}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ background:var(--bg); color:var(--text); font-family:'JetBrains Mono',monospace; font-size:0.8rem; }}
.header {{ padding:16px 24px; border-bottom:1px solid var(--border); }}
.header h1 {{ color:var(--gold); font-size:1.4rem; font-weight:700; }}
.updated {{ color:var(--muted); font-size:0.7rem; margin-top:4px; }}
.config-bar {{ background:#111827; border-bottom:1px solid var(--border); padding:8px 24px; font-size:0.68rem; color:var(--blue); }}
.cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; padding:16px 24px; }}
.card {{ background:var(--bg2); border:1px solid var(--border); border-radius:8px; padding:16px; }}
.card-label {{ font-size:0.62rem; color:var(--muted); text-transform:uppercase; letter-spacing:1px; }}
.card-value {{ font-size:1.6rem; font-weight:700; margin-top:6px; }}
.card-sub {{ font-size:0.65rem; color:var(--muted); margin-top:4px; }}
.pnl-pos {{ color:var(--green); }} .pnl-neg {{ color:var(--red); }}
.risk-bar {{ height:4px; background:var(--border); border-radius:2px; margin-top:8px; }}
.risk-fill {{ height:4px; border-radius:2px; transition:width 0.5s; }}
.section {{ background:var(--bg2); border:1px solid var(--border); border-radius:8px; margin:0 24px 16px; overflow:hidden; }}
.section-title {{ padding:12px 16px; font-size:0.72rem; color:var(--gold); border-bottom:1px solid var(--border); letter-spacing:2px; font-weight:600; }}
table {{ width:100%; border-collapse:collapse; }}
th {{ padding:8px 10px; text-align:left; font-size:0.62rem; color:var(--muted); text-transform:uppercase; letter-spacing:1px; border-bottom:1px solid var(--border); font-weight:500; white-space:nowrap; }}
td {{ padding:8px 10px; border-bottom:1px solid #1c2128; font-size:0.72rem; color:var(--text); white-space:nowrap; }}
tr:hover td {{ background:#1c2128; }}
.asset-name {{ font-weight:700; color:var(--text); font-size:0.78rem; }}
.asset-sub  {{ font-size:0.62rem; color:var(--blue); margin-top:2px; }}
.entry-price {{ color:var(--text); font-weight:500; font-size:0.72rem; }}
.badge {{ display:inline-block; padding:2px 8px; border-radius:4px; font-size:0.62rem; font-weight:700; }}
.badge-open {{ background:#052e16; color:var(--green); }}
.badge-closed {{ background:#1f2937; color:var(--muted); }}
.badge-247 {{ background:#0c2461; color:var(--blue); }}
.badge-high {{ background:#713f12; color:#fbbf24; }}
.badge-med {{ background:#1e3a5f; color:var(--blue); }}
.badge-low {{ background:#1f2937; color:var(--muted); }}
.badge-buy {{ background:#052e16; color:var(--green); padding:2px 10px; }}
.badge-sell {{ background:#2d0a0a; color:var(--red); padding:2px 10px; }}
.badge-hold {{ background:#1f2937; color:var(--muted); padding:2px 10px; }}
.pos-badge {{ background:var(--navy); color:var(--blue); padding:2px 8px; border-radius:4px; font-size:0.65rem; font-weight:700; }}
.green {{ color:var(--green); }} .red {{ color:var(--red); }}
.gold {{ color:var(--gold); }} .muted {{ color:var(--muted); }}
.reason {{ font-size:0.6rem; color:var(--muted); max-width:280px; white-space:normal; }}
.market-grid {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }}
.pos-list {{ display:flex; flex-direction:column; gap:6px; margin-top:8px; }}
.pos-item {{ display:flex; gap:8px; align-items:baseline; font-size:0.7rem; }}
.pos-sym {{ color:var(--gold); font-weight:700; min-width:65px; }}
.pos-detail {{ color:var(--muted); font-size:0.63rem; }}

/* Regime layer */
.regime-nav {{ display:flex; gap:2px; padding:8px 16px; border-bottom:1px solid var(--border); background:var(--bg3); flex-wrap:wrap; }}
.regime-tab {{ background:transparent; border:1px solid transparent; color:var(--muted); padding:6px 14px; font-family:'JetBrains Mono',monospace; font-size:0.7rem; cursor:pointer; border-radius:4px; letter-spacing:1px; }}
.regime-tab:hover {{ color:var(--text); border-color:var(--border); }}
.regime-tab.active {{ color:var(--gold); border-color:var(--gold); }}
.regime-tab .count {{ color:var(--muted); margin-left:6px; font-size:0.62rem; }}
.regime-tab.active .count {{ color:var(--gold); }}
.regime-pane {{ display:none; }}
.regime-pane.active {{ display:block; }}
.regime-empty {{ padding:24px 16px; color:var(--muted); font-size:0.72rem; line-height:1.5; }}
.regime-meta {{ padding:8px 16px; color:var(--muted); font-size:0.62rem; border-bottom:1px solid #1c2128; text-transform:uppercase; letter-spacing:1px; }}
.disagreement-row td {{ background:#3b0a0a; }}
.disagreement-row td:first-child {{ box-shadow:inset 3px 0 0 var(--red); }}
.regime-bool-yes {{ color:var(--green); font-weight:700; }}
.regime-bool-no {{ color:var(--red); font-weight:700; }}
.regime-pill {{ display:inline-block; padding:2px 8px; border-radius:4px; font-size:0.62rem; font-weight:700; background:#1f2937; color:var(--blue); }}
.regime-pill.regime-TRENDING {{ background:#052e16; color:var(--green); }}
.regime-pill.regime-RANGING {{ background:#1e3a5f; color:var(--blue); }}
.regime-pill.regime-VOLATILE {{ background:#713f12; color:#fbbf24; }}
.regime-pill.regime-UNCLEAR {{ background:#1f2937; color:var(--muted); }}
.engine-pill {{ display:inline-block; padding:2px 8px; border-radius:4px; font-size:0.62rem; font-weight:700; background:#0c2461; color:var(--blue); }}
.engine-pill.engine-NoOpEngine {{ background:#1f2937; color:var(--muted); }}
</style>
</head>
<body>

<!-- Nav Bar -->
<div style="display:flex;align-items:center;padding:10px 24px;background:#161b22;border-bottom:1px solid #30363d;gap:8px;">
  <span style="color:#f5c842;font-weight:700;font-size:0.82rem;margin-right:8px;">CogniflowAI</span><span id="navBroker" style="color:#38bdf8;font-size:0.68rem;margin-right:8px;"></span>
  <a href="dashboard.html" style="font-size:0.72rem;text-decoration:none;padding:6px 14px;border-radius:4px;color:#f5c842;border:1px solid #f5c842;font-family:'JetBrains Mono',monospace;">Dashboard</a>
  <a href="instruments.html" style="font-size:0.72rem;text-decoration:none;padding:6px 14px;border-radius:4px;color:#8b949e;border:1px solid transparent;font-family:'JetBrains Mono',monospace;">Instruments</a>
  <a href="tests.html" style="font-size:0.72rem;text-decoration:none;padding:6px 14px;border-radius:4px;color:#8b949e;border:1px solid transparent;font-family:'JetBrains Mono',monospace;">Tests</a>
  <a href="advisor.html" style="font-size:0.72rem;text-decoration:none;padding:6px 14px;border-radius:4px;color:#8b949e;border:1px solid transparent;font-family:'JetBrains Mono',monospace;">Advisor</a>
  <div style="flex:1;"></div>
  <span style="color:#22c55e;font-size:0.65rem;padding:6px 0;">Logged in as: <span id="navUser">--</span></span>
  <button onclick="logout()" style="color:#ef4444;border:1px solid #ef4444;cursor:pointer;background:none;font-weight:600;font-family:'JetBrains Mono',monospace;font-size:0.72rem;padding:6px 14px;border-radius:4px;">Logout</button>
</div>

<div class="header">
  <h1>Trading Bot Dashboard</h1>
  <div class="updated" id="updated">Last updated: --</div>
</div>

<div class="config-bar" id="configBar">Loading...</div>

<div class="cards">
  <div class="card">
    <div class="card-label">Total P&L</div>
    <div class="card-value" id="pnl">--</div>
    <div class="risk-bar"><div class="risk-fill" id="riskFill" style="width:0%"></div></div>
    <div class="card-sub" id="riskPct">--</div>
  </div>
  <div class="card">
    <div class="card-label">Cycle</div>
    <div class="card-value gold" id="cycle">--</div>
    <div class="card-sub" id="cycleInterval">every {interval} min</div>
  </div>
  <div class="card">
    <div class="card-label">Markets</div>
    <div class="market-grid" id="markets">--</div>
  </div>
  <div class="card">
    <div class="card-label">Open Positions</div>
    <div class="pos-list" id="positions">--</div>
  </div>
</div>

<div class="section">
  <div class="section-title">// LAYER 1 — ACTIVE TRADING · TRIPLE CONFIRMATION</div>
  <table>
    <thead><tr>
      <th>Asset</th><th>Market</th><th>Price</th>
      <th>Entry</th><th>P&amp;L</th><th>P&amp;L%</th>
      <th>Alligator</th><th>Dir</th><th>MA200</th>
      <th>W%R</th><th>RSI</th><th>Conf</th>
      <th>Signal</th><th>Pos</th><th>Action · Reason</th>
    </tr></thead>
    <tbody id="layer1"></tbody>
  </table>
</div>

<div class="section">
  <div class="section-title">// LAYER 2 — ACCUMULATION ETFs · RSI DIP BUYING</div>
  <table>
    <thead><tr><th>ETF</th><th>Price</th><th>RSI</th><th>W%R</th><th>Position</th><th>Action</th></tr></thead>
    <tbody id="layer2"></tbody>
  </table>
</div>

<div class="section">
  <div class="section-title">// REGIME LAYER</div>
  <div class="regime-nav" id="regimeNav">
    <button class="regime-tab active" data-tab="regime">Regime</button>
    <button class="regime-tab" data-tab="overlays">Overlays</button>
    <button class="regime-tab" data-tab="routing">Routing</button>
    <button class="regime-tab" data-tab="shadow">Shadow vs Live</button>
    <button class="regime-tab" data-tab="degradation">Degradation</button>
    <button class="regime-tab" data-tab="pauses">Pauses</button>
  </div>
  <div class="regime-meta" id="regimeMeta">Last refresh: --</div>
  <div class="regime-pane active" id="regime-pane-regime"><div class="regime-empty">Loading...</div></div>
  <div class="regime-pane" id="regime-pane-overlays"></div>
  <div class="regime-pane" id="regime-pane-routing"></div>
  <div class="regime-pane" id="regime-pane-shadow"></div>
  <div class="regime-pane" id="regime-pane-degradation"></div>
  <div class="regime-pane" id="regime-pane-pauses"></div>
</div>

<script>
function ccySymbol(c) {{ return c==='GBP'?'£':c==='EUR'?'€':'$'; }}
function isGBPPence(c) {{ return c==='GBP'; }}

function formatPrice(price, currency) {{
  if (price <= 0) return '<span class="muted">--</span>';
  if (isGBPPence(currency)) {{
    const p = Math.round(price);
    if (p >= 1000) {{
      return p.toLocaleString() + 'p (£' + (price/100).toFixed(2) + ')';
    }}
    return p.toLocaleString() + 'p';
  }}
  return ccySymbol(currency) + price.toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
}}

function formatEntry(cost, currency) {{
  if (cost <= 0) return '<span class="muted">--</span>';
  if (isGBPPence(currency)) {{
    return '£' + (cost/100).toFixed(2);
  }}
  return ccySymbol(currency) + cost.toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
}}

function formatPnl(pnl, currency) {{
  if (pnl === 0) return '<span class="muted">--</span>';
  const sign = pnl >= 0 ? '+' : '';
  const cls = pnl >= 0 ? 'green' : 'red';
  const sym = ccySymbol(currency);
  return `<span class="${{cls}}">${{sign}}${{sym}}${{Math.abs(pnl).toFixed(2)}}</span>`;
}}

function formatPosPrice(cost, currency) {{
  if (isGBPPence(currency)) {{
    return Math.round(cost) + 'p';
  }}
  return ccySymbol(currency) + cost.toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
}}

async function load() {{
  try {{
    const r = await fetch('data.json?t=' + Date.now());
    const d = await r.json();

    document.getElementById('updated').textContent = 'Last updated: ' + d.last_updated;
    const brokerName = d.broker_name || 'IBKR';
    document.querySelector('h1').textContent = 'Trading Bot Dashboard — ' + brokerName;
    const navBroker = document.getElementById('navBroker');
    if (navBroker) navBroker.textContent = '[' + brokerName + ']';
    document.getElementById('configBar').textContent =
      '📋 Config: ' + d.config_file + ' — ' + d.instrument_count.active + ' active, ' + d.instrument_count.accum + ' accumulation';
    document.getElementById('cycleInterval').textContent = 'every ' + (d.check_interval_mins||15) + ' min';

    const pnlEl = document.getElementById('pnl');
    const pnlByCcy = d.pnl_by_currency || {{}};
    const entries = Object.entries(pnlByCcy).filter(([k, v]) => v !== 0);
    let pnlHtml = '';
    if (entries.length > 0) {{
      pnlHtml = entries.map(([ccy, val]) => {{
        const sign = val >= 0 ? '+' : '';
        const sym = ccy === 'GBP' ? '£' : ccy === 'EUR' ? '€' : '$';
        const color = val >= 0 ? '#27AE60' : '#E74C3C';
        return `<span style="color:${{color}}">${{sym}}${{sign}}${{val.toFixed(2)}}</span>`;
      }}).join(' <span style="color:#8B949E">|</span> ');
    }} else {{
      const t = d.total_pnl || 0;
      const color = t >= 0 ? '#27AE60' : '#E74C3C';
      pnlHtml = `<span style="color:${{color}}">$${{t.toFixed(2)}}</span>`;
    }}
    pnlEl.innerHTML = pnlHtml;
    document.getElementById('riskPct').textContent = 'Risk: ' + d.risk_pct + '% of $' + d.loss_limit + ' limit';
    const fill = document.getElementById('riskFill');
    fill.style.width = d.risk_pct + '%';
    fill.style.background = d.risk_pct > 70 ? '#ef4444' : d.risk_pct > 40 ? '#f59e0b' : '#22c55e';

    document.getElementById('cycle').textContent = '#' + d.cycle;

    document.getElementById('markets').innerHTML = [
      ['LSE', d.lse_open], ['US', d.us_open], ['Crypto', true],
    ].map(([n,o]) => `<span class="badge ${{o?'badge-open':'badge-closed'}}">${{n}} ${{o?'OPEN':'CLOSED'}}</span>`).join('');

    document.getElementById('positions').innerHTML = d.positions.length
      ? d.positions.map(p => {{
          const pnlStr = p.unreal_pnl !== 0
            ? ' ' + formatPnl(p.unreal_pnl, p.currency) : '';
          return `<div class="pos-item">
            <span class="pos-sym">${{p.symbol}}</span>
            <span class="pos-detail">${{p.qty}} @ ${{formatPosPrice(p.avg_cost, p.currency)}} ${{p.currency}}</span>${{pnlStr}}
          </div>`;
        }}).join('')
      : '<span class="muted">No open positions</span>';

    // Sort: open positions first (by P&L% desc), then flat (alphabetical), then disabled
    const openPos     = d.signals.filter(s => s.pos !== 0 && !s.disabled).sort((a,b) => (b.pnl_pct||0) - (a.pnl_pct||0));
    const flatPos     = d.signals.filter(s => s.pos === 0 && !s.disabled).sort((a,b) => a.symbol.localeCompare(b.symbol));
    const disabledPos = d.signals.filter(s => s.disabled).sort((a,b) => a.symbol.localeCompare(b.symbol));
    const sorted      = [...openPos, ...flatPos, ...disabledPos];

    function renderRow(s, isOpen) {{
      const isDisabled = s.disabled === true;
      const rowStyle   = isDisabled ? 'opacity:0.4' : '';
      const mktClass  = s.market==='24/7' ? 'badge-247' : s.market==='OPEN' ? 'badge-open' : 'badge-closed';
      const confClass = s.confidence==='HIGH' ? 'badge-high' : s.confidence==='MEDIUM' ? 'badge-med' : 'badge-low';
      const sigClass  = isDisabled ? 'badge-hold' : s.signal==='BUY' ? 'badge-buy' : s.signal==='SELL' ? 'badge-sell' : 'badge-hold';
      const wrColor   = s.wr >= -50 ? 'green' : 'red';
      const rsiColor  = s.rsi > 70 ? 'red' : s.rsi < 35 ? 'green' : '';
      const dot       = isOpen ? '<span style="color:#22c55e;font-size:0.55rem;margin-right:4px">●</span>' : '';

      const ccy = s.currency || 'USD';
      const entryStr = s.avg_cost > 0
        ? `<span class="entry-price">${{formatEntry(s.avg_cost, ccy)}}</span>`
        : '<span class="muted">--</span>';

      const pnlStr = formatPnl(s.unreal_pnl, ccy);
      const pctStr = s.pnl_pct !== 0
        ? `<span class="${{s.pnl_pct>=0?'green':'red'}}">${{s.pnl_pct>=0?'+':''}}${{s.pnl_pct.toFixed(2)}}%</span>`
        : '<span class="muted">--</span>';

      const priceStr = isDisabled ? '<span class="muted">--</span>' : formatPrice(s.price, ccy);
      const posStr   = s.pos !== 0 ? `<span class="pos-badge">${{s.pos}}</span>` : '<span class="muted">0</span>';
      const actColor = isDisabled ? 'muted'
                     : s.action&&s.action.includes('BOUGHT') ? 'green'
                     : s.action&&s.action.includes('CLOSED') ? 'red'
                     : s.action&&s.action.includes('BLOCKED') ? 'red' : 'muted';

      return `<tr style="${{rowStyle}}">
        <td>
          <div class="asset-name">${{dot}}${{s.flag}} ${{s.symbol}}</div>
          <div class="asset-sub">${{s.name}}</div>
        </td>
        <td><span class="badge ${{mktClass}}">${{s.market}}</span></td>
        <td>${{priceStr}}</td>
        <td>${{entryStr}}</td>
        <td>${{pnlStr}}</td>
        <td>${{pctStr}}</td>
        <td>${{s.alligator}}</td>
        <td>${{s.direction}}</td>
        <td class="muted">${{s.ma200}}</td>
        <td class="${{wrColor}}">${{s.wr}}</td>
        <td class="${{rsiColor}}">${{s.rsi}}</td>
        <td><span class="badge ${{confClass}}">${{s.confidence}}</span></td>
        <td><span class="badge ${{sigClass}}">${{isDisabled ? 'DISABLED' : s.signal}}</span></td>
        <td>${{posStr}}</td>
        <td>
          <span class="${{actColor}}">${{s.action}}</span>
          ${{s.reason ? '<br><span class="reason">'+s.reason+'</span>' : ''}}
        </td>
      </tr>`;
    }}

    let rows = openPos.map(s => renderRow(s, true));
    if (openPos.length > 0 && flatPos.length > 0) {{
      rows.push('<tr class="divider-row"><td colspan="15" style="padding:0;border-bottom:2px solid #30363d"></td></tr>');
    }}
    rows = rows.concat(flatPos.map(s => renderRow(s, false)));
    if (disabledPos.length > 0) {{
      rows.push('<tr class="divider-row"><td colspan="15" style="padding:0;border-bottom:2px dashed #30363d"></td></tr>');
      rows = rows.concat(disabledPos.map(s => renderRow(s, false)));
    }}
    document.getElementById('layer1').innerHTML = rows.join('');

    document.getElementById('layer2').innerHTML = d.accum.map(e => {{
      const rsiColor = e.rsi > 70 ? 'red' : e.rsi < 35 ? 'green' : '';
      const wrColor  = e.wr < -80 ? 'red' : e.wr > -20 ? 'green' : 'muted';
      const ccy = e.currency || 'USD';
      return `<tr>
        <td><div class="asset-name">${{e.flag}} ${{e.symbol}}</div><div class="asset-sub">${{e.name}}</div></td>
        <td>${{formatPrice(e.price, ccy)}}</td>
        <td class="${{rsiColor}}">${{e.rsi}}</td>
        <td class="${{wrColor}}">${{e.wr}}</td>
        <td>${{e.pos !== 0 ? '<span class="pos-badge">'+e.pos+'</span>' : '0'}}</td>
        <td class="${{e.action!=='--'?'green':'muted'}}">${{e.action}}</td>
      </tr>`;
    }}).join('');

  }} catch(e) {{
    document.getElementById('updated').textContent = 'Error: ' + e.message;
  }}
}}
load();
setInterval(load, 30000);

// Nav auth
const _user = localStorage.getItem('jwt_user');
if (_user) document.getElementById('navUser').textContent = _user;
function logout() {{
  localStorage.removeItem('jwt_token');
  localStorage.removeItem('jwt_user');
  document.cookie = 'jwt_token=; path=/; max-age=0';
  window.location.href = 'login.html';
}}

// ──────────────────────────────────────────────────────
// Regime layer tabs (PR 6 §15.5)
// ──────────────────────────────────────────────────────
function regimeEscape(s) {{
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({{
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }}[c]));
}}

function regimePill(value) {{
  if (!value) return '<span class="muted">--</span>';
  const v = regimeEscape(value);
  return `<span class="regime-pill regime-${{v}}">${{v}}</span>`;
}}

function enginePill(value) {{
  if (!value) return '<span class="muted">--</span>';
  const v = regimeEscape(value);
  return `<span class="engine-pill engine-${{v}}">${{v}}</span>`;
}}

function formatTs(ts) {{
  if (!ts) return '<span class="muted">--</span>';
  try {{
    const d = new Date(ts);
    if (isNaN(d.getTime())) return regimeEscape(ts);
    return d.toISOString().slice(0, 19).replace('T', ' ');
  }} catch (err) {{
    return regimeEscape(ts);
  }}
}}

function regimeRenderTable(headers, rows) {{
  if (!rows || rows.length === 0) return null;
  const thead = '<thead><tr>' +
    headers.map(h => `<th>${{regimeEscape(h)}}</th>`).join('') + '</tr></thead>';
  const tbody = '<tbody>' + rows.map(r => {{
    if (Array.isArray(r)) {{
      return '<tr>' + r.map(c => `<td>${{c == null ? '' : c}}</td>`).join('') + '</tr>';
    }}
    const cls = r.rowClass ? ` class="${{r.rowClass}}"` : '';
    return `<tr${{cls}}>` + r.cells.map(c => `<td>${{c == null ? '' : c}}</td>`).join('') + '</tr>';
  }}).join('') + '</tbody>';
  return `<table>${{thead}}${{tbody}}</table>`;
}}

const REGIME_TABS = {{
  regime: {{
    url: '/api/regime/states',
    empty: "Classifier hasn't run yet — first run typically happens after market close.",
    render: rows => regimeRenderTable(
      ['Instrument', 'Raw regime', 'Confidence', 'Smoothed', 'Days in regime', 'Last classified'],
      rows.slice().sort((a, b) => (a.instrument || '').localeCompare(b.instrument || '')).map(r => [
        regimeEscape(r.instrument || ''),
        regimePill(r.raw_regime),
        r.confidence != null ? Number(r.confidence).toFixed(2) : '<span class="muted">--</span>',
        regimePill(r.smoothed_regime),
        r.days_in_regime != null ? String(r.days_in_regime) : '<span class="muted">--</span>',
        formatTs(r.last_classified),
      ])
    ),
  }},
  overlays: {{
    url: '/api/overlays/active',
    empty: "No overlays currently active. This is normal during shadow phase. (MACRO_LOCKOUT events from the calendar would appear here; DATA_QUALITY and LOW_LIQUIDITY require bot runtime context and are not displayed in this view — check the Pauses tab.)",
    render: rows => regimeRenderTable(
      ['Overlay', 'Reason', 'Instruments affected'],
      rows.map(r => [
        `<span class="engine-pill">${{regimeEscape(r.overlay_name || '')}}</span>`,
        regimeEscape(r.reason || ''),
        Array.isArray(r.instruments_affected) ? r.instruments_affected.map(regimeEscape).join(', ') : '',
      ])
    ),
  }},
  routing: {{
    url: '/api/routing/decisions',
    empty: "Router hasn't been invoked yet.",
    render: rows => regimeRenderTable(
      ['Instrument', 'Smoothed regime', 'Selected engine', 'Allow new entries', 'Block reason'],
      rows.slice().sort((a, b) => (a.instrument || '').localeCompare(b.instrument || '')).map(r => [
        regimeEscape(r.instrument || ''),
        regimePill(r.smoothed_regime),
        enginePill(r.selected_engine),
        r.allow_new_entries
          ? '<span class="regime-bool-yes">YES</span>'
          : '<span class="regime-bool-no">NO</span>',
        regimeEscape(r.block_reason || ''),
      ])
    ),
  }},
  shadow: {{
    url: '/api/shadow/comparison',
    empty: "No shadow decisions logged yet — bot needs to complete more cycles.",
    render: rows => regimeRenderTable(
      ['Timestamp', 'Instrument', 'Live engine', 'Live action', 'Shadow engine', 'Shadow action', 'Disagreement'],
      rows.map(r => {{
        const disagreed = r.disagreement_type && String(r.disagreement_type).trim() !== '';
        return {{
          cells: [
            formatTs(r.ts),
            regimeEscape(r.instrument || ''),
            enginePill(r.live_engine),
            regimeEscape(r.live_action_taken || ''),
            enginePill(r.shadow_engine_selected),
            regimeEscape(r.shadow_action_would_be || ''),
            disagreed
              ? `<span class="regime-bool-no">${{regimeEscape(r.disagreement_type)}}</span>`
              : '<span class="muted">—</span>',
          ],
          rowClass: disagreed ? 'disagreement-row' : '',
        }};
      }})
    ),
  }},
  degradation: {{
    url: '/api/degradation/events',
    empty: "No degradation events recorded. System is healthy.",
    render: rows => regimeRenderTable(
      ['Timestamp', 'Component', 'Severity', 'Trigger reason', 'Action taken'],
      rows.map(r => [
        formatTs(r.ts),
        `<span class="engine-pill">${{regimeEscape(r.component || '')}}</span>`,
        r.severity === 'hard'
          ? '<span class="regime-bool-no">HARD</span>'
          : `<span class="muted">${{regimeEscape(r.severity || '')}}</span>`,
        regimeEscape(r.trigger_reason || ''),
        regimeEscape(r.action_taken || ''),
      ])
    ),
  }},
  pauses: {{
    url: '/api/pauses/list',
    empty: "No instruments currently paused.",
    render: rows => regimeRenderTable(
      ['Instrument', 'Reason', 'Paused at', 'Overlay'],
      rows.map(r => [
        regimeEscape(r.instrument || ''),
        regimeEscape(r.reason || ''),
        formatTs(r.paused_at),
        `<span class="engine-pill">${{regimeEscape(r.paused_by_overlay || '')}}</span>`,
      ])
    ),
  }},
}};

let _activeRegimeTab = 'regime';
let _regimeRefreshTimer = null;

async function fetchRegimeTab(key) {{
  const cfg = REGIME_TABS[key];
  if (!cfg) return;
  const pane = document.getElementById('regime-pane-' + key);
  if (!pane) return;
  const token = localStorage.getItem('jwt_token') || '';
  try {{
    const r = await fetch(cfg.url + '?t=' + Date.now(), {{
      headers: token ? {{ 'Authorization': 'Bearer ' + token }} : {{}},
    }});
    if (r.status === 401) {{
      pane.innerHTML = '<div class="regime-empty">Session expired — <a href="login.html" style="color:#f5c842">log in</a> again.</div>';
      return;
    }}
    if (!r.ok) {{
      pane.innerHTML = `<div class="regime-empty">Error: HTTP ${{r.status}}.</div>`;
      return;
    }}
    const data = await r.json();
    const rows = Array.isArray(data) ? data : [];
    const tab = REGIME_TABS[key];
    const btn = document.querySelector(`.regime-tab[data-tab="${{key}}"]`);
    if (btn) {{
      const existing = btn.querySelector('.count');
      if (existing) existing.remove();
      if (rows.length > 0) {{
        const span = document.createElement('span');
        span.className = 'count';
        span.textContent = '(' + rows.length + ')';
        btn.appendChild(span);
      }}
    }}
    const table = tab.render(rows);
    pane.innerHTML = table || `<div class="regime-empty">${{regimeEscape(tab.empty)}}</div>`;
    const meta = document.getElementById('regimeMeta');
    if (meta) meta.textContent = 'Last refresh: ' + new Date().toISOString().slice(11, 19) + ' UTC · ' + key;
  }} catch (err) {{
    pane.innerHTML = `<div class="regime-empty">Fetch error: ${{regimeEscape(err.message || String(err))}}</div>`;
  }}
}}

function activateRegimeTab(key) {{
  if (!REGIME_TABS[key]) return;
  _activeRegimeTab = key;
  document.querySelectorAll('.regime-tab').forEach(b => {{
    b.classList.toggle('active', b.dataset.tab === key);
  }});
  document.querySelectorAll('.regime-pane').forEach(p => {{
    p.classList.toggle('active', p.id === 'regime-pane-' + key);
  }});
  fetchRegimeTab(key);
  if (_regimeRefreshTimer) clearInterval(_regimeRefreshTimer);
  _regimeRefreshTimer = setInterval(() => fetchRegimeTab(_activeRegimeTab), 30000);
}}

const _regimeNav = document.getElementById('regimeNav');
if (_regimeNav) {{
  _regimeNav.addEventListener('click', e => {{
    const btn = e.target.closest('.regime-tab');
    if (btn) activateRegimeTab(btn.dataset.tab);
  }});
  activateRegimeTab('regime');
}}
</script>
</body>
</html>"""
        path = os.path.join(self.data_dir, 'dashboard.html')
        with open(path, 'w') as f:
            f.write(html)
