"""Inlined CSS, the company seal, and the refresh script.

Everything is embedded in the generated page: no CDN, no external fonts, no
remote images. The page must render identically with the network unplugged,
because it is an operations view of local state and a half-loaded dashboard is
worse than a plain one.
"""

from __future__ import annotations

# Drawn rather than fetched, so the page stays self-contained.
SEAL_SVG = """
<svg class="seal" viewBox="0 0 64 64" role="img" aria-label="Ajax HQ seal">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#E3C46B"/>
      <stop offset="100%" stop-color="#A88A3C"/>
    </linearGradient>
  </defs>
  <path d="M32 2 L58 17 L58 47 L32 62 L6 47 L6 17 Z" fill="none"
        stroke="url(#g)" stroke-width="1.6"/>
  <path d="M32 8 L52 20 L52 44 L32 56 L12 44 L12 20 Z" fill="none"
        stroke="url(#g)" stroke-width="0.7" opacity="0.55"/>
  <text x="32" y="38" text-anchor="middle" font-size="20"
        font-family="ui-serif, Georgia, serif" fill="url(#g)" letter-spacing="1">A</text>
</svg>
"""

CSS = """
:root {
  --ink:        #0B1220;
  --panel:      #111A2B;
  --panel-hi:   #16203A;
  --gold:       #C8A951;
  --gold-hi:    #E3C46B;
  --gold-dim:   rgba(200,169,81,0.14);
  --text:       #E8EAF0;
  --muted:      #8A93A8;
  --faint:      #5C6580;
  --pos:        #57D48A;
  --neg:        #F0736F;
  --warn:       #E0A93F;
  --serif: ui-serif, Georgia, "Times New Roman", serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background:
    radial-gradient(1200px 600px at 50% -200px, #16233d 0%, transparent 70%),
    var(--ink);
  color: var(--text);
  font-family: var(--sans);
  font-size: 14px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}

.wrap { max-width: 1440px; margin: 0 auto; padding: 0 24px 72px; }

/* ---------------------------------------------------------------- masthead */

.masthead {
  display: flex; align-items: center; gap: 20px;
  padding: 26px 0 18px;
  border-bottom: 1px solid var(--gold-dim);
}
.seal { width: 52px; height: 52px; flex: none; }
.brand h1 {
  margin: 0; font-family: var(--serif); font-weight: 500;
  font-size: 30px; letter-spacing: 0.16em; color: var(--gold-hi);
}
.brand .ko {
  margin: 2px 0 0; font-size: 12px; letter-spacing: 0.3em;
  color: var(--faint); text-transform: none;
}
.masthead .spacer { flex: 1; }
.clock { text-align: right; font-family: var(--mono); font-size: 12px; color: var(--muted); }
.clock strong { display: block; color: var(--gold); font-size: 13px; letter-spacing: 0.06em; }

.banner {
  margin: 16px 0 0; padding: 10px 14px; border-radius: 3px;
  border-left: 2px solid var(--warn); background: rgba(224,169,63,0.07);
  font-size: 13px; color: #E9D9B4;
}
.banner.ok { border-left-color: var(--gold); background: rgba(200,169,81,0.05); color: var(--muted); }
.banner.bad { border-left-color: var(--neg); background: rgba(240,115,111,0.08); color: #F3C9C7; }

/* ------------------------------------------------------------ section head */

.section { margin-top: 40px; }
.section > h2 {
  margin: 0 0 14px; font-family: var(--serif); font-weight: 500;
  font-size: 13px; letter-spacing: 0.26em; text-transform: uppercase;
  color: var(--gold); display: flex; align-items: baseline; gap: 12px;
}
.section > h2::after {
  content: ""; flex: 1; height: 1px; background: var(--gold-dim);
}
.section > h2 .ko { font-size: 11px; letter-spacing: 0.18em; color: var(--faint); }

/* ------------------------------------------------------------- stat strip */

.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(160px, 100%),1fr)); gap: 1px;
         background: var(--gold-dim); border: 1px solid var(--gold-dim); }
.stat { background: var(--panel); padding: 16px 18px; }
.stat .k { font-size: 10px; letter-spacing: 0.18em; text-transform: uppercase; color: var(--faint); }
.stat .v { font-family: var(--mono); font-variant-numeric: tabular-nums;
           font-size: 24px; color: var(--gold-hi); margin-top: 6px; }
.stat .s { font-size: 11px; color: var(--muted); margin-top: 2px; }

/* -------------------------------------------------------------- divisions */

/* 400px minimum lands the six divisions as two balanced rows of three on a
   desktop viewport, rather than four-then-two. The min() is load-bearing: a bare
   400px floor cannot shrink below itself and overflows a 390px phone. */
.divisions {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(400px, 100%), 1fr));
  gap: 14px;
}
.division {
  background: linear-gradient(180deg, var(--panel-hi) 0%, var(--panel) 60%);
  border: 1px solid var(--gold-dim); border-radius: 3px; padding: 16px 18px 14px;
  display: flex; flex-direction: column; gap: 10px;
}
.division .top { display: flex; align-items: flex-start; gap: 10px; }
.division .code {
  font-family: var(--mono); font-size: 10px; letter-spacing: 0.12em;
  color: var(--ink); background: var(--gold); padding: 3px 6px; border-radius: 2px;
  font-weight: 700; flex: none;
}
.division h3 { margin: 0; font-family: var(--serif); font-size: 17px; font-weight: 500; }
.division h3 .ko { display: block; font-family: var(--sans); font-size: 11px;
                   letter-spacing: 0.16em; color: var(--faint); margin-top: 1px; }
.division .mandate { font-size: 12px; color: var(--muted); margin: 0; }
.division dl { display: grid; grid-template-columns: 1fr auto; gap: 4px 10px; margin: 4px 0 0; }
.division dt { font-size: 11px; color: var(--faint); letter-spacing: 0.04em; }
.division dd { margin: 0; font-family: var(--mono); font-variant-numeric: tabular-nums;
               font-size: 13px; text-align: right; color: var(--text); }
.division .note { font-size: 11px; color: var(--faint); border-top: 1px solid var(--gold-dim);
                  padding-top: 8px; margin: 2px 0 0; }
.division .src { font-family: var(--mono); font-size: 10px; color: #46506A; }

/* ------------------------------------------------------------------ pills */

.pill {
  font-family: var(--mono); font-size: 9px; letter-spacing: 0.14em;
  padding: 3px 7px; border-radius: 2px; border: 1px solid currentColor;
  white-space: nowrap; flex: none;
}
.pill.active, .pill.completed { color: var(--pos); }
.pill.idle { color: var(--muted); }
.pill.degraded, .pill.unknown { color: var(--warn); }
.pill.never_active { color: var(--faint); }
.pill.running { color: var(--gold-hi); animation: breathe 2.4s ease-in-out infinite; }
@keyframes breathe { 0%,100% { opacity: 1 } 50% { opacity: 0.45 } }
@media (prefers-reduced-motion: reduce) { .pill.running { animation: none } }

/* ----------------------------------------------------------------- tables */

.scroll { overflow-x: auto; border: 1px solid var(--gold-dim); background: var(--panel); }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th {
  text-align: left; font-family: var(--sans); font-weight: 500; font-size: 10px;
  letter-spacing: 0.16em; text-transform: uppercase; color: var(--faint);
  padding: 10px 14px; border-bottom: 1px solid var(--gold-dim); white-space: nowrap;
}
tbody td { padding: 11px 14px; border-bottom: 1px solid rgba(200,169,81,0.06); vertical-align: top; }
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover { background: rgba(200,169,81,0.03); }
.num { font-family: var(--mono); font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
.dim { color: var(--muted); }
.mono { font-family: var(--mono); font-size: 12px; }
.pos { color: var(--pos); } .neg { color: var(--neg); }

/* ------------------------------------------------------------- drill-down */

tbody tr.drill-row td { padding-top: 0; padding-bottom: 12px; }
tbody tr.drill-row:hover { background: none; }
details.drill { margin-top: 0; }
details.drill > summary {
  cursor: pointer; font-size: 11px; color: var(--gold); letter-spacing: 0.08em;
  list-style: none; user-select: none;
}
details.drill > summary::-webkit-details-marker { display: none; }
details.drill > summary::before { content: "▸ "; }
details.drill[open] > summary::before { content: "▾ "; }
details.drill pre {
  margin: 8px 0 0; padding: 12px; background: var(--ink); border: 1px solid var(--gold-dim);
  border-radius: 2px; font-family: var(--mono); font-size: 11px; line-height: 1.5;
  color: #C3CADB; white-space: pre-wrap; word-break: break-word;
  max-height: 340px; overflow-y: auto;
}
details.drill .label { font-size: 10px; letter-spacing: 0.16em; text-transform: uppercase;
                       color: var(--faint); margin-top: 10px; }

/* --------------------------------------------------------------- timeline */

.timeline { border-left: 1px solid var(--gold-dim); margin-left: 6px; padding-left: 18px; }
.event { position: relative; padding: 7px 0; font-size: 13px; }
.event::before {
  content: ""; position: absolute; left: -23px; top: 14px; width: 7px; height: 7px;
  border-radius: 50%; background: var(--gold); opacity: 0.6;
}
.event .t { font-family: var(--mono); font-size: 11px; color: var(--faint); margin-right: 10px; }

/* ------------------------------------------------------------ empty/foot */

.empty {
  border: 1px dashed var(--gold-dim); border-radius: 3px; padding: 22px;
  text-align: center; color: var(--muted); font-size: 13px; background: rgba(17,26,43,0.5);
}
.empty code { font-family: var(--mono); color: var(--gold); font-size: 12px; }

footer { margin-top: 48px; padding-top: 16px; border-top: 1px solid var(--gold-dim);
         font-size: 11px; color: var(--faint); }
footer table { font-size: 11px; }
footer td { padding: 4px 12px 4px 0; border: none; }

@media (max-width: 720px) {
  .wrap { padding: 0 14px 48px; }
  .masthead { flex-wrap: wrap; }
  .brand h1 { font-size: 22px; }
}
"""

# Only used when served: polls for a changed generation stamp and reloads.
# The static file has no script at all.
REFRESH_JS = """
(function () {
  var current = document.body.getAttribute('data-generated');
  setInterval(function () {
    fetch('/api/generated', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (stamp) {
        if (stamp && stamp.trim() && stamp.trim() !== current) { location.reload(); }
      })
      .catch(function () { /* server gone; keep showing the last good page */ });
  }, %(interval)d);
})();
"""


def refresh_script(interval_seconds: int = 20) -> str:
    return REFRESH_JS % {"interval": max(interval_seconds, 5) * 1000}
