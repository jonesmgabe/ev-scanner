"""Static, dependency-free HTML report: dark theme, sortable, filterable, with an
expandable per-bet book-comparison panel. Mobile friendly."""
from __future__ import annotations

import html
import json

TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>+EV Scanner</title>
<style>
:root{--bg:#0e1116;--panel:#161b22;--panel2:#11161d;--line:#262c36;--text:#e6edf3;--muted:#8b949e;--acc:#3fb950;--acc2:#58a6ff;--neg:#f85149;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;-webkit-text-size-adjust:100%}
header{padding:20px 28px 8px}
h1{margin:0;font-size:22px;font-weight:650;letter-spacing:.2px}
h1 span{color:var(--acc)}
.meta{color:var(--muted);font-size:12.5px;margin-top:4px}
.controls{display:flex;flex-wrap:wrap;gap:10px;padding:12px 28px;align-items:center}
select,input{background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:6px 9px;font-size:13px}
label{color:var(--muted);font-size:12px;display:flex;gap:6px;align-items:center}
.count{margin-left:auto;color:var(--muted);font-size:12.5px}
.wrap{padding:0 28px 28px;overflow-x:auto}
table.main{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.main>thead th,.main>tbody>tr>td{padding:8px 10px;text-align:left;white-space:nowrap;border-bottom:1px solid var(--line)}
.main>thead th{position:sticky;top:0;background:#1c222b;color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.4px;cursor:pointer;user-select:none}
.main>thead th:hover{color:var(--text)}
th.sorted-asc::after{content:" \25B2";font-size:9px}
th.sorted-desc::after{content:" \25BC";font-size:9px}
tr.bet{cursor:pointer}
tr.bet:hover>td{background:#1b2129}
tr.bet.open>td{background:#1a2230;border-bottom-color:transparent}
td.num{text-align:right;font-variant-numeric:tabular-nums}
th.num{text-align:right}
.ev{color:var(--acc);font-weight:650}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;background:#21262d;border:1px solid var(--line);font-size:12px}
.src{color:var(--muted);font-size:12px}
.sport{color:var(--acc2);font-weight:600;font-size:12px}
.tag{font-size:10px;padding:0 5px;border-radius:4px;background:#2d2350;color:#c4b5fd;margin-left:4px;vertical-align:1px}
.ref{padding:1px 6px;border-radius:4px;border:1px solid var(--line)}
.ref-pinnacle{color:#79c0ff}.ref-novig{color:#f0a6ff}.ref-blend{color:#ffd479}.ref-consensus{color:#8b949e}
.chev{width:28px;padding-right:0!important;color:var(--muted)}
.chev button{background:none;border:0;color:inherit;font-size:14px;cursor:pointer;padding:4px;line-height:1;transition:transform .15s}
tr.open .chev button{transform:rotate(90deg);color:var(--acc2)}
.show-q{display:none!important}
.sub{display:none;color:var(--muted);font-size:11.5px;font-weight:400;white-space:normal}
/* detail panel */
tr.detail>td{padding:0 10px 14px 38px!important;background:#1a2230;border-bottom:1px solid var(--line);white-space:normal}
.panel{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.ph{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:baseline;margin-bottom:8px;font-size:13px}
.ph b{font-size:15px}
.ph .muted{color:var(--muted);font-size:12px}
table.q{width:100%;border-collapse:collapse;font-size:13px}
.q th{color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.3px;text-align:left;padding:4px 8px;border-bottom:1px solid var(--line)}
.q td{padding:5px 8px;border-bottom:1px solid #1d232c;white-space:nowrap}
.q td.num,.q th.num{text-align:right;font-variant-numeric:tabular-nums}
.q tr.best td{background:rgba(63,185,80,.13)}
.q tr.best td:first-child{box-shadow:inset 3px 0 0 var(--acc)}
.q tr.flag td:first-child b::after{content:" \2190 flagged";color:var(--acc2);font-weight:500;font-size:11px}
.q tr.refrow td{color:#c9b6ff}
.q .role{font-size:10.5px;color:var(--muted);display:block}
.q tr.refrow .role{color:#f0a6ff}
.badge{font-size:10px;padding:0 5px;border-radius:4px;background:var(--acc);color:#04130a;font-weight:700;margin-left:6px}
.pos{color:var(--acc)}.neg{color:var(--neg)}
.other{margin-top:8px;font-size:12px;color:var(--muted)}
.other span{display:inline-block;margin:2px 10px 2px 0;color:var(--text)}
.other span i{color:var(--muted);font-style:normal}
footer{color:var(--muted);font-size:12px;padding:0 28px 28px}
.status{margin-top:6px;font-size:13.5px}
.status .upd{font-weight:650}
.status .note{color:var(--muted);font-size:12.5px}
.status details{margin-top:4px;overflow-wrap:anywhere}.status summary{cursor:pointer}
.banner{margin-top:8px;padding:8px 12px;border-radius:6px;background:#3a2a05;border:1px solid #7d5a00;color:#ffd479;font-size:13px}
.live{font-size:12px;margin-top:2px}.live.ok{color:var(--acc)}.live.warn{color:#ffd479}.live.old{color:var(--neg)}
.empty{padding:30px;text-align:center;color:var(--muted)}
/* ---------- mobile ---------- */
@media (max-width: 700px){
  header{padding:14px 12px 4px} h1{font-size:19px} .meta{font-size:11px}
  .controls{padding:8px 12px;gap:6px} .controls label{font-size:11px} select,input{font-size:13px;padding:5px 6px}
  .count{width:100%;margin-left:0}
  .wrap{padding:0 6px 20px}
  .main>thead th,.main>tbody>tr>td{padding:7px 5px;font-size:12.5px}
  .hide-m{display:none!important}
  .sub{display:block}
  .main>tbody>tr>td.sel{white-space:normal;min-width:0;width:100%}
  .main>tbody>tr>td{white-space:normal}
  .q td:first-child{white-space:normal}
  .ph{font-size:12px}
  .pill{font-size:11px;padding:1px 6px;white-space:normal}
  tr.detail>td{padding:0 4px 10px 4px!important}
  .panel{padding:8px}
  .q td,.q th{padding:5px 4px;font-size:12px}
  .q .hide-q{display:none}
  .q .show-q{display:block!important}
  footer{padding:0 12px 20px}
}
</style></head><body>
<header>
<h1><span>+EV</span> Scanner</h1>
<div class="status">__STATUS__</div>
<div class="live" id="live"></div>
<div class="meta">__META__</div>
</header>
<div class="controls">
<label>Sport <select id="f-sport"><option value="">All</option></select></label>
<label>Book <select id="f-book"><option value="">All</option></select></label>
<label>Market <select id="f-market"><option value="">All</option></select></label>
<label>Reference <select id="f-ref"><option value="">All</option></select></label>
<label>Type <select id="f-type"><option value="">All</option><option>Main</option><option>Prop</option><option>DFS</option></select></label>
<label>Min EV% <input id="f-ev" type="number" step="0.5" placeholder="__MINEV__" style="width:64px"></label>
<label>Search <input id="f-q" type="text" placeholder="team / player" style="width:140px"></label>
<label><input type="checkbox" id="f-started" checked> Hide started</label>
<span class="count" id="count"></span>
</div>
<div class="wrap"><table class="main" id="t"><thead><tr>
<th class="chev" title="Tap a row to compare books"></th>
<th data-k="sport" class="hide-m">Sport</th><th data-k="event" class="hide-m">Event</th><th data-k="commence_utc" class="hide-m">Start (ET)</th>
<th data-k="market" class="hide-m">Market</th><th data-k="selection">Selection</th><th data-k="book">Book</th>
<th data-k="book_decimal" class="num">Odds</th><th data-k="fair_prob" class="num">Fair</th>
<th data-k="ev_pct" class="num">EV%</th><th data-k="kelly_pct" class="num hide-m">¼ Kelly</th><th data-k="fair_source" class="hide-m">Reference</th><th data-k="scanned_at" class="hide-m">Odds age</th>
</tr></thead><tbody></tbody></table></div>
<footer>__FOOTER__</footer>
<script>
const DATA = __DATA__;
const OPEN = new Set(__OPEN__);
const ROLE = {reference:'reference, not bettable', 'reference+target':'reference & target', target:'target book', dfs:"pick'em", other:'not a target', 'not bettable':'not bettable'};
DATA.forEach((r,i)=>{r._id=i;r.ref=r.fair_source.split('(')[0];r.type=r.is_dfs?'DFS':(r.is_prop?'Prop':'Main');});
const $ = s => document.querySelector(s);
const esc = s => String(s==null?'':s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function fill(id, vals){const el=$(id);[...new Set(vals)].sort().forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v;el.appendChild(o);});}
fill('#f-sport', DATA.map(r=>r.sport)); fill('#f-book', DATA.map(r=>r.book)); fill('#f-market', DATA.map(r=>r.market)); fill('#f-ref', DATA.map(r=>r.ref));
let sortK='ev_pct', sortDir=-1;
const NCOL = 13;
const STATUS = __STATUSJSON__;
function ago(iso){ if(!iso) return '—'; const m=Math.max(0,Math.round((Date.now()-Date.parse(iso))/60000));
  return m<60? m+'m' : (m<1440? Math.floor(m/60)+'h '+(m%60)+'m' : Math.floor(m/1440)+'d '+Math.floor((m%1440)/60)+'h'); }
function live(){ const el=$('#live'); if(!STATUS.generated_utc){el.textContent='';return;}
  const h=(Date.now()-Date.parse(STATUS.generated_utc))/3.6e6;
  el.className='live '+(h>12?'old':(h>6?'warn':'ok'));
  el.textContent=`Page built ${ago(STATUS.generated_utc)} ago`+(STATUS.next_update_et?` · next scheduled update ${STATUS.next_update_et}`:'')+
    (h>6?' · these odds are old; prices have probably moved':''); }
function evCell(v){ if(v===null||v===undefined) return '<span class="src">—</span>'; return `<span class="${v>0?'pos':'neg'}">${v>0?'+':''}${v.toFixed(2)}%</span>`; }
function panel(r){
  const qs = r.quotes||[];
  const rows = qs.map(q=>{
    const cls=[q.best?'best':'', q.book_key===r.book_key?'flag':'', q.role.startsWith('reference')?'refrow':''].join(' ');
    const role = ROLE[q.role]||q.role;
    const extra = (q.role==='dfs' ? (q.entry? ' · '+esc(q.entry):'') : (q.fee_adjusted? ' · after fee':''))
      + (q.fair_weight? ` · ${Math.round(q.fair_weight*100)}% of fair` : '')
      + (q.self_excluded? ' · left out of its own fair price' : '');
    return `<tr class="${cls}"><td><b>${esc(q.book)}</b>${q.best?'<span class="badge">BEST</span>':''}<span class="role">${esc(role)}${extra}</span></td>
<td class="num">${esc(q.american)}${q.opposite?'<span class="role show-q">opp '+esc(q.opposite)+'</span>':''}</td><td class="num hide-q">${esc(q.opposite||'—')}</td>
<td class="num">${q.implied!=null?(q.implied*100).toFixed(1)+'%':'—'}</td>
<td class="num">${q.role==='dfs'?'edge ':''}${evCell(q.ev_pct)}</td></tr>`;}).join('');
  const others = (r.other_lines||[]).map(o=>`<span><b>${esc(o.book)}</b> <i>${esc(o.line)}</i> ${esc(o.american)}${o.opposite?' / '+esc(o.opposite):''}</span>`).join('');
  return `<div class="panel"><div class="ph"><span>Fair <b>${esc(r.fair_american)}</b> <span class="muted">(${(r.fair_prob*100).toFixed(1)}%)</span></span>
<span>Reference <span class="ref ref-${esc(r.ref)}">${esc(r.fair_source)}</span></span>
<span class="muted">${esc(r.sharp_prices)}</span></div>
<table class="q"><thead><tr><th>Book</th><th class="num">This side</th><th class="num hide-q">Opposite</th><th class="num">Implied</th><th class="num">EV%</th></tr></thead>
<tbody>${rows||'<tr><td colspan="5" class="src">No quotes stored for this bet.</td></tr>'}</tbody></table>
${others?`<div class="other">Other lines: ${others}</div>`:''}</div>`;
}
function render(){
  const s=$('#f-sport').value,b=$('#f-book').value,m=$('#f-market').value,t=$('#f-type').value,rf=$('#f-ref').value,
        ev=parseFloat($('#f-ev').value||'-999'),q=$('#f-q').value.toLowerCase(),hs=$('#f-started').checked,nowMs=Date.now();
  let started=0;
  let rows=DATA.filter(r=>(!s||r.sport===s)&&(!b||r.book===b)&&(!m||r.market===m)&&(!t||r.type===t)&&(!rf||r.ref===rf)&&r.ev_pct>=ev&&(!q||(r.event+' '+r.selection).toLowerCase().includes(q))&&!(hs&&Date.parse(r.commence_utc)<nowMs&&++started));
  rows.sort((x,y)=>{let a=x[sortK],c=y[sortK];if(a===null)a=-1;if(c===null)c=-1;if(typeof a==='string')return a.localeCompare(c)*sortDir;return (a-c)*sortDir;});
  const tb=$('#t tbody');
  tb.innerHTML=rows.length?rows.map(r=>{const open=OPEN.has(r._id);
    return `<tr class="bet${open?' open':''}" data-id="${r._id}" aria-expanded="${open}">
<td class="chev"><button aria-label="Compare books">&#9656;</button></td>
<td class="hide-m"><span class="sport">${esc(r.sport)}</span></td><td class="hide-m">${esc(r.event)}</td><td class="hide-m">${esc(r.commence_et)}</td>
<td class="hide-m">${esc(r.market)}${r.type!=='Main'?' <span class="tag">'+r.type+'</span>':''}</td>
<td class="sel"><b>${esc(r.selection)}</b><span class="sub">${esc(r.event)} · ${esc(r.market)} · ${esc(r.commence_et)}${r.scanned_at?' · odds '+ago(r.scanned_at)+' old':''}</span></td>
<td><span class="pill">${esc(r.book)}</span></td>
<td class="num" title="decimal ${r.book_decimal}${r.effective_decimal!==r.book_decimal?' / effective '+r.effective_decimal:''}">${esc(r.book_american)}</td>
<td class="num" title="fair prob ${(r.fair_prob*100).toFixed(1)}%">${esc(r.fair_american)}</td>
<td class="num ev">${r.ev_pct.toFixed(2)}%</td>
<td class="num hide-m">${r.kelly_pct===null?'<span class="src" title="pick\'em leg: Kelly n/a">—</span>':r.kelly_pct.toFixed(2)+'%'}</td>
<td class="src hide-m" title="${esc(r.sharp_prices)}"><span class="ref ref-${esc(r.ref)}">${esc(r.fair_source)}</span></td>
<td class="src hide-m" title="odds pulled ${esc(r.scanned_at||'')}">${ago(r.scanned_at)}${r.carried?' <span class="tag" title="carried over from an earlier run">earlier</span>':''}</td></tr>`
    + (open?`<tr class="detail" data-parent="${r._id}"><td colspan="${NCOL}">${panel(r)}</td></tr>`:'');}).join('')
    :`<tr><td colspan="${NCOL}" class="empty">No bets match these filters.</td></tr>`;
  $('#count').textContent=`${rows.length} of ${DATA.length} bets`+(started?` (${started} started hidden)`:'')+' · tap a row to compare books';
  document.querySelectorAll('#t thead th').forEach(th=>{th.classList.remove('sorted-asc','sorted-desc');if(th.dataset.k&&th.dataset.k===sortK)th.classList.add(sortDir>0?'sorted-asc':'sorted-desc');});
}
$('#t tbody').addEventListener('click',e=>{const tr=e.target.closest('tr.bet');if(!tr)return;const id=+tr.dataset.id;OPEN.has(id)?OPEN.delete(id):OPEN.add(id);render();});
document.querySelectorAll('#t thead th[data-k]').forEach(th=>th.addEventListener('click',()=>{const k=th.dataset.k;if(k===sortK)sortDir*=-1;else{sortK=k;sortDir=(th.classList.contains('num')?-1:1);}render();}));
['#f-sport','#f-book','#f-market','#f-ref','#f-type','#f-ev','#f-q','#f-started'].forEach(id=>$(id).addEventListener('input',render));
// ?open=0,3 or #open=0 pre-expands rows (used for screenshots / sharing)
const pm=(location.search+location.hash).match(/open=([\d,]+)/); if(pm) pm[1].split(',').forEach(x=>OPEN.add(+x));
render(); live(); setInterval(()=>{live();},60000);
</script></body></html>
"""


def write_report(path, bets: list[dict], meta: str, footer: str, min_ev: float,
                 open_ids: list[int] | None = None, status_html: str = "",
                 status: dict | None = None) -> None:
    """status_html: trusted HTML shown under the title (last updated / banner).
    status: {"generated_utc": iso, "next_update_et": str} for the live staleness line."""
    doc = (TEMPLATE
           .replace("__DATA__", json.dumps(bets).replace("</", "<\\/"))
           .replace("__OPEN__", json.dumps(open_ids or []))
           .replace("__STATUSJSON__", json.dumps(status or {}).replace("</", "<\\/"))
           .replace("__STATUS__", status_html)
           .replace("__META__", html.escape(meta))
           .replace("__FOOTER__", footer)
           .replace("__MINEV__", str(min_ev)))
    with open(path, "w") as fh:
        fh.write(doc)
