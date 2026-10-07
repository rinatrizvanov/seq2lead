"""The single-page interface, served inline.

One file, no build step, no external resources. The Content-Security-Policy the
server sets allows nothing off-origin, so the page must be self-contained.
"""

from __future__ import annotations

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Seq2Lead — local ranking</title>
<style>
  :root{--ink:#1c2024;--muted:#687076;--line:#dfe3e6;--bg:#fbfcfd;--panel:#fff;
        --accent:#174e77;--warn:#8a5a00;--warnbg:#fff8e6}
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
       color:var(--ink);background:var(--bg)}
  header{background:var(--panel);border-bottom:1px solid var(--line);padding:14px 20px}
  h1{margin:0;font-size:17px;font-weight:650}
  .sub{color:var(--muted);font-size:12.5px;margin-top:3px}
  .wrap{max-width:1180px;margin:0 auto;padding:18px 20px 60px}
  .disclaimer{background:var(--warnbg);border:1px solid #f0dfae;color:var(--warn);
              padding:10px 12px;border-radius:6px;margin:14px 0;font-size:13px}
  .tabs{display:flex;gap:6px;margin:16px 0 14px;border-bottom:1px solid var(--line)}
  .tab{padding:8px 14px;cursor:pointer;border:1px solid transparent;border-bottom:none;
       border-radius:6px 6px 0 0;color:var(--muted);font-weight:550}
  .tab.on{background:var(--panel);border-color:var(--line);color:var(--ink);margin-bottom:-1px}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px}
  label{display:block;font-size:12.5px;font-weight:600;margin:10px 0 4px}
  textarea,input,select{width:100%;padding:8px;border:1px solid var(--line);border-radius:6px;
                        font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;background:#fff;color:var(--ink)}
  textarea{min-height:110px;resize:vertical}
  .row{display:flex;gap:12px;flex-wrap:wrap}
  .row>div{flex:1;min-width:120px}
  button{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:9px 16px;
         font-size:13.5px;font-weight:600;cursor:pointer}
  button.ghost{background:#fff;color:var(--ink);border:1px solid var(--line)}
  button:disabled{opacity:.5;cursor:not-allowed}
  .bar{height:6px;background:#eef1f3;border-radius:3px;overflow:hidden;margin:10px 0 6px}
  .bar>i{display:block;height:100%;background:var(--accent);width:0;transition:width .25s}
  table{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px}
  th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:middle}
  th{font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  .smiles{font:11.5px ui-monospace,Menlo,monospace;color:var(--muted);word-break:break-all}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-top:12px}
  .card{border:1px solid var(--line);border-radius:7px;padding:9px;background:#fff}
  .card img{width:100%;height:140px;object-fit:contain;background:#fff}
  .pill{display:inline-block;background:#eef1f3;border-radius:10px;padding:1px 7px;
        font-size:11px;color:var(--muted);margin-right:4px}
  .pill.tie{background:#fdf1dc;color:var(--warn)}
  .note{color:var(--muted);font-size:12.5px;margin:6px 0}
  .warn{background:var(--warnbg);border-left:3px solid #e0b25e;padding:7px 10px;margin:6px 0;font-size:12.5px}
  .muted{color:var(--muted)}
  .pager{display:flex;gap:8px;align-items:center;margin-top:12px}
  .hide{display:none}
</style>
</head>
<body>
<header>
  <h1>Seq2Lead — local ranking</h1>
  <div class="sub" id="sub">loading bundle…</div>
</header>
<div class="wrap">
  <div class="disclaimer" id="disclaimer"></div>

  <div class="tabs">
    <div class="tab on" data-tab="rank">Rank a sequence</div>
    <div class="tab" data-tab="library">Browse the library</div>
  </div>

  <!-- ─────────────────────────── rank ─────────────────────────── -->
  <section class="panel" id="tab-rank">
    <label for="seq">Protein sequence — paste one sequence or one FASTA record</label>
    <textarea id="seq" placeholder="MDPLNLSWYDDDLERQNWSRPFNGSD…  or  &gt;my_target&#10;MDPLNLSW…"></textarea>
    <div class="row" style="margin-top:8px;align-items:center">
      <div><input type="file" id="file" accept=".fasta,.fa,.faa,.txt"></div>
      <div class="muted" style="flex:2">A FASTA file must hold exactly one record.</div>
    </div>

    <div class="row">
      <div><label for="topn">Top N</label><input id="topn" type="number" value="50" min="1" max="25000"></div>
      <div><label for="diverse">Diverse subset (0 = off)</label><input id="diverse" type="number" value="0" min="0"></div>
      <div><label for="thr">Diversity threshold</label><input id="thr" type="number" value="0.7" step="0.05" min="0.05" max="1"></div>
    </div>
    <div class="row">
      <div><label for="minmw">Min MW</label><input id="minmw" type="number" placeholder="off"></div>
      <div><label for="maxmw">Max MW</label><input id="maxmw" type="number" placeholder="off"></div>
      <div><label for="mintpsa">Min TPSA</label><input id="mintpsa" type="number" placeholder="off"></div>
      <div><label for="maxtpsa">Max TPSA</label><input id="maxtpsa" type="number" placeholder="off"></div>
    </div>
    <p class="note">Filters and diversity are optional and never replace the ranking:
       removed compounds keep their rank and score and are listed with the reason.</p>

    <div style="margin-top:12px;display:flex;gap:8px">
      <button id="go">Rank library</button>
      <button id="cancel" class="ghost hide">Cancel</button>
      <button id="csv" class="ghost hide">Download CSV</button>
    </div>

    <div id="progress" class="hide">
      <div class="bar"><i id="barfill"></i></div>
      <div class="note" id="phase"></div>
    </div>
    <div id="rankout"></div>
  </section>

  <!-- ─────────────────────────── library ─────────────────────────── -->
  <section class="panel hide" id="tab-library">
    <div class="row">
      <div style="flex:3"><label for="q">Search identifier or SMILES</label>
        <input id="q" placeholder="e.g. 1369460 or c1ccccc1"></div>
      <div><label for="lminmw">Min MW</label><input id="lminmw" type="number" placeholder="off"></div>
      <div><label for="lmaxmw">Max MW</label><input id="lmaxmw" type="number" placeholder="off"></div>
      <div><label for="lmintpsa">Min TPSA</label><input id="lmintpsa" type="number" placeholder="off"></div>
      <div><label for="lmaxtpsa">Max TPSA</label><input id="lmaxtpsa" type="number" placeholder="off"></div>
    </div>
    <div style="margin-top:10px"><button id="search">Search</button></div>
    <div class="note" id="libnote"></div>
    <div class="grid" id="libgrid"></div>
    <div class="pager">
      <button class="ghost" id="prev">Previous</button>
      <span id="pageinfo" class="muted"></span>
      <button class="ghost" id="next">Next</button>
    </div>
  </section>
</div>

<script>
const $ = (id) => document.getElementById(id);
let JOB = null, POLL = null, PAGE_N = 1;

async function api(path, opts) {
  const r = await fetch(path, opts);
  const text = await r.text();
  let data; try { data = JSON.parse(text); } catch { data = { error: text }; }
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

/* ── tabs ── */
document.querySelectorAll('.tab').forEach(t => t.onclick = () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.toggle('on', x === t));
  $('tab-rank').classList.toggle('hide', t.dataset.tab !== 'rank');
  $('tab-library').classList.toggle('hide', t.dataset.tab !== 'library');
  if (t.dataset.tab === 'library' && !$('libgrid').children.length) loadLibrary(1);
});

/* ── bundle header ── */
api('/api/bundle').then(b => {
  $('sub').textContent =
    `${b.library.name} · ${b.counts.compounds.toLocaleString()} compounds · ` +
    `projection_dim ${b.model.projection_dim} · device ${b.device}`;
  $('disclaimer').textContent = b.disclaimer;
}).catch(e => $('sub').textContent = 'bundle error: ' + e.message);

/* ── FASTA upload, read in the browser ── */
$('file').onchange = (e) => {
  const f = e.target.files[0]; if (!f) return;
  const r = new FileReader();
  r.onload = () => { $('seq').value = r.result; };
  r.readAsText(f);
};

/* ── ranking ── */
function num(id) { const v = $(id).value.trim(); return v === '' ? null : Number(v); }

$('go').onclick = async () => {
  const seq = $('seq').value.trim();
  if (!seq) { $('rankout').innerHTML = '<div class="warn">Paste a sequence or choose a FASTA file.</div>'; return; }
  $('rankout').innerHTML = ''; $('csv').classList.add('hide');
  $('go').disabled = true; $('cancel').classList.remove('hide'); $('progress').classList.remove('hide');
  try {
    JOB = await api('/api/rank', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sequence: seq, source: $('file').files[0]?.name || 'pasted sequence',
        top_n: Number($('topn').value) || 50,
        diverse: Number($('diverse').value) || 0,
        diversity_threshold: Number($('thr').value) || 0.7,
        min_mw: num('minmw'), max_mw: num('maxmw'),
        min_tpsa: num('mintpsa'), max_tpsa: num('maxtpsa'),
      })
    });
    POLL = setInterval(poll, 400); poll();
  } catch (e) { fail(e.message); }
};

$('cancel').onclick = async () => {
  if (!JOB) return;
  $('cancel').disabled = true;
  try { await api(`/api/job/${JOB.id}/cancel`, { method: 'POST' }); } catch (e) { /* reported by poll */ }
};

async function poll() {
  if (!JOB) return;
  let s; try { s = await api(`/api/job/${JOB.id}`); } catch (e) { return fail(e.message); }
  $('barfill').style.width = Math.round((s.progress || 0) * 100) + '%';
  const queued = s.queue_position != null && s.queue_position > 0
    ? ` · ${s.queue_position} job(s) ahead` : '';
  $('phase').textContent = `${s.state} — ${s.phase}${queued} · ${s.elapsed}s`;
  if (['done', 'failed', 'cancelled'].includes(s.state)) {
    clearInterval(POLL); POLL = null;
    $('go').disabled = false; $('cancel').classList.add('hide'); $('cancel').disabled = false;
    if (s.state === 'done') { render(s.result); $('csv').classList.remove('hide'); }
    else if (s.state === 'failed') fail(s.error);
    else $('rankout').innerHTML = '<div class="warn">Cancelled. Nothing was written.</div>';
  }
}

function fail(msg) {
  clearInterval(POLL); POLL = null;
  $('go').disabled = false; $('cancel').classList.add('hide'); $('progress').classList.add('hide');
  $('rankout').innerHTML = `<div class="warn"><strong>Could not rank.</strong><br>${esc(msg)}</div>`;
}

$('csv').onclick = () => { if (JOB) window.location = `/api/job/${JOB.id}.csv`; };

const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function render(r) {
  const kept = r.shortlist ? new Set(r.shortlist.kept_ids) : null;
  let h = '';
  h += `<p class="note">${esc(r.query.header || 'unnamed query')} · ${r.query.length} residues ·
        library ${esc(r.library.name)} (${r.library.members.toLocaleString()}) · device ${esc(r.device)}</p>`;
  (r.notes || []).forEach(n => h += `<div class="warn">${esc(n)}</div>`);
  (r.warnings || []).forEach(n => h += `<div class="warn">${esc(n)}</div>`);
  if (r.shortlist) {
    h += `<div class="note"><strong>Shortlist:</strong> ${r.shortlist.applied.map(esc).join('; ')} —
          ${r.shortlist.kept} kept, ${r.shortlist.removed} removed.
          The unfiltered ranking below is unchanged; removed rows are marked.</div>`;
  }
  h += `<table><thead><tr><th>Rank</th><th>Structure</th><th>Compound</th>
        <th class="num">Predicted pKi</th><th>Notes</th></tr></thead><tbody>`;
  r.rows.forEach(row => {
    const out = kept && !kept.has(row.compound_id);
    const pills = [];
    if (row.tied_with) pills.push(`<span class="pill tie">tied with ${row.tied_with}</span>`);
    (row.flags || []).forEach(f => pills.push(`<span class="pill">${esc(f)}</span>`));
    if (out) pills.push('<span class="pill">not in shortlist</span>');
    h += `<tr${out ? ' style="opacity:.45"' : ''}>
      <td class="num">${row.rank}</td>
      <td><img loading="lazy" width="110" height="88" src="/api/depict?row=${row.row}" alt=""></td>
      <td><strong>${esc(row.compound_id)}</strong><div class="smiles">${esc(row.smiles)}</div></td>
      <td class="num">${row.score_pki.toFixed(3)}</td>
      <td>${pills.join(' ') || '<span class="muted">—</span>'}</td></tr>`;
  });
  h += '</tbody></table>';
  if (r.shortlist && r.shortlist.removed_rows && r.shortlist.removed_rows.length) {
    h += `<p class="note" style="margin-top:14px"><strong>Removed from the shortlist</strong>
          — original rank and score preserved:</p><table><thead><tr><th>Orig rank</th><th>Compound</th>
          <th class="num">Predicted pKi</th><th>Reason</th></tr></thead><tbody>`;
    r.shortlist.removed_rows.slice(0, 100).forEach(g => {
      h += `<tr><td class="num">${g.original_rank}</td><td>${esc(g.compound_id)}</td>
            <td class="num">${g.score_pki.toFixed(3)}</td>
            <td>${esc(g.reason)}<div class="muted" style="font-size:11.5px">${esc(g.detail)}</div></td></tr>`;
    });
    h += '</tbody></table>';
    if (r.shortlist.removed_rows.length > 100)
      h += `<p class="note">… ${r.shortlist.removed_rows.length - 100} more, all in the CSV.</p>`;
  }
  $('rankout').innerHTML = h;
  $('progress').classList.add('hide');
}

/* ── library browser ── */
async function loadLibrary(page) {
  PAGE_N = page;
  const p = new URLSearchParams({
    page, page_size: 24, q: $('q').value.trim(),
  });
  [['min_mw', 'lminmw'], ['max_mw', 'lmaxmw'], ['min_tpsa', 'lmintpsa'], ['max_tpsa', 'lmaxtpsa']]
    .forEach(([k, id]) => { const v = $(id).value.trim(); if (v) p.set(k, v); });
  $('libnote').textContent = 'loading…';
  let d; try { d = await api('/api/library?' + p); } catch (e) { $('libnote').textContent = e.message; return; }
  $('libnote').innerHTML =
    `${d.total.toLocaleString()} of ${d.library_total.toLocaleString()} compounds match` +
    (d.filtered_out ? ` · ${d.filtered_out.toLocaleString()} excluded by the property filter` : '');
  $('libgrid').innerHTML = d.rows.map(x => `
    <div class="card">
      <img loading="lazy" src="/api/depict?row=${x.row}" alt="">
      <div><strong>${esc(x.compound_id)}</strong></div>
      <div class="smiles">${esc(x.smiles.length > 64 ? x.smiles.slice(0, 64) + '…' : x.smiles)}</div>
    </div>`).join('');
  $('pageinfo').textContent = `page ${d.page} of ${d.pages}`;
  $('prev').disabled = d.page <= 1; $('next').disabled = d.page >= d.pages;
}
$('search').onclick = () => loadLibrary(1);
$('q').addEventListener('keydown', e => { if (e.key === 'Enter') loadLibrary(1); });
$('prev').onclick = () => loadLibrary(PAGE_N - 1);
$('next').onclick = () => loadLibrary(PAGE_N + 1);
</script>
</body>
</html>
"""
