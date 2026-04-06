"""
bot/dashboard.py  — v7.0
Writes data.json and dashboard.html every cycle.
Font: JetBrains Mono (Google Fonts CDN + local woff2 fallback)
Subtitle colour: blue (was green — avoids confusion with buy signals)
Entry price: bright white
"""

import json, os, datetime
from bot.config import Config
from bot.logger import log


class Dashboard:

    def __init__(self, cfg: Config):
        self.cfg      = cfg
        self.data_dir = cfg.web_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self._write_html()
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
        data = {
            'last_updated':        datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
            'check_interval_mins': self.cfg.check_interval_mins,
            'cycle':               cycle,
            'total_pnl':           round(total_pnl, 2),
            'risk_pct':            round(min(abs(total_pnl / self.cfg.portfolio_loss_limit) * 100, 100), 1),
            'loss_limit':          self.cfg.portfolio_loss_limit,
            'lse_open':            lse_open,
            'us_open':             us_open,
            'positions':           pos_list,
            'signals':             signal_rows,
            'accum':               accum_rows,
            'config_file':         self.cfg.path,
            'instrument_count':    {'active': len(self.cfg.active_instruments), 'accum': len(self.cfg.accum_instruments)},
        }
        path = os.path.join(self.data_dir, 'data.json')
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
        log(f"Dashboard updated — cycle #{cycle}")

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
</style>
</head>
<body>

<!-- Nav Bar -->
<div style="display:flex;align-items:center;padding:10px 24px;background:#161b22;border-bottom:1px solid #30363d;gap:8px;">
  <span style="color:#f5c842;font-weight:700;font-size:0.82rem;margin-right:8px;">CogniflowAI</span>
  <a href="dashboard.html" style="font-size:0.72rem;text-decoration:none;padding:6px 14px;border-radius:4px;color:#f5c842;border:1px solid #f5c842;font-family:'JetBrains Mono',monospace;">Dashboard</a>
  <a href="instruments.html" style="font-size:0.72rem;text-decoration:none;padding:6px 14px;border-radius:4px;color:#8b949e;border:1px solid transparent;font-family:'JetBrains Mono',monospace;">Instruments</a>
  <a href="tests.html" style="font-size:0.72rem;text-decoration:none;padding:6px 14px;border-radius:4px;color:#8b949e;border:1px solid transparent;font-family:'JetBrains Mono',monospace;">Tests</a>
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

<script>
async function load() {{
  try {{
    const r = await fetch('data.json?t=' + Date.now());
    const d = await r.json();

    document.getElementById('updated').textContent = 'Last updated: ' + d.last_updated;
    document.getElementById('configBar').textContent =
      '📋 Config: ' + d.config_file + ' — ' + d.instrument_count.active + ' active, ' + d.instrument_count.accum + ' accumulation';
    document.getElementById('cycleInterval').textContent = 'every ' + (d.check_interval_mins||15) + ' min';

    const pnl   = d.total_pnl;
    const pnlEl = document.getElementById('pnl');
    pnlEl.textContent = (pnl >= 0 ? '+' : '') + '$' + Math.abs(pnl).toFixed(2);
    pnlEl.className   = 'card-value ' + (pnl >= 0 ? 'pnl-pos' : 'pnl-neg');
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
            ? ` <span class="${{p.unreal_pnl>=0?'green':'red'}}">${{p.unreal_pnl>=0?'+':''}}$${{p.unreal_pnl.toFixed(2)}}</span>` : '';
          return `<div class="pos-item">
            <span class="pos-sym">${{p.symbol}}</span>
            <span class="pos-detail">${{p.qty}} @ ${{p.avg_cost}} ${{p.currency}}</span>${{pnlStr}}
          </div>`;
        }}).join('')
      : '<span class="muted">No open positions</span>';

    // Sort: open positions first (by P&L% desc), then flat (alphabetical)
    const openPos = d.signals.filter(s => s.pos !== 0).sort((a,b) => (b.pnl_pct||0) - (a.pnl_pct||0));
    const flatPos = d.signals.filter(s => s.pos === 0).sort((a,b) => a.symbol.localeCompare(b.symbol));
    const sorted  = [...openPos, ...flatPos];

    function renderRow(s, isOpen) {{
      const mktClass  = s.market==='24/7' ? 'badge-247' : s.market==='OPEN' ? 'badge-open' : 'badge-closed';
      const confClass = s.confidence==='HIGH' ? 'badge-high' : s.confidence==='MEDIUM' ? 'badge-med' : 'badge-low';
      const sigClass  = s.signal==='BUY' ? 'badge-buy' : s.signal==='SELL' ? 'badge-sell' : 'badge-hold';
      const wrColor   = s.wr >= -50 ? 'green' : 'red';
      const rsiColor  = s.rsi > 70 ? 'red' : s.rsi < 35 ? 'green' : '';
      const dot       = isOpen ? '<span style="color:#22c55e;font-size:0.55rem;margin-right:4px">●</span>' : '';

      const entryStr = s.avg_cost > 0
        ? `<span class="entry-price">${{s.currency==='GBP'?'£':'$'}}${{s.avg_cost.toLocaleString()}}</span>`
        : '<span class="muted">--</span>';

      const pnlStr = s.unreal_pnl !== 0
        ? `<span class="${{s.unreal_pnl>=0?'green':'red'}}">${{s.unreal_pnl>=0?'+':''}}$${{s.unreal_pnl.toFixed(2)}}</span>`
        : '<span class="muted">--</span>';

      const pctStr = s.pnl_pct !== 0
        ? `<span class="${{s.pnl_pct>=0?'green':'red'}}">${{s.pnl_pct>=0?'+':''}}${{s.pnl_pct.toFixed(2)}}%</span>`
        : '<span class="muted">--</span>';

      const priceStr = s.price > 0 ? s.price.toLocaleString() : '<span class="muted">--</span>';
      const posStr   = s.pos !== 0 ? `<span class="pos-badge">${{s.pos}}</span>` : '<span class="muted">0</span>';
      const actColor = s.action&&s.action.includes('BOUGHT') ? 'green'
                     : s.action&&s.action.includes('CLOSED') ? 'red'
                     : s.action&&s.action.includes('BLOCKED') ? 'red' : 'muted';

      return `<tr>
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
        <td><span class="badge ${{sigClass}}">${{s.signal}}</span></td>
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
    document.getElementById('layer1').innerHTML = rows.join('');

    document.getElementById('layer2').innerHTML = d.accum.map(e => {{
      const rsiColor = e.rsi > 70 ? 'red' : e.rsi < 35 ? 'green' : '';
      const wrColor  = e.wr < -80 ? 'red' : e.wr > -20 ? 'green' : 'muted';
      return `<tr>
        <td><div class="asset-name">${{e.flag}} ${{e.symbol}}</div><div class="asset-sub">${{e.name}}</div></td>
        <td>${{e.price > 0 ? e.price.toLocaleString() : '--'}}</td>
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
</script>
</body>
</html>"""
        path = os.path.join(self.data_dir, 'dashboard.html')
        with open(path, 'w') as f:
            f.write(html)
