"""The canvas page, inlined.

Self-contained by requirement: no CDN, no external font, no remote image. The
whole thing is one string so the server has nothing to serve from disk and the
page cannot silently acquire a dependency.

The browser holds no authority over the world. It receives tile-grid positions
and eases actors toward them; if a poll is missed the office is simply a moment
stale rather than wrong.
"""

from __future__ import annotations

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ajax HQ · Floor</title>
<style>
  :root {
    --ink: #0B1220; --panel: #111A2B; --gold: #C8A951; --gold-hi: #E3C46B;
    --dim: #8A93A8; --faint: #5C6580; --line: rgba(200,169,81,0.12);
    --green: #4ADE80; --cyan: #22D3EE;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--ink); color: #E8ECF4; min-height: 100vh;
    font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  header {
    display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap;
    padding: 18px 20px 12px; border-bottom: 1px solid var(--line);
  }
  h1 {
    margin: 0; font: 600 20px/1.2 ui-serif, Georgia, serif;
    letter-spacing: 0.06em; color: var(--gold-hi);
  }
  .badge {
    padding: 2px 10px; border-radius: 2px; font-size: 11px; font-weight: 700;
    letter-spacing: 0.1em; background: var(--gold); color: #0B1220;
  }
  .badge.replay { background: var(--cyan); }
  .stats { color: var(--dim); font-size: 12px; }
  main { display: grid; grid-template-columns: minmax(min(760px,100%),3fr) minmax(260px,1fr);
         gap: 18px; padding: 18px 20px 8px; align-items: start; }
  @media (max-width: 900px) { main { grid-template-columns: 1fr; } }
  #floor { width: 100%; height: auto; display: block; background: #070C15;
           border: 1px solid var(--line); border-radius: 4px; }
  .roster { border: 1px solid var(--line); border-radius: 4px; background: var(--panel);
            max-height: 70vh; overflow-y: auto; }
  .who { display: grid; grid-template-columns: 10px 1fr auto; gap: 8px; align-items: center;
         padding: 8px 10px; border-bottom: 1px solid rgba(255,255,255,0.04); }
  .who:last-child { border-bottom: 0; }
  .who > span { min-width: 0; }   /* lets the ellipsis actually engage */
  .lamp { width: 8px; height: 8px; border-radius: 50%; background: var(--faint); }
  .lamp.working { background: var(--green); box-shadow: 0 0 8px rgba(74,222,128,.6); }
  .lamp.errand, .lamp.visiting { background: var(--gold-hi); }
  .name { font-weight: 600; font-size: 12px; overflow: hidden; text-overflow: ellipsis;
          white-space: nowrap; }
  .meta { font-size: 11px; color: var(--faint); overflow: hidden; text-overflow: ellipsis;
          white-space: nowrap; }
  .count { font-size: 11px; color: var(--dim); font-variant-numeric: tabular-nums; }
  footer { padding: 4px 20px 24px; color: var(--faint); font-size: 11px; max-width: 90ch; }
  footer b { color: var(--dim); font-weight: 600; }
</style>
</head>
<body>
<header>
  <h1>AJAX HQ · 사무실</h1>
  <span class="badge" id="mode">LIVE</span>
  <span class="stats" id="stats">connecting…</span>
</header>
<main>
  <canvas id="floor" width="1200" height="360"></canvas>
  <div class="roster" id="roster"></div>
</main>
<footer id="caveat"></footer>
<script>
const CELL = 20, PAD = 8;
const TILE_FILL = { 0: '#1b2436', 2: '#33405c', 3: '#212d45' };  // wall, door, desk
let world = null, actors = new Map();

const canvas = document.getElementById('floor');
const ctx = canvas.getContext('2d');

async function loadWorld() {
  world = await (await fetch('/api/world')).json();
  canvas.width = world.width * CELL + PAD * 2;
  canvas.height = world.height * CELL + PAD * 2;
}

async function poll() {
  try {
    const state = await (await fetch('/api/state')).json();
    document.getElementById('mode').textContent = state.live ? 'LIVE' : 'REPLAY';
    document.getElementById('mode').className = 'badge' + (state.live ? '' : ' replay');
    document.getElementById('stats').textContent =
      state.events + ' real events applied · ' + state.errands + ' errands · ' +
      state.actors.length + ' on the floor' +
      (state.live ? '' : ' · ' + state.remaining + ' left to replay');
    document.getElementById('caveat').innerHTML = (state.live ? '' :
      '<b>Replay of events already on disk — history, not live activity.</b><br>') +
      'Every move between wings is a real tool call from a transcript. ' +
      'Drifting inside a wing is decoration: nothing on disk records where anyone stands.';

    for (const a of state.actors) {
      const existing = actors.get(a.id);
      if (existing) {
        existing.tx = a.x; existing.ty = a.y; Object.assign(existing.data, a);
      } else {
        actors.set(a.id, { x: a.x, y: a.y, tx: a.x, ty: a.y, data: a });
      }
    }
    for (const id of [...actors.keys()]) {
      if (!state.actors.some(a => a.id === id)) actors.delete(id);
    }
    drawRoster(state.actors);
  } catch (err) { /* a missed poll leaves the floor a moment stale, not wrong */ }
}

function drawRoster(list) {
  document.getElementById('roster').innerHTML = list.map(a => `
    <div class="who">
      <span class="lamp ${a.state}"></span>
      <span>
        <div class="name">${escapeHtml(a.name)}</div>
        <div class="meta">${a.wing} · ${a.state_label}${a.detail ? ' · ' + escapeHtml(a.detail) : ''}</div>
      </span>
      <span class="count">${a.events}</span>
    </div>`).join('');
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function draw() {
  requestAnimationFrame(draw);
  if (!world) return;

  ctx.fillStyle = '#070C15';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  for (let y = 0; y < world.height; y++) {
    for (let x = 0; x < world.width; x++) {
      const t = world.tiles[y][x];
      if (t === 1) continue;                       // open floor: leave it dark
      ctx.fillStyle = TILE_FILL[t];
      ctx.fillRect(PAD + x * CELL, PAD + y * CELL, CELL - 1, CELL - 1);
    }
  }

  ctx.font = '600 11px ui-monospace, monospace';
  for (const room of world.rooms) {
    ctx.fillStyle = '#C8A951';
    ctx.fillText(room.code, PAD + room.x * CELL + 8, PAD + room.y * CELL + 14);
    ctx.fillStyle = 'rgba(138,147,168,0.55)';
    ctx.fillText(room.korean, PAD + room.x * CELL + 44, PAD + room.y * CELL + 14);
  }

  for (const a of actors.values()) {
    a.x += (a.tx - a.x) * 0.22;
    a.y += (a.ty - a.y) * 0.22;
    const cx = PAD + a.x * CELL + CELL / 2, cy = PAD + a.y * CELL + CELL / 2;
    const st = a.data.state;
    const color = st === 'working' ? '#4ADE80'
                : (st === 'errand' || st === 'visiting') ? '#E3C46B'
                : '#6B7794';
    if (a.data.real) {
      ctx.beginPath(); ctx.arc(cx, cy, CELL * 0.62, 0, Math.PI * 2);
      ctx.fillStyle = color + '22'; ctx.fill();
    }
    ctx.beginPath(); ctx.arc(cx, cy, CELL * 0.32, 0, Math.PI * 2);
    ctx.fillStyle = color; ctx.fill();
    if (a.data.principal) {
      ctx.strokeStyle = '#C8A951'; ctx.lineWidth = 2; ctx.stroke();
    }
    ctx.fillStyle = 'rgba(232,236,244,0.72)';
    ctx.font = '10px ui-monospace, monospace';
    const label = a.data.name.length > 16 ? a.data.name.slice(0, 15) + '…' : a.data.name;
    ctx.fillText(label, cx - ctx.measureText(label).width / 2, cy - CELL * 0.55);
  }
}

loadWorld().then(() => { poll(); setInterval(poll, 400); draw(); });
</script>
</body>
</html>
"""
