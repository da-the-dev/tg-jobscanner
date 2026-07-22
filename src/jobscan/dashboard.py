"""Render the static HTML dashboard from SQLite."""
import datetime
import json

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Job Scan Dashboard</title>
<style>
  :root { --bg:#0f1115; --card:#1a1d24; --border:#2a2e38; --fg:#e6e6e6; --dim:#9aa0ab;
          --green:#4caf7d; --yellow:#d9a441; --red:#c75b5b; --accent:#5b8dd9; }
  * { box-sizing:border-box; }
  body { margin:0; padding:24px; background:var(--bg); color:var(--fg);
         font:14px/1.45 -apple-system,'Segoe UI',Roboto,sans-serif; }
  h1 { font-size:20px; margin:0 0 4px; }
  .meta { color:var(--dim); margin-bottom:16px; font-size:12px; }
  .controls { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:16px; align-items:center; }
  input,select { background:var(--card); color:var(--fg); border:1px solid var(--border);
                 border-radius:6px; padding:6px 10px; font-size:13px; }
  input[type=search]{ width:260px; }
  label { color:var(--dim); font-size:12px; }
  table { width:100%; border-collapse:collapse; }
  th { text-align:left; padding:8px 10px; color:var(--dim); font-size:12px; cursor:pointer;
       border-bottom:1px solid var(--border); white-space:nowrap; user-select:none; }
  th:hover { color:var(--fg); }
  td { padding:10px; border-bottom:1px solid var(--border); vertical-align:top; }
  tr.row { cursor:pointer; }
  tr.row:hover { background:var(--card); }
  .score { font-weight:700; font-size:15px; }
  .bar { height:4px; border-radius:2px; background:var(--border); width:64px; margin-top:4px; }
  .bar>div { height:100%; border-radius:2px; }
  .title { font-weight:600; }
  .company { color:var(--dim); font-size:12px; }
  .tag { display:inline-block; background:var(--card); border:1px solid var(--border);
         border-radius:4px; padding:1px 7px; font-size:11px; color:var(--dim); margin-right:4px; }
  a.gobtn { color:var(--accent); text-decoration:none; font-size:13px; white-space:nowrap; }
  a.gobtn:hover { text-decoration:underline; }
  .detail { display:none; background:var(--card); }
  .detail.open { display:table-row; }
  .cols { display:grid; grid-template-columns:1fr 1fr; gap:16px; padding:6px 4px; }
  .cols h4 { margin:0 0 6px; font-size:12px; text-transform:uppercase; letter-spacing:.05em; }
  .cols ul { margin:0; padding-left:18px; }
  .cols li { margin-bottom:4px; }
  .apply h4 { color:var(--green); } .skip h4 { color:var(--red); }
  .strong h4 { color:var(--green); } .weak h4 { color:var(--yellow); }
  .status-new { color:var(--accent); } .status-applied { color:var(--green); }
  .status-skipped { color:var(--dim); }
  tr.dim-row { opacity:.45; }
  .empty { color:var(--dim); padding:40px; text-align:center; }
</style>
</head>
<body>
<h1>Job Scan Dashboard</h1>
<div class="meta">Generated __GENERATED__ · __COUNT__ relevant postings · click a row for analysis</div>
<div class="controls">
  <input type="search" id="q" placeholder="Search title / company / text…">
  <label>Min score <input type="number" id="minScore" value="__MIN_SCORE__" min="0" max="100" style="width:64px"></label>
  <label><input type="checkbox" id="hideSkipped" checked> hide skipped</label>
  <select id="chan"><option value="">All channels</option></select>
</div>
<table>
  <thead><tr>
    <th data-k="score">Score</th><th data-k="title">Job</th><th data-k="salary">Salary</th>
    <th data-k="location">Location</th><th data-k="channel">Channel</th>
    <th data-k="date">Date</th><th>Status</th><th></th>
  </tr></thead>
  <tbody id="tb"></tbody>
</table>
<div class="empty" id="empty" style="display:none">Nothing matches the current filters.</div>
<script>
const DATA = __DATA__;
let sortKey='score', sortDir=-1;
const tb=document.getElementById('tb'), q=document.getElementById('q'),
      minScore=document.getElementById('minScore'),
      hideSkipped=document.getElementById('hideSkipped'),
      chanSel=document.getElementById('chan');
[...new Set(DATA.map(d=>d.channel))].sort().forEach(c=>{
  const o=document.createElement('option'); o.value=o.textContent=c; chanSel.appendChild(o);});
const getStatus=h=>localStorage.getItem('jobstatus:'+h)||'new';
const setStatus=(h,v)=>localStorage.setItem('jobstatus:'+h,v);
function color(s){return s>=70?'var(--green)':s>=45?'var(--yellow)':'var(--red)';}
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function list(items){return items&&items.length?'<ul>'+items.map(i=>'<li>'+esc(i)+'</li>').join('')+'</ul>':'<span style="color:var(--dim)">—</span>';}
function render(){
  const term=q.value.toLowerCase(), min=+minScore.value||0, ch=chanSel.value;
  let rows=DATA.filter(d=>d.score>=min && (!ch||d.channel===ch) &&
    (!term||(d.title+' '+d.company+' '+d.salary+' '+d.location).toLowerCase().includes(term)));
  if(hideSkipped.checked) rows=rows.filter(d=>getStatus(d.hash)!=='skipped');
  rows.sort((a,b)=>{const va=a[sortKey]??'',vb=b[sortKey]??'';
    return (typeof va==='number'?va-vb:String(va).localeCompare(String(vb)))*sortDir;});
  tb.innerHTML=rows.map((d,i)=>{
    const st=getStatus(d.hash);
    return `<tr class="row ${st==='skipped'?'dim-row':''}" data-i="${i}">
      <td><span class="score" style="color:${color(d.score)}">${d.score}</span>
          <div class="bar"><div style="width:${d.score}%;background:${color(d.score)}"></div></div></td>
      <td><div class="title">${esc(d.title)||'(untitled)'}</div><div class="company">${esc(d.company)}</div></td>
      <td>${esc(d.salary)||'—'}</td><td>${esc(d.location)||'—'}</td>
      <td><span class="tag">${esc(d.channel)}</span></td>
      <td style="white-space:nowrap">${d.date.slice(0,10)}</td>
      <td><select class="st" data-h="${d.hash}" onclick="event.stopPropagation()">
        ${['new','applied','skipped'].map(s=>`<option ${s===st?'selected':''}>${s}</option>`).join('')}
      </select></td>
      <td><a class="gobtn" href="${esc(d.link)}" target="_blank" onclick="event.stopPropagation()">Open in TG ↗</a></td>
    </tr>
    <tr class="detail"><td colspan="8"><div class="cols">
      <div class="apply"><h4>Why apply</h4>${list(d.reasons_apply)}</div>
      <div class="skip"><h4>Why skip</h4>${list(d.reasons_skip)}</div>
      <div class="strong"><h4>Your strengths</h4>${list(d.strengths)}</div>
      <div class="weak"><h4>Your gaps</h4>${list(d.weaknesses)}</div>
    </div></td></tr>`;}).join('');
  document.getElementById('empty').style.display=rows.length?'none':'block';
  tb.querySelectorAll('tr.row').forEach(r=>r.addEventListener('click',
    ()=>r.nextElementSibling.classList.toggle('open')));
  tb.querySelectorAll('select.st').forEach(s=>s.addEventListener('change',
    e=>{setStatus(e.target.dataset.h,e.target.value);render();}));
}
document.querySelectorAll('th[data-k]').forEach(th=>th.addEventListener('click',()=>{
  const k=th.dataset.k; sortDir=(sortKey===k)?-sortDir:(k==='score'?-1:1); sortKey=k; render();}));
[q,minScore,hideSkipped,chanSel].forEach(el=>el.addEventListener('input',render));
render();
</script>
</body>
</html>"""


def render(cfg, db, log=print):
    rows = []
    for r in db.dashboard_rows():
        rows.append({
            "hash": r["text_hash"], "score": r["score"], "title": r["title"],
            "company": r["company"], "salary": r["salary"], "location": r["location"],
            "channel": r["channel_title"] or (r["username"] or "?"),
            "date": r["date"] or "", "link": r["link"],
            "reasons_apply": json.loads(r["reasons_apply"] or "[]"),
            "reasons_skip": json.loads(r["reasons_skip"] or "[]"),
            "strengths": json.loads(r["strengths"] or "[]"),
            "weaknesses": json.loads(r["weaknesses"] or "[]"),
        })
    html = (TEMPLATE
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False))
            .replace("__GENERATED__", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__COUNT__", str(len(rows)))
            .replace("__MIN_SCORE__", str(cfg["scoring"]["min_score"])))
    out = cfg["dashboard_path"]
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"  dashboard: {out} ({len(rows)} postings)")
    return out
