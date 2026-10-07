"""The single-page interface, served inline.

One file, no build step, no external resources. The Content-Security-Policy the
server sets allows nothing off-origin, so the page must be self-contained --
which also rules out web fonts, hence the system stacks below.

Design notes, kept here so a later pass can see what was decided and why:

* Subject. A bench instrument for one scientist on their own machine: paste a
  protein sequence, get a ranked shortlist from a fixed library, inspect it
  compound by compound, export it. Not a product page and not a dashboard.
* The hero is the score distribution, not a headline number. A predicted pKi of
  9.17 means nothing without the spread it came out of, and the measured spreads
  are narrow (sd 0.53-0.90 across the diagnostic panel) with medians clustered
  near 6.4. So every ranking opens with all 25,000 scores drawn as a histogram
  and the kept rows marked inside it. This is the one piece of real subject
  matter that no generic layout would have produced.
* Boldness goes in one place: the molecular depictions. RDKit draws them
  black-on-white with CPK heteroatom colours, so the page stays a cool neutral
  and lets the structures carry the only strong colour. One accent (petrol) for
  everything interactive; amber and rust are status, not decoration.
* Type. Sans for prose and labels, monospace for *data only* -- sequences,
  SMILES, identifiers, digests and scores -- and monospace at display size for
  the numerals that matter. Never monospace on a label; that inversion is what
  makes data read as data here.
* Structure. Rules separate zones that genuinely differ. No numbered markers
  (nothing here is a sequence of steps), no eyebrow labels, no card kit.
* Light and dark both supported, but a depiction always sits on a white plate,
  like a light box on a dark bench -- black bonds on dark grey would be
  unreadable.
"""

from __future__ import annotations

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Seq2Lead</title>
<style>
:root{
  --paper:#FBFBF9; --panel:#FFFFFF; --sunk:#F2F3EF;
  --ink:#1B2227; --slate:#55646A; --rule:#D5D9D2;
  --accent:#0B5563; --accent-ink:#083F4A; --accent-soft:#E0EBED;
  --amber:#7A4E0C; --amber-soft:#FAF2E1; --alert:#8C2F1E; --alert-soft:#FBEDEA;
  --sans:"Avenir Next","Segoe UI",system-ui,-apple-system,Roboto,sans-serif;
  --mono:"SF Mono",Menlo,"Cascadia Mono","Roboto Mono",Consolas,monospace;
  --gap:clamp(.9rem,2.2vw,1.5rem);
}
@media (prefers-color-scheme:dark){
  :root{
    --paper:#141A1D; --panel:#1C2328; --sunk:#232B30;
    --ink:#E9EBE7; --slate:#A7B3B7; --rule:#333E44;
    --accent:#6FC0CF; --accent-ink:#A8DCE6; --accent-soft:#1E333A;
    --amber:#E0B569; --amber-soft:#2A2213; --alert:#F0988A; --alert-soft:#2C1A17;
  }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font:16px/1.55 var(--sans); overflow-wrap:break-word;
}
h1,h2,h3{line-height:1.2; margin:0; font-weight:600}
h1{font-size:1.4rem; letter-spacing:-.015em}
h2{font-size:1.1rem}
h3{font-size:.95rem}
p{margin:.4rem 0}
a{color:var(--accent-ink)}
code,.mono{font-family:var(--mono); font-variant-numeric:tabular-nums}
:focus-visible{outline:2.5px solid var(--accent); outline-offset:2px; border-radius:3px}
@media (prefers-reduced-motion:reduce){*{transition:none !important; animation:none !important}}

.skip{position:absolute; left:-9999px; top:0; background:var(--panel); color:var(--ink);
  padding:.6rem .9rem; z-index:9; border:2px solid var(--accent)}
.skip:focus{left:.5rem; top:.5rem}

.shell{max-width:1500px; margin:0 auto; padding:0 var(--gap) 3rem}

/* ---------------------------------------------------------- masthead */
.masthead{border-bottom:1px solid var(--rule); padding:1.2rem 0 .9rem; margin-bottom:1rem}
.masthead .lede{color:var(--slate); max-width:62ch; margin:.15rem 0 0}
.status{display:flex; flex-wrap:wrap; gap:.15rem 1.6rem; margin:.9rem 0 0; padding:0}
.status>div{min-width:0}
.status dt{font-size:.75rem; color:var(--slate); margin:0}
.status dd{margin:0; font-family:var(--mono); font-size:.82rem}

/* ------------------------------------------------------------- tabs */
.modes{display:flex; gap:.3rem; border-bottom:1px solid var(--rule); margin-bottom:var(--gap)}
.modes button{
  appearance:none; background:none; border:0; border-bottom:3px solid transparent;
  color:var(--slate); font:inherit; font-size:.95rem; padding:.7rem .95rem; min-height:44px;
  cursor:pointer; margin-bottom:-1px;
}
.modes button[aria-selected="true"]{color:var(--ink); border-bottom-color:var(--accent); font-weight:600}
.modes button:hover{color:var(--ink)}

/* ------------------------------------------------------------ bench */
.bench{display:grid; gap:var(--gap); align-items:start}
@media (min-width:1080px){ .bench{grid-template-columns:355px minmax(0,1fr)} }

.card{background:var(--panel); border:1px solid var(--rule); padding:1.1rem}
@media (min-width:1080px){ .query{position:sticky; top:1rem} }

label{display:block; font-size:.9rem; font-weight:600; margin-bottom:.15rem}
.hint{color:var(--slate); font-size:.82rem; margin:0 0 .45rem}
textarea,input[type=text],input[type=number]{
  width:100%; font-family:var(--mono); font-size:.85rem; color:var(--ink);
  background:var(--sunk); border:1px solid var(--rule); border-radius:3px; padding:.55rem .6rem;
}
textarea{min-height:11.5rem; resize:vertical; line-height:1.7; letter-spacing:.04em}
input[type=number]{min-height:44px}
.field+.field{margin-top:.9rem}
.filepick{margin:.7rem 0 0}
.filepick label{font-weight:400; font-size:.82rem}
.filepick input[type=file]{font:inherit; font-size:.8rem; width:100%; min-height:44px; padding:.4rem 0}
.filepick .hint{display:block; margin:.1rem 0 0}
details{margin-top:1rem; border-top:1px solid var(--rule); padding-top:.7rem}
summary{cursor:pointer; font-size:.9rem; font-weight:600; min-height:28px; padding:.3rem 0}
summary::marker{color:var(--accent)}
.pair{display:grid; grid-template-columns:1fr 1fr; gap:.6rem}
.pair label{font-weight:400; font-size:.82rem}
.scope{color:var(--slate); font-size:.78rem; margin:.5rem 0 0; border-left:2px solid var(--rule); padding-left:.6rem}

.actions{display:flex; gap:.5rem; flex-wrap:wrap; margin-top:1.1rem}
.btn{
  appearance:none; font:inherit; font-size:.9rem; font-weight:600; min-height:44px;
  padding:.6rem 1.1rem; border-radius:3px; cursor:pointer;
  background:var(--accent); color:#fff; border:1px solid var(--accent);
}
@media (prefers-color-scheme:dark){ .btn{color:#10191C} }
.btn:hover{background:var(--accent-ink); border-color:var(--accent-ink)}
.btn.quiet{background:none; color:var(--accent-ink); border-color:var(--rule)}
.btn.quiet:hover{background:var(--accent-soft)}
.btn:disabled{opacity:.45; cursor:not-allowed}
.btn.tiny{min-height:34px; padding:.3rem .65rem; font-size:.8rem; font-weight:400}
@media (pointer:coarse){ .btn.tiny{min-height:44px; padding:.55rem .9rem} summary{min-height:44px; display:flex; align-items:center} }

.msg{margin:.8rem 0 0; padding:.6rem .75rem; font-size:.86rem; border-left:3px solid}
.msg:empty{display:none}
.msg.bad{background:var(--alert-soft); border-color:var(--alert); color:var(--alert)}
.msg.note{background:var(--amber-soft); border-color:var(--amber); color:var(--amber)}
.msg ul{margin:.2rem 0 0; padding-left:1.1rem}

/* ----------------------------------------------------------- results */
.split{display:grid; gap:var(--gap); align-items:start}
@media (min-width:1340px){ .split.open{grid-template-columns:minmax(0,1fr) 370px} }

.empty{border:1px dashed var(--rule); padding:2.6rem 1.4rem; text-align:center; color:var(--slate)}
.empty h2{color:var(--ink); margin-bottom:.3rem}

.jobbar{display:flex; align-items:center; gap:.8rem; flex-wrap:wrap;
  background:var(--accent-soft); border:1px solid var(--rule); padding:.7rem .9rem}
.track{flex:1 1 9rem; height:6px; background:var(--panel); border:1px solid var(--rule); min-width:6rem}
.track>i{display:block; height:100%; background:var(--accent); transition:width .25s}

/* hero: the whole score distribution */
.dist{background:var(--panel); border:1px solid var(--rule); padding:1rem 1.1rem 1.2rem}
.dist h2{font-size:.95rem}
.dist .why{color:var(--slate); font-size:.8rem; max-width:70ch; margin:.1rem 0 .7rem}
.dist svg{display:block; width:100%; height:auto}
.quant{display:flex; flex-wrap:wrap; gap:.1rem 1.5rem; margin:.7rem 0 0; padding:0}
.quant>div{min-width:3.6rem}
.quant dt{font-size:.72rem; color:var(--slate)}
.quant dd{margin:0; font-family:var(--mono); font-size:1.02rem}

.qsum{display:flex; flex-wrap:wrap; gap:.1rem 1.5rem; margin:0 0 var(--gap); padding:.75rem .9rem;
  background:var(--sunk); border:1px solid var(--rule)}
.qsum dt{font-size:.72rem; color:var(--slate)}
.qsum dd{margin:0; font-family:var(--mono); font-size:.84rem}

.toolbar{display:flex; gap:.5rem; flex-wrap:wrap; align-items:center; margin:var(--gap) 0 .6rem}
.toolbar .count{color:var(--slate); font-size:.84rem; margin-right:auto}

/* rank rows */
.ranks{list-style:none; margin:0; padding:0; border:1px solid var(--rule); background:var(--panel)}
.ranks li+li{border-top:1px solid var(--rule)}
.rank{
  display:grid; grid-template-columns:2.6rem 76px minmax(0,1fr) auto; gap:.8rem; align-items:center;
  width:100%; text-align:left; background:none; border:0; font:inherit; color:inherit;
  padding:.6rem .8rem; cursor:pointer; min-height:72px;
}
.rank:hover{background:var(--sunk)}
.rank[aria-current="true"]{background:var(--accent-soft); box-shadow:inset 3px 0 0 var(--accent)}
.rank .n{font-family:var(--mono); font-size:1.15rem; color:var(--slate); text-align:right}
.plate{display:block; background:#fff; border:1px solid var(--rule); width:76px; height:60px; overflow:hidden}
.plate img, .plate svg{width:100%; height:100%; object-fit:contain; display:block}
.rank .mid{display:block; min-width:0; overflow:hidden}
.rank .id{display:block; font-family:var(--mono); font-size:.88rem}
.rank .smi{display:block; max-width:100%; font-family:var(--mono); font-size:.72rem;
  color:var(--slate); overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
.rank .sc{font-family:var(--mono); font-size:1.15rem; text-align:right; white-space:nowrap}
.rank .sc small{display:block; font-size:.66rem; color:var(--slate); font-family:var(--sans)}
.tags{display:flex; gap:.3rem; flex-wrap:wrap; margin-top:.2rem}
.tag{font-size:.68rem; padding:.05rem .4rem; border:1px solid var(--rule); color:var(--slate); border-radius:2px}
.tag.cut{border-color:var(--amber); color:var(--amber); background:var(--amber-soft)}
@media (max-width:620px){
  .rank{grid-template-columns:2.2rem 60px minmax(0,1fr); row-gap:.35rem}
  .plate{width:60px; height:50px}
  .rank .sc{grid-column:3; text-align:left; font-size:1rem}
  .rank .sc small{display:inline; margin-left:.35rem}
}

/* detail panel */
.detail{background:var(--panel); border:1px solid var(--rule); padding:1rem}
@media (min-width:1340px){ .detail{position:sticky; top:1rem} }
.detail .head{display:flex; justify-content:space-between; align-items:flex-start; gap:.5rem}
.detail h2{font-family:var(--mono); font-size:1.05rem}
.bigplate{background:#fff; border:1px solid var(--rule); margin:.8rem 0; padding:.3rem}
.bigplate img, .bigplate svg{width:100%; height:auto; display:block}
.smiles{background:var(--sunk); border:1px solid var(--rule); padding:.55rem .6rem; margin:.5rem 0 0;
  font-family:var(--mono); font-size:.78rem; line-height:1.5; overflow-wrap:anywhere; max-height:7rem; overflow:auto}
.props{display:grid; grid-template-columns:repeat(auto-fit,minmax(6.2rem,1fr)); gap:.55rem .8rem; margin:.9rem 0 0; padding:0}
.props dt{font-size:.72rem; color:var(--slate)}
.props dd{margin:0; font-family:var(--mono); font-size:.9rem; overflow-wrap:anywhere}
.caveat{color:var(--slate); font-size:.76rem; margin-top:.9rem; border-top:1px solid var(--rule); padding-top:.6rem}

/* ---------------------------------------------------------- library */
.libbar{display:grid; gap:.8rem; margin-bottom:var(--gap)}
@media (min-width:860px){ .libbar{grid-template-columns:minmax(0,2fr) minmax(0,3fr) auto; align-items:end} }
.grid{display:grid; gap:.8rem; grid-template-columns:repeat(auto-fill,minmax(188px,1fr))}
.tile{background:var(--panel); border:1px solid var(--rule); padding:0; width:100%; text-align:left;
  font:inherit; color:inherit; cursor:pointer; display:block}
.tile:hover{border-color:var(--accent)}
.tile[aria-current="true"]{border-color:var(--accent); box-shadow:inset 0 0 0 2px var(--accent)}
.tile .plate{width:100%; height:134px; border:0; border-bottom:1px solid var(--rule)}
.tile .meta{display:block; padding:.5rem .6rem}
.tile .id{display:block; font-family:var(--mono); font-size:.84rem}
.tile .mw{display:block; color:var(--slate); font-size:.74rem; font-family:var(--mono)}
.pager{display:flex; align-items:center; gap:.7rem; flex-wrap:wrap; margin-top:var(--gap)}
.pager .at{font-family:var(--mono); font-size:.84rem; color:var(--slate)}

footer{border-top:1px solid var(--rule); margin-top:2.4rem; padding-top:1rem; color:var(--slate); font-size:.82rem}
footer p{max-width:80ch}
.sr{position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap}
</style>
</head>
<body>
<a class="skip" href="#main">Skip to the workbench</a>
<div class="shell">

<header class="masthead">
  <h1>Seq2Lead</h1>
  <p class="lede">Rank a fixed compound library against one protein sequence, on this
  machine. Scores are ranking scores in pKi units, not measurements.</p>
  <dl class="status" id="status"><div><dt>Bundle</dt><dd>loading</dd></div></dl>
</header>

<nav class="modes" role="tablist" aria-label="Workbench mode">
  <button type="button" role="tab" id="tab-rank" aria-controls="panel-rank" aria-selected="true">Rank a sequence</button>
  <button type="button" role="tab" id="tab-lib" aria-controls="panel-lib" aria-selected="false" tabindex="-1">Browse the library</button>
</nav>

<main id="main">

<section id="panel-rank" role="tabpanel" aria-labelledby="tab-rank">
<div class="bench">

  <form class="card query" id="form" novalidate>
    <div class="field">
      <label for="seq">Protein sequence</label>
      <p class="hint" id="seq-hint">Plain amino acids, or one FASTA record. A file with more
      than one record is rejected rather than guessed at.</p>
      <textarea id="seq" name="seq" rows="10" spellcheck="false" autocapitalize="off"
        aria-describedby="seq-hint" placeholder="MKVLA..."></textarea>
      <p class="filepick">
        <label for="file">Or read a FASTA file</label>
        <input type="file" id="file" accept=".fasta,.fa,.faa,.txt" aria-describedby="file-hint">
        <span class="hint" id="file-hint">Read in this page and placed in the box above.
        Nothing is uploaded.</span>
      </p>
    </div>

    <div class="field">
      <label for="topn">Candidate pool</label>
      <p class="hint" id="topn-hint">How many of the scored library rows to keep and show.
      Every compound is always scored; this sets what comes back.</p>
      <input type="number" id="topn" name="topn" value="50" min="1" max="25000" step="1"
        aria-describedby="topn-hint">
    </div>

    <details id="adv">
      <summary>Advanced settings</summary>
      <p class="scope">These narrow the candidate pool above. They do not re-score anything
      and never change a rank: the unfiltered rank and score stay on every row, and removed
      rows are listed with the reason.</p>

      <div class="field">
        <h3>Molecular weight</h3>
        <div class="pair">
          <div><label for="min_mw">Minimum (Da)</label><input type="number" id="min_mw" min="0" max="5000" step="1"></div>
          <div><label for="max_mw">Maximum (Da)</label><input type="number" id="max_mw" min="0" max="5000" step="1"></div>
        </div>
      </div>

      <div class="field">
        <h3>Topological polar surface area</h3>
        <div class="pair">
          <div><label for="min_tpsa">Minimum (&#8491;&sup2;)</label><input type="number" id="min_tpsa" min="0" max="1000" step="1"></div>
          <div><label for="max_tpsa">Maximum (&#8491;&sup2;)</label><input type="number" id="max_tpsa" min="0" max="1000" step="1"></div>
        </div>
      </div>

      <div class="field">
        <h3>Diversity</h3>
        <div class="pair">
          <div><label for="diverse">Pick at most</label><input type="number" id="diverse" min="0" max="500" step="1" placeholder="off" aria-describedby="div-hint"></div>
          <div><label for="dthr">Tanimoto cut-off</label><input type="number" id="dthr" min="0.05" max="1" step="0.05" value="0.7"></div>
        </div>
        <p class="scope" id="div-hint">Greedy selection that skips a compound once it is more
        similar than the cut-off to one already picked. Measured top-20 shortlists average
        0.30-0.70 internal Tanimoto, so this is often the difference between twenty compounds
        and twenty variations of four.</p>
      </div>
    </details>

    <div class="actions">
      <button class="btn" type="submit" id="go">Rank library</button>
      <button class="btn quiet" type="button" id="stop" hidden>Cancel</button>
    </div>
    <p class="msg bad" id="err" role="alert"></p>
  </form>

  <div id="out">
    <div class="empty">
      <h2>No ranking yet</h2>
      <p>Paste a sequence and rank the library. The encoder stays loaded between
      queries, so the first run is the slow one.</p>
    </div>
  </div>

</div>
</section>

<section id="panel-lib" role="tabpanel" aria-labelledby="tab-lib" hidden>
  <div class="libbar card">
    <div>
      <label for="q">Search</label>
      <p class="hint" id="q-hint">Substring match on the compound identifier and on the SMILES
      text. Not a structure or similarity search.</p>
      <input type="text" id="q" aria-describedby="q-hint" placeholder="1382339 or OCCCCC">
    </div>
    <div>
      <label>Property windows</label>
      <p class="hint">Leave a box empty to leave that side open.</p>
      <div class="pair">
        <div><label for="l_min_mw">Min MW</label><input type="number" id="l_min_mw" min="0" max="5000" step="1"></div>
        <div><label for="l_max_mw">Max MW</label><input type="number" id="l_max_mw" min="0" max="5000" step="1"></div>
      </div>
    </div>
    <div class="actions" style="margin:0">
      <button class="btn" type="button" id="libgo">Apply</button>
      <button class="btn quiet" type="button" id="libclear">Clear</button>
    </div>
  </div>
  <p class="msg bad" id="liberr" role="alert"></p>
  <div class="split" id="libsplit">
    <div>
      <p class="toolbar"><span class="count" id="libcount" role="status">Loading the library...</span></p>
      <div class="grid" id="tiles"></div>
      <div class="pager">
        <button class="btn quiet" type="button" id="prev">Previous</button>
        <span class="at" id="at"></span>
        <button class="btn quiet" type="button" id="next">Next</button>
      </div>
    </div>
    <div id="libdetail"></div>
  </div>
</section>

</main>

<footer>
  <p id="foot"></p>
  <p>Runs on this machine only. Nothing is uploaded, and the page loads no external
  resource.</p>
</footer>
</div>

<p class="sr" id="say" role="status" aria-live="polite"></p>

<script>
"use strict";
const $ = (s, r) => (r || document).querySelector(s);
const el = (t, cls, txt) => { const n = document.createElement(t); if (cls) n.className = cls;
  if (txt !== undefined) n.textContent = txt; return n; };
const dl = (pairs) => { const f = document.createDocumentFragment();
  for (const [k, v] of pairs) { const d = el("div"); d.append(el("dt", null, k), el("dd", null, v)); f.append(d); }
  return f; };
const say = (m) => { $("#say").textContent = m; };
const num = (v, d) => { const n = Number(v); return Number.isFinite(n) ? n : d; };

/* SVG presentation attributes do not reliably accept var(), so the palette is
   read from the live stylesheet once per render. That also picks up whichever
   colour scheme is in force. */
function palette() {
  const css = getComputedStyle(document.body);
  const v = (n) => css.getPropertyValue(n).trim() || "#000";
  return { accent: v("--accent"), soft: v("--accent-soft"), rule: v("--rule"), slate: v("--slate") };
}

const state = { bundle: null, job: null, timer: null, result: null, picked: null, lib: { page: 1, pages: 1 } };

/* ------------------------------------------------------------- startup */
async function get(path) {
  const r = await fetch(path);
  const body = await r.json().catch(() => ({ error: r.statusText }));
  if (!r.ok) throw new Error(body.error || ("request failed: " + r.status));
  return body;
}

async function boot() {
  try {
    const b = await get("/api/bundle");
    state.bundle = b;
    const s = $("#status");
    s.textContent = "";
    s.append(dl([
      ["Bundle", b.bundle_version],
      ["Library", b.library.name + " (" + b.counts.compounds.toLocaleString() + " compounds)"],
      ["Embedding", b.protein.model.split("/").pop()],
      ["Device", b.device],
      ["Encoder", b.encoder_loaded ? "loaded" : "loads on first query"],
    ]));
    $("#foot").textContent = b.disclaimer;
    $("#topn").max = String(b.counts.compounds);
    loadLibrary();
  } catch (e) { fail($("#err"), e.message); }
}

function fail(node, msg) { node.textContent = msg; }

/* ---------------------------------------------------------------- tabs */
const tabs = [$("#tab-rank"), $("#tab-lib")];
tabs.forEach((t, i) => {
  t.addEventListener("click", () => showTab(i));
  t.addEventListener("keydown", (ev) => {
    const d = ev.key === "ArrowRight" ? 1 : ev.key === "ArrowLeft" ? -1 : 0;
    if (!d) return;
    ev.preventDefault();
    const j = (i + d + tabs.length) % tabs.length;
    showTab(j); tabs[j].focus();
  });
});
function showTab(i) {
  tabs.forEach((t, j) => {
    t.setAttribute("aria-selected", String(i === j));
    t.tabIndex = i === j ? 0 : -1;
    $("#" + t.getAttribute("aria-controls")).hidden = i !== j;
  });
}

/* -------------------------------------------------------------- ranking */
$("#file").addEventListener("change", (ev) => {
  const f = ev.target.files && ev.target.files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = () => {
    $("#seq").value = String(reader.result || "");
    fail($("#err"), "");
    say("Read " + f.name + " into the sequence box.");
    $("#seq").focus();
  };
  reader.onerror = () => fail($("#err"), "That file could not be read.");
  reader.readAsText(f);
});

$("#form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  fail($("#err"), "");
  const seq = $("#seq").value.trim();
  if (!seq) { fail($("#err"), "Paste a protein sequence first."); $("#seq").focus(); return; }

  const picked = $("#file").files && $("#file").files[0];
  const body = { sequence: seq, source: picked ? picked.name : "browser",
                 top_n: num($("#topn").value, 50) };
  for (const k of ["min_mw", "max_mw", "min_tpsa", "max_tpsa"]) {
    const raw = $("#" + k).value.trim();
    if (raw !== "") body[k] = Number(raw);
  }
  const d = $("#diverse").value.trim();
  if (d !== "" && Number(d) > 0) { body.diverse = Number(d); body.diversity_threshold = num($("#dthr").value, 0.7); }

  const lo = body.min_mw, hi = body.max_mw;
  if (lo !== undefined && hi !== undefined && lo > hi) {
    fail($("#err"), "Minimum molecular weight is above the maximum."); return;
  }

  try {
    const r = await fetch("/api/rank", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const job = await r.json();
    if (!r.ok) { fail($("#err"), job.error || "the server rejected the request"); return; }
    state.job = job.id;
    running(true);
    renderJob(job);
    poll();
  } catch (e) { fail($("#err"), e.message); }
});

$("#stop").addEventListener("click", async () => {
  if (!state.job) return;
  $("#stop").disabled = true;
  try { await fetch("/api/job/" + state.job + "/cancel", { method: "POST" }); say("Cancelling."); }
  catch (e) { fail($("#err"), e.message); }
});

function running(on) {
  $("#go").disabled = on;
  $("#stop").hidden = !on;
  $("#stop").disabled = false;
}

function poll() {
  clearTimeout(state.timer);
  state.timer = setTimeout(async () => {
    if (!state.job) return;
    try {
      const j = await get("/api/job/" + state.job);
      renderJob(j);
      if (j.state === "running" || j.state === "queued") { poll(); return; }
      running(false);
      if (j.state === "done") { state.result = j.result; renderResult(j.result); say("Ranking finished."); }
      else if (j.state === "cancelled") { idle("Ranking cancelled", "Nothing was changed. Rank again whenever you are ready."); say("Ranking cancelled."); }
      else { idle("The ranking failed", j.error || "No further detail was returned."); fail($("#err"), j.error || "the ranking failed"); }
    } catch (e) { running(false); fail($("#err"), e.message); }
  }, 350);
}

function idle(title, body) {
  const out = $("#out");
  out.textContent = "";
  const box = el("div", "empty");
  box.append(el("h2", null, title), el("p", null, body));
  out.append(box);
}

function renderJob(j) {
  let bar = $("#jobbar");
  if (!bar) {
    $("#out").textContent = "";
    bar = el("div", "jobbar"); bar.id = "jobbar";
    bar.setAttribute("role", "status"); bar.setAttribute("aria-live", "polite");
    const t = el("strong"); t.id = "phase";
    const track = el("div", "track"); track.append(el("i"));
    bar.append(t, track);
    $("#out").append(bar);
  }
  const pct = Math.round((j.progress || 0) * 100);
  const pos = j.queue_position;
  $("#phase", bar).textContent = j.state === "queued"
    ? (pos ? "Queued, " + pos + " ahead" : "Queued")
    : (j.phase || j.state) + " — " + pct + "%";
  $(".track>i", bar).style.width = pct + "%";
  if (j.state !== "running" && j.state !== "queued") bar.remove();
}

/* ------------------------------------------------- the distribution hero */
function histogram(d, keptLo, keptHi, keptN) {
  const W = 760, H = 150, PAD = 26, BASE = H - 22;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label",
    "Histogram of all " + d.n.toLocaleString() + " scores for this query, from " +
    d.min + " to " + d.max + " pKi, median " + d.median +
    ". The rows kept span " + keptLo.toFixed(2) + " to " + keptHi.toFixed(2) + ".");
  const mk = (t, a) => { const n = document.createElementNS("http://www.w3.org/2000/svg", t);
    for (const k in a) n.setAttribute(k, a[k]); return n; };

  const C = palette();
  const lo = d.edges[0], hi = d.edges[d.edges.length - 1], span = (hi - lo) || 1;
  const x = (v) => PAD + ((v - lo) / span) * (W - 2 * PAD);
  const peak = Math.max.apply(null, d.bins) || 1;

  // the band the kept rows occupy, drawn under the bars and labelled: an
  // unexplained rectangle is just a void on the chart.
  const bx = x(keptLo), bw = Math.max(2, x(keptHi) - bx);
  svg.append(mk("rect", { x: bx, y: 14, width: bw, height: BASE - 14,
    fill: C.soft, "fill-opacity": "0.75" }));
  svg.append(mk("line", { x1: bx, y1: 14, x2: bx + bw, y2: 14, stroke: C.accent, "stroke-width": 1.5 }));
  const tag = mk("text", { x: bx + bw / 2, y: 10, "font-size": "10.5", fill: C.accent,
    "text-anchor": "middle", "font-family": "monospace" });
  tag.textContent = keptN + " kept";
  svg.append(tag);

  d.bins.forEach((c, i) => {
    if (!c) return;
    const x0 = x(d.edges[i]), x1 = x(d.edges[i + 1]);
    const h = Math.max(1, (c / peak) * (BASE - 12));
    const inKept = d.edges[i + 1] >= keptLo;
    svg.append(mk("rect", { x: x0, y: BASE - h, width: Math.max(1, x1 - x0 - 0.6), height: h,
      fill: C.accent, "fill-opacity": inKept ? "0.95" : "0.38" }));
  });

  svg.append(mk("line", { x1: PAD, y1: BASE, x2: W - PAD, y2: BASE, stroke: C.rule, "stroke-width": 1 }));
  for (const [v, label] of [[d.min, d.min.toFixed(2)], [d.median, "median " + d.median.toFixed(2)], [d.max, d.max.toFixed(2)]]) {
    svg.append(mk("line", { x1: x(v), y1: BASE, x2: x(v), y2: BASE + 5, stroke: C.slate }));
    const t = mk("text", { x: x(v), y: H - 4, "font-size": "11", fill: C.slate,
      "text-anchor": v === d.min ? "start" : v === d.max ? "end" : "middle",
      "font-family": "monospace" });
    t.textContent = label;
    svg.append(t);
  }
  return svg;
}

/* ------------------------------------------------------ ranking results */
function renderResult(r) {
  const out = $("#out");
  out.textContent = "";
  state.picked = null;

  const q = el("dl", "qsum");
  q.append(dl([
    ["Query", r.query.header || "(no header)"],
    ["Residues", String(r.query.length)],
    ["Sequence SHA-256", r.query.sha256.slice(0, 16) + "…"],
    ["Device", r.device],
  ]));
  out.append(q);

  for (const [title, items] of [["What to know about this ranking", r.warnings], ["Notes on the sequence", r.notes]]) {
    if (!items || !items.length) continue;
    const box = el("div", "msg note");
    box.append(el("strong", null, title));
    const ul = el("ul");
    items.forEach((w) => ul.append(el("li", null, w)));
    box.append(ul);
    out.append(box);
  }

  const kept = r.rows;
  if (r.distribution && r.distribution.bins) {
    const d = r.distribution;
    const lo = Math.min.apply(null, kept.map((x) => x.score_pki));
    const hi = Math.max.apply(null, kept.map((x) => x.score_pki));
    const card = el("div", "dist");
    card.append(el("h2", null, "Every score for this query"));
    card.append(el("p", "why", "All " + d.n.toLocaleString() +
      " compounds were scored. The shaded band is where the " + kept.length +
      " rows below sit. A high pKi only means something against this spread."));
    card.append(histogram(d, lo, hi, kept.length));
    const qd = el("dl", "quant");
    qd.append(dl([["Lowest", d.min.toFixed(2)], ["25th", d.p25.toFixed(2)], ["Median", d.median.toFixed(2)],
      ["75th", d.p75.toFixed(2)], ["Highest", d.max.toFixed(2)], ["SD", d.sd.toFixed(3)]]));
    card.append(qd);
    out.append(card);
  }

  const sl = r.shortlist;
  if (sl) {
    const box = el("div", "msg note");
    box.append(el("strong", null, "Shortlisting applied"));
    const ul = el("ul");
    (sl.applied || []).forEach((a) => ul.append(el("li", null, a)));
    ul.append(el("li", null, sl.kept + " of " + kept.length + " rows kept, " + sl.removed + " removed. Ranks and scores below are the unfiltered ones."));
    box.append(ul);
    out.append(box);
  }

  const bar = el("p", "toolbar");
  const n = el("span", "count");
  n.id = "rcount";
  n.textContent = kept.length + " rows from " + r.library.members.toLocaleString() + " scored compounds";
  const csv = el("a", "btn tiny", "Download CSV");
  csv.href = "/api/job/" + state.job + ".csv";
  csv.setAttribute("download", "");
  const ids = el("button", "btn tiny quiet", "Copy identifiers");
  ids.type = "button";
  ids.addEventListener("click", () => copy(kept.map((x) => x.compound_id).join("\n"), ids, "Identifiers copied"));
  bar.append(n, csv, ids);
  out.append(bar);

  const split = el("div", "split");
  split.id = "rsplit";
  const list = el("ul", "ranks");
  list.id = "ranks";
  const keptSet = sl ? new Set(sl.kept_ids) : null;
  const whyGone = {};
  if (sl) (sl.removed_rows || []).forEach((g) => { whyGone[g.compound_id] = g.detail || g.reason; });

  kept.forEach((row, i) => {
    const li = el("li");
    const b = el("button", "rank");
    b.type = "button";
    b.dataset.row = String(row.row);
    b.dataset.i = String(i);
    b.append(el("span", "n", String(row.rank)));

    const plate = el("span", "plate");
    if (row.row >= 0) {
      const img = el("img");
      img.src = "/api/depict?row=" + row.row + "&w=152&h=120";
      img.alt = "";
      img.width = 76; img.height = 60;
      img.loading = "lazy";
      plate.append(img);
    }
    b.append(plate);

    const mid = el("span", "mid");
    mid.append(el("span", "id", row.compound_id));
    mid.append(el("span", "smi", row.smiles));
    const tags = el("span", "tags");
    if (row.tied_with) tags.append(el("span", "tag", "tied with " + row.tied_with));
    (row.flags || []).forEach((f) => tags.append(el("span", "tag", f)));
    if (keptSet && !keptSet.has(row.compound_id)) {
      tags.append(el("span", "tag cut", "filtered out: " + (whyGone[row.compound_id] || "no reason given")));
    }
    if (tags.childNodes.length) mid.append(tags);
    b.append(mid);

    const sc = el("span", "sc", row.score_pki.toFixed(2));
    sc.append(el("small", null, "pKi"));
    b.append(sc);

    b.addEventListener("click", () => pick(row, b));
    b.addEventListener("keydown", (ev) => {
      const d = ev.key === "ArrowDown" ? 1 : ev.key === "ArrowUp" ? -1 : 0;
      if (!d) return;
      ev.preventDefault();
      const all = list.querySelectorAll(".rank");
      const j = Math.min(all.length - 1, Math.max(0, i + d));
      all[j].focus();
    });
    li.append(b);
    list.append(li);
  });

  const holder = el("div");
  holder.append(list);
  const det = el("div");
  det.id = "rdetail";
  split.append(holder, det);
  out.append(split);
}

async function pick(row, btn) {
  const list = btn.closest(".ranks");
  list.querySelectorAll('.rank[aria-current="true"]').forEach((x) => x.removeAttribute("aria-current"));
  btn.setAttribute("aria-current", "true");
  const host = $("#rdetail");
  $("#rsplit").classList.add("open");
  host.textContent = "";
  host.append(el("p", null, "Loading " + row.compound_id + "…"));
  try {
    const d = await get("/api/compound?row=" + row.row);
    host.textContent = "";
    host.append(detailCard(d, row, () => { btn.focus(); $("#rsplit").classList.remove("open"); host.textContent = ""; }));
    const h = $("h2", host);
    h.tabIndex = -1;
    h.focus();
  } catch (e) {
    host.textContent = "";
    const m = el("p", "msg bad", e.message);
    host.append(m);
  }
}

function detailCard(d, row, onClose) {
  const card = el("div", "detail");
  const head = el("div", "head");
  const h = el("h2", null, d.compound_id);
  const x = el("button", "btn tiny quiet", "Close");
  x.type = "button";
  x.addEventListener("click", onClose);
  head.append(h, x);
  card.append(head);

  if (row) {
    const r = el("dl", "props");
    r.style.margin = ".7rem 0 0";
    r.append(dl([["Rank", String(row.rank)], ["Predicted", row.score_pki.toFixed(3) + " pKi"]]));
    card.append(r);
  }

  const plate = el("div", "bigplate");
  const img = el("img");
  img.src = "/api/depict?row=" + d.row + "&w=620&h=460";
  img.alt = "Structure of compound " + d.compound_id;
  plate.append(img);
  card.append(plate);

  card.append(el("h3", null, "SMILES"));
  const smi = el("div", "smiles mono");
  smi.textContent = d.smiles;
  smi.tabIndex = 0;
  card.append(smi);
  const copyBtn = el("button", "btn tiny quiet", "Copy SMILES");
  copyBtn.type = "button";
  copyBtn.style.marginTop = ".5rem";
  copyBtn.addEventListener("click", () => copy(d.smiles, copyBtn, "SMILES copied"));
  card.append(copyBtn);

  if (d.descriptors) {
    const p = d.descriptors;
    card.append(el("h3", null, "Computed properties"));
    const props = el("dl", "props");
    props.append(dl([
      ["Formula", p.formula],
      ["MW", p.molecular_weight.toFixed(1)],
      ["cLogP", p.clogp.toFixed(2)],
      ["TPSA", p.tpsa.toFixed(1)],
      ["HBD", String(p.h_bond_donors)],
      ["HBA", String(p.h_bond_acceptors)],
      ["Rot. bonds", String(p.rotatable_bonds)],
      ["Rings", p.rings + " (" + p.aromatic_rings + " aromatic)"],
      ["Heavy atoms", String(p.heavy_atoms)],
      ["Stereocentres", String(p.stereocentres)],
    ]));
    card.append(props);
  } else {
    card.append(el("p", "msg note", "RDKit could not parse this SMILES, so no properties are shown."));
  }

  card.append(el("p", "caveat",
    "Properties are computed from the structure above, not predicted. The model saw only this " +
    "compound's ECFP4 fingerprint, which is blind to differences beyond two bonds — some " +
    "library members share a fingerprint and therefore score identically for every query."));
  return card;
}

async function copy(text, btn, done) {
  const was = btn.textContent;
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = done;
    say(done);
  } catch (e) {
    btn.textContent = "Press " + (navigator.platform.indexOf("Mac") === 0 ? "⌘C" : "Ctrl+C");
    say("Copying was blocked. The text is selectable.");
  }
  setTimeout(() => { btn.textContent = was; }, 2200);
}

/* -------------------------------------------------------------- library */
function libQuery() {
  const p = new URLSearchParams({ page: String(state.lib.page), page_size: "24" });
  const q = $("#q").value.trim();
  if (q) p.set("q", q);
  for (const [id, key] of [["l_min_mw", "min_mw"], ["l_max_mw", "max_mw"]]) {
    const v = $("#" + id).value.trim();
    if (v !== "") p.set(key, v);
  }
  return p.toString();
}

async function loadLibrary() {
  fail($("#liberr"), "");
  try {
    const d = await get("/api/library?" + libQuery());
    state.lib.page = d.page;
    state.lib.pages = d.pages;
    const tiles = $("#tiles");
    tiles.textContent = "";
    d.rows.forEach((row) => {
      const b = el("button", "tile");
      b.type = "button";
      const plate = el("span", "plate");
      const img = el("img");
      img.src = "/api/depict?row=" + row.row + "&w=250&h=180";
      img.alt = "";
      img.loading = "lazy";
      plate.append(img);
      const meta = el("span", "meta");
      meta.append(el("span", "id", row.compound_id));
      meta.append(el("span", "mw", row.properties
        ? row.properties.molecular_weight.toFixed(0) + " Da, TPSA " + row.properties.tpsa.toFixed(0)
        : "no descriptors"));
      b.append(plate, meta);
      b.addEventListener("click", async () => {
        $("#tiles").querySelectorAll('[aria-current="true"]').forEach((t) => t.removeAttribute("aria-current"));
        b.setAttribute("aria-current", "true");
        const host = $("#libdetail");
        $("#libsplit").classList.add("open");
        host.textContent = "";
        try {
          const det = await get("/api/compound?row=" + row.row);
          host.append(detailCard(det, null, () => { b.focus(); $("#libsplit").classList.remove("open"); host.textContent = ""; }));
          const h = $("h2", host); h.tabIndex = -1; h.focus();
        } catch (e) { host.append(el("p", "msg bad", e.message)); }
      });
      tiles.append(b);
    });
    const shown = d.total.toLocaleString();
    $("#libcount").textContent = d.total === d.library_total
      ? shown + " compounds"
      : shown + " of " + d.library_total.toLocaleString() + " compounds match" +
        (d.filtered_out ? " (" + d.filtered_out.toLocaleString() + " excluded)" : "");
    $("#at").textContent = "page " + d.page + " of " + d.pages;
    $("#prev").disabled = d.page <= 1;
    $("#next").disabled = d.page >= d.pages;
  } catch (e) { fail($("#liberr"), e.message); $("#libcount").textContent = "Nothing loaded."; }
}

$("#libgo").addEventListener("click", () => { state.lib.page = 1; loadLibrary(); });
$("#libclear").addEventListener("click", () => {
  ["q", "l_min_mw", "l_max_mw"].forEach((id) => { $("#" + id).value = ""; });
  state.lib.page = 1; loadLibrary();
});
$("#q").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); state.lib.page = 1; loadLibrary(); } });
$("#prev").addEventListener("click", () => { if (state.lib.page > 1) { state.lib.page--; loadLibrary(); } });
$("#next").addEventListener("click", () => { if (state.lib.page < state.lib.pages) { state.lib.page++; loadLibrary(); } });

boot();
</script>
</body>
</html>
"""

__all__ = ["PAGE"]
