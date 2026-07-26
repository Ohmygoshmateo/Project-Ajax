"""The canvas page, inlined.

Self-contained by requirement: no CDN, no external font, no remote image, no
sprite sheet to fetch. Characters are drawn from pixel data defined here and
baked into offscreen canvases once at load, so the whole game is one HTML
string the server holds in memory.

Everything is drawn at 1 sprite-pixel = ``SCALE`` screen pixels with image
smoothing off, which is what makes it read as pixel art rather than as small
smooth graphics.

The browser holds no authority over the world. It receives fractional tile
positions and a facing, and animates between polls; a missed poll leaves the
office a moment stale rather than wrong.
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
    --dim: #8A93A8; --faint: #5C6580; --line: rgba(200,169,81,0.14);
    --green: #4ADE80; --cyan: #22D3EE;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--ink); color: #E8ECF4; min-height: 100vh;
    font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  header {
    display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
    padding: 16px 20px 10px; border-bottom: 1px solid var(--line);
  }
  h1 { margin: 0; font: 600 19px/1.2 ui-serif, Georgia, serif;
       letter-spacing: 0.06em; color: var(--gold-hi); }
  .badge { padding: 2px 9px; border-radius: 2px; font-size: 10px; font-weight: 700;
           letter-spacing: 0.12em; background: var(--gold); color: #0B1220; }
  .badge.replay { background: var(--cyan); }
  .stats { color: var(--dim); font-size: 12px; }
  main { display: grid; grid-template-columns: minmax(min(820px,100%),3fr) minmax(250px,1fr);
         gap: 16px; padding: 16px 20px 8px; align-items: start; }
  @media (max-width: 980px) { main { grid-template-columns: 1fr; } }
  .stage { border: 1px solid var(--line); border-radius: 4px; overflow: hidden;
           background: #070C15; }
  #floor { max-width: 100%; height: auto; display: block; margin: 0 auto;
           image-rendering: pixelated; }
  .roster { border: 1px solid var(--line); border-radius: 4px; background: var(--panel);
            max-height: 46vh; overflow-y: auto; }
  .feed { margin-top: 12px; border: 1px solid var(--line); border-radius: 4px;
          background: var(--panel); }
  .feed-head { padding: 7px 10px; font-size: 10px; letter-spacing: 0.1em;
               color: var(--faint); border-bottom: 1px solid var(--line); }
  #feed { max-height: 30vh; overflow-y: auto; }
  .line { display: grid; grid-template-columns: 56px 12px 1fr; gap: 7px;
          padding: 5px 10px; font-size: 11px; align-items: baseline;
          border-bottom: 1px solid rgba(255,255,255,0.03); }
  .line:last-child { border-bottom: 0; }
  .line time { color: var(--faint); font-variant-numeric: tabular-nums; }
  .line .dot { width: 6px; height: 6px; border-radius: 50%; align-self: center; }
  .line .txt { overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
               color: var(--dim); }
  .line .txt b { color: #E8ECF4; font-weight: 600; }
  .who { display: grid; grid-template-columns: 20px 1fr auto; gap: 9px; align-items: center;
         padding: 7px 10px; border-bottom: 1px solid rgba(255,255,255,0.04); }
  .who:last-child { border-bottom: 0; }
  .who > * { min-width: 0; }
  .who canvas { image-rendering: pixelated; width: 20px; height: 28px; }
  .name { font-weight: 600; font-size: 12px; overflow: hidden; text-overflow: ellipsis;
          white-space: nowrap; }
  .meta { font-size: 10.5px; color: var(--faint); overflow: hidden; text-overflow: ellipsis;
          white-space: nowrap; }
  .meta em { font-style: normal; color: var(--dim); }
  .count { font-size: 11px; color: var(--dim); font-variant-numeric: tabular-nums; }
  footer { padding: 6px 20px 26px; color: var(--faint); font-size: 11px; max-width: 92ch; }
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
  <div class="stage"><canvas id="floor"></canvas></div>
  <div>
    <div class="roster" id="roster"></div>
    <div class="feed">
      <div class="feed-head">ACTIVITY · every line is a record on disk</div>
      <div id="feed"></div>
    </div>
  </div>
</main>
<footer id="caveat"></footer>
<script>
'use strict';

// ---------------------------------------------------------------- constants
const TILE = 16;          // sprite pixels per world tile
const SW = 12, SH = 18;   // character sprite size, in sprite pixels
// Screen pixels per sprite pixel. Chosen from the available width and always a
// whole number: a fractional scale is what turns pixel art into mush.
let SCALE = 3;

// Tile codes from the server: 0 wall, 1 floor, 2 door, 3 desk.
const canvas = document.getElementById('floor');
const ctx = canvas.getContext('2d');
ctx.imageSmoothingEnabled = false;

let world = null, backdrop = null, actors = new Map(), tick = 0;

// ------------------------------------------------------------------ sprites
// Characters are drawn from rectangles rather than a pixel map: it is a
// fraction of the data for the same look, and the walk cycle becomes a matter
// of offsetting limbs by a pixel instead of storing four more frames.

function hash(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
  return Math.abs(h);
}

const SHIRTS = ['#3E6FB0','#4C8C6B','#A65B4C','#6C5BA6','#B0863E','#3E8FA6','#9A4C79','#5B7A3E'];
const HAIRS  = ['#2B2118','#3E2B1F','#1A1A22','#5A3A22','#7A6A55','#2A3140'];
const SKINS  = ['#E8C39E','#C98D62','#8D5A3B','#F0D6B8','#A96B44'];

function paletteFor(id, principal) {
  const h = hash(id);
  return {
    shirt: principal ? '#C8A951' : SHIRTS[h % SHIRTS.length],
    hair:  HAIRS[(h >> 3) % HAIRS.length],
    skin:  SKINS[(h >> 7) % SKINS.length],
    shoe:  '#20242E',
    pants: principal ? '#2B2F3C' : ['#2F3646','#3A3040','#2B3A34'][(h >> 11) % 3],
  };
}

function px(g, x, y, w, h, color) { g.fillStyle = color; g.fillRect(x, y, w, h); }

/**
 * One character frame, drawn into `g` at sprite-pixel scale 1.
 * dir: 'down' | 'up' | 'left' | 'right';  step: 0..3 walk phase; sit: seated.
 * act: the activity pose — typing raises the forearms, reading holds a page,
 * talking lifts one hand. Each pose is produced by a specific kind of record.
 */
function drawCharacter(g, pal, dir, step, sit, act) {
  const side = dir === 'left' || dir === 'right';
  const back = dir === 'up';
  const lift = (step === 1) ? 1 : (step === 3) ? 1 : 0;   // limb swing phase
  const bob  = (step === 1 || step === 3) ? 1 : 0;        // body bob while walking
  const top  = (sit ? 3 : 1) + bob;

  // legs (hidden when seated — the desk covers them)
  if (!sit) {
    const legY = top + 11;
    if (step === 1) {
      px(g, 3, legY, 3, 4, pal.pants); px(g, 7, legY, 3, 3, pal.pants);
      px(g, 3, legY + 4, 3, 1, pal.shoe); px(g, 7, legY + 3, 3, 1, pal.shoe);
    } else if (step === 3) {
      px(g, 3, legY, 3, 3, pal.pants); px(g, 7, legY, 3, 4, pal.pants);
      px(g, 3, legY + 3, 3, 1, pal.shoe); px(g, 7, legY + 4, 3, 1, pal.shoe);
    } else {
      px(g, 3, legY, 3, 4, pal.pants); px(g, 7, legY, 3, 4, pal.pants);
      px(g, 3, legY + 4, 3, 1, pal.shoe); px(g, 7, legY + 4, 3, 1, pal.shoe);
    }
  }

  // torso
  px(g, 2, top + 6, 8, 6, pal.shirt);

  // arms — the pose is the activity, and the activity came from a record
  const armY = top + 6 + lift;
  if (act === 'typing') {
    // forearms forward over the keys, alternating on the animation phase
    px(g, 1, armY + 1, 2, 3, pal.shirt); px(g, 9, armY + 1, 2, 3, pal.shirt);
    px(g, 1, armY + 4 + (step % 2), 2, 1, pal.skin);
    px(g, 9, armY + 4 + ((step + 1) % 2), 2, 1, pal.skin);
  } else if (act === 'reading') {
    px(g, 1, armY + 1, 1, 4, pal.shirt); px(g, 10, armY + 1, 1, 4, pal.shirt);
    px(g, 2, armY + 3, 8, 4, '#E8E2D2');            // a page, held up
    px(g, 3, armY + 4, 6, 1, '#9AA3B2');
    px(g, 3, armY + 6, 4, 1, '#9AA3B2');
  } else if (act === 'talking') {
    px(g, 1, armY, 1, 5, pal.shirt);
    px(g, 10, armY - 2, 1, 4, pal.shirt);           // one hand raised
    px(g, 10, armY - 3, 1, 1, pal.skin);
    px(g, 1, armY + 5, 1, 1, pal.skin);
  } else {
    px(g, 1, armY, 1, 5, pal.shirt);
    px(g, 10, armY + (lift ? -1 : 0), 1, 5, pal.shirt);
    px(g, 1, armY + 5, 1, 1, pal.skin);
    px(g, 10, armY + 5 + (lift ? -1 : 0), 1, 1, pal.skin);
  }

  // head
  px(g, 3, top, 6, 6, pal.skin);
  px(g, 2, top - 1, 8, 3, pal.hair);       // hair cap
  px(g, 2, top + 1, 1, 2, pal.hair);
  px(g, 9, top + 1, 1, 2, pal.hair);
  if (back) {
    px(g, 3, top + 1, 6, 3, pal.hair);     // back of the head: all hair
  } else if (side) {
    const ex = dir === 'right' ? 7 : 4;
    px(g, ex, top + 3, 1, 1, '#151A22');
    px(g, dir === 'right' ? 9 : 2, top + 3, 1, 1, pal.skin);  // nose
  } else {
    px(g, 4, top + 3, 1, 1, '#151A22');
    px(g, 7, top + 3, 1, 1, '#151A22');
  }
}

const spriteCache = new Map();
function sprite(id, principal, dir, step, sit, act) {
  const key = `${id}|${dir}|${step}|${sit ? 1 : 0}|${act}`;
  let found = spriteCache.get(key);
  if (found) return found;
  const c = document.createElement('canvas');
  c.width = SW; c.height = SH;
  const g = c.getContext('2d');
  g.imageSmoothingEnabled = false;
  drawCharacter(g, paletteFor(id, principal), dir, step, sit, act);
  spriteCache.set(key, c);
  return c;
}

// ------------------------------------------------------------------ backdrop
// The room grid never changes, so it is drawn once into an offscreen canvas
// and blitted each frame. Only characters are redrawn per frame.

function buildBackdrop() {
  const c = document.createElement('canvas');
  c.width = world.width * TILE; c.height = world.height * TILE;
  const g = c.getContext('2d');
  g.imageSmoothingEnabled = false;

  px(g, 0, 0, c.width, c.height, '#0A0F1A');

  for (let y = 0; y < world.height; y++) {
    for (let x = 0; x < world.width; x++) {
      const t = world.tiles[y][x], X = x * TILE, Y = y * TILE;
      if (t === 0) {
        px(g, X, Y, TILE, TILE, '#1D2739');
        px(g, X, Y, TILE, 2, '#2B3854');          // lit top edge
        px(g, X, Y + TILE - 2, TILE, 2, '#151D2C');
      } else if (t === 2) {
        px(g, X, Y, TILE, TILE, '#101828');
        px(g, X + 1, Y + 1, TILE - 2, TILE - 2, '#16203a');
        px(g, X, Y, 2, TILE, '#33405c'); px(g, X + TILE - 2, Y, 2, TILE, '#33405c');
      } else {
        // floorboards, with a faint checker so movement is legible
        px(g, X, Y, TILE, TILE, ((x + y) % 2) ? '#0E1524' : '#101828');
        px(g, X, Y + TILE - 1, TILE, 1, '#0B1120');
        if (t === 3) drawDesk(g, X, Y);
      }
    }
  }

  for (const room of world.rooms) {
    const X = room.x * TILE, Y = room.y * TILE;
    px(g, X + 2, Y + 2, TILE * 3 + 6, 9, '#C8A951');
    g.fillStyle = '#0B1220';
    g.font = '8px ui-monospace, monospace';
    g.fillText(room.code, X + 5, Y + 9);
    g.fillStyle = 'rgba(200,169,81,0.55)';
    g.fillText(room.korean, X + TILE * 3 + 12, Y + 9);
    drawPlant(g, X + TILE, Y + room.h * TILE - TILE * 1.6);
  }
  return c;
}

function drawDesk(g, X, Y) {
  px(g, X + 1, Y + 5, TILE - 2, 8, '#3A2E22');    // desktop
  px(g, X + 1, Y + 5, TILE - 2, 2, '#54412F');    // lit edge
  px(g, X + 2, Y + 13, 2, 3, '#241C15');          // legs
  px(g, X + TILE - 4, Y + 13, 2, 3, '#241C15');
  px(g, X + 4, Y + 1, 8, 5, '#1B2740');           // monitor
  px(g, X + 5, Y + 2, 6, 3, '#2E4E6B');           // screen
  px(g, X + 7, Y + 6, 2, 1, '#1B2740');           // stand
}

function drawPlant(g, X, Y) {
  px(g, X + 5, Y + 10, 6, 5, '#5A3A28');
  px(g, X + 4, Y + 4, 8, 6, '#2F6B47');
  px(g, X + 6, Y + 1, 4, 4, '#3C8659');
}

// -------------------------------------------------------------------- state

function fitScale() {
  const room = canvas.parentElement.clientWidth || 900;
  SCALE = Math.max(2, Math.min(4, Math.floor(room / (world.width * TILE))));
  canvas.width = world.width * TILE * SCALE;
  canvas.height = world.height * TILE * SCALE;
  canvas.style.width = canvas.width + 'px';
  ctx.imageSmoothingEnabled = false;
}

async function loadWorld() {
  world = await (await fetch('/api/world')).json();
  fitScale();
  backdrop = buildBackdrop();
  window.addEventListener('resize', fitScale);
}

async function poll() {
  try {
    const state = await (await fetch('/api/state')).json();
    const mode = document.getElementById('mode');
    mode.textContent = state.live ? 'LIVE' : 'REPLAY';
    mode.className = 'badge' + (state.live ? '' : ' replay');
    document.getElementById('stats').textContent =
      state.events + ' real events applied · ' + state.errands + ' errands · ' +
      state.actors.length + ' on the floor' +
      (state.live ? '' : ' · ' + state.remaining + ' left to replay');
    document.getElementById('caveat').innerHTML = (state.live ? '' :
      '<b>Replay of events already on disk — history, not live activity.</b><br>') +
      'Every walk between wings is a real tool call from a transcript. Drifting inside ' +
      'a wing, and every pixel of the animation, is decoration: nothing on disk records ' +
      'where anyone stands or what they look like.';

    for (const a of state.actors) {
      const existing = actors.get(a.id);
      if (existing) { existing.data = a; }
      else { actors.set(a.id, { x: a.fx, y: a.fy, data: a, phase: Math.random() * 4 }); }
    }
    for (const id of [...actors.keys()]) {
      if (!state.actors.some(a => a.id === id)) actors.delete(id);
    }
    drawRoster(state.actors);
    drawFeed(state.feed || []);
  } catch (err) { /* a missed poll leaves the floor a moment stale, not wrong */ }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

const TINT = {
  typing: '#E3C46B', reading: '#22D3EE', testing: '#4ADE80',
  shipping: '#A78BFA', talking: '#F0A868', reporting: '#93C5FD', working: '#8A93A8',
};

function drawFeed(items) {
  document.getElementById('feed').innerHTML = items.length ? items.map(i => `
    <div class="line">
      <time>${escapeHtml(i.clock)}</time>
      <span class="dot" style="background:${TINT[i.activity] || '#8A93A8'}"></span>
      <span class="txt"><b>${escapeHtml(i.actor)}</b> · ${escapeHtml(i.label)} · ${
        escapeHtml(i.detail)}</span>
    </div>`).join('')
    : '<div class="line"><span></span><span></span><span class="txt">' +
      'Nothing yet — the feed fills as records arrive.</span></div>';
}

function drawRoster(list) {
  const host = document.getElementById('roster');
  host.innerHTML = list.map(a => `
    <div class="who" data-id="${escapeHtml(a.id)}">
      <canvas width="${SW}" height="${SH}"></canvas>
      <span>
        <div class="name">${escapeHtml(a.name)}</div>
        <div class="meta"><em>${a.wing}</em> · ${
          a.real ? escapeHtml(a.activity_label) : a.state_label}${
          a.detail ? ' · ' + escapeHtml(a.detail) : ''}</div>
      </span>
      <span class="count">${a.events}</span>
    </div>`).join('');

  // Each row shows that agent's own character, so the roster and the floor
  // are recognisably the same people.
  host.querySelectorAll('.who').forEach((row, index) => {
    const a = list[index];
    const g = row.querySelector('canvas').getContext('2d');
    g.imageSmoothingEnabled = false;
    g.drawImage(sprite(a.id, a.principal, 'down', 0, false, 'idle'), 0, 0);
  });
}

// --------------------------------------------------------------------- draw

function draw() {
  requestAnimationFrame(draw);
  if (!world || !backdrop) return;
  tick++;

  ctx.setTransform(SCALE, 0, 0, SCALE, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(backdrop, 0, 0);

  // Draw back-to-front so a character lower on screen overlaps one above it.
  const ordered = [...actors.values()].sort((p, q) => p.y - q.y);

  for (const a of ordered) {
    // Ease toward the server's fractional position: the poll is 400ms, the
    // frame is ~16ms, so without this the walk would be a slideshow.
    a.x += (a.data.fx - a.x) * 0.28;
    a.y += (a.data.fy - a.y) * 0.28;

    const moving = a.data.moving;
    const act = moving ? 'walking' : a.data.activity;
    // Typing animates while standing still, so the phase advances for it too.
    if (moving || act === 'typing') a.phase = (a.phase + (moving ? 0.28 : 0.14)) % 4;
    const step = moving ? Math.floor(a.phase) : (act === 'typing' ? Math.floor(a.phase) : 0);
    const sit = a.data.at_desk && !moving;

    // Idle characters breathe. Without it a still office looks like a freeze,
    // which is a different (and wrong) claim from "nothing is happening".
    const bob = (!moving && Math.floor(tick / 34) % 2) ? 1 : 0;

    const sx = Math.round(a.x * TILE + (TILE - SW) / 2);
    // Seated actors are drawn a few pixels high so the desk reads as being in
    // front of them — they sit *at* the desk rather than on top of it.
    const sy = Math.round(a.y * TILE + TILE - SH + 2) - (sit ? 7 : 0) + bob;

    ctx.fillStyle = 'rgba(0,0,0,0.35)';
    ctx.fillRect(sx + 2, sy + SH - 1, SW - 4, 1);

    ctx.drawImage(
      sprite(a.data.id, a.data.principal, a.data.facing, step, sit, act), sx, sy);

    if (a.data.real) drawStatusBubble(ctx, sx, sy, a.data);
    // Nameplates only for people doing something: with everyone labelled, a
    // busy wing is a wall of overlapping text.
    if (a.data.real || a.data.principal) drawNameplate(ctx, sx, sy, a.data);
  }
}

// A bubble appears only while an actor is genuinely busy, and its icon is the
// activity the record produced — never idle chatter to fill the frame.
const ACTIVITY_TINT = {
  typing: '#E3C46B', reading: '#22D3EE', testing: '#4ADE80',
  shipping: '#A78BFA', talking: '#F0A868', reporting: '#93C5FD', working: '#8A93A8',
};

function drawStatusBubble(g, sx, sy, data) {
  const bx = sx + SW - 2, by = sy - 9;
  g.fillStyle = '#0B1220'; g.fillRect(bx, by, 11, 10);
  g.fillStyle = '#243149';
  g.fillRect(bx, by, 11, 1); g.fillRect(bx, by + 9, 11, 1);
  g.fillRect(bx, by, 1, 10); g.fillRect(bx + 10, by, 1, 10);
  g.fillRect(bx + 1, by + 10, 1, 1);            // bubble tail

  const act = data.activity || 'working';
  g.fillStyle = ACTIVITY_TINT[act] || '#8A93A8';
  const blink = Math.floor(tick / 10) % 2;

  if (act === 'typing') {                        // a pencil
    g.fillRect(bx + 3, by + 6, 5, 1);
    g.fillRect(bx + 4, by + 5, 4, 1); g.fillRect(bx + 6, by + 3, 2, 2);
  } else if (act === 'reading') {                // a magnifier
    g.fillRect(bx + 3, by + 2, 4, 1); g.fillRect(bx + 3, by + 5, 4, 1);
    g.fillRect(bx + 2, by + 3, 1, 2); g.fillRect(bx + 7, by + 3, 1, 2);
    g.fillRect(bx + 7, by + 6, 2, 2);
  } else if (act === 'testing') {                // a tick
    g.fillRect(bx + 3, by + 5, 1, 2); g.fillRect(bx + 4, by + 6, 1, 2);
    g.fillRect(bx + 5, by + 4, 1, 2); g.fillRect(bx + 6, by + 2, 1, 2);
  } else if (act === 'shipping') {               // an up arrow
    g.fillRect(bx + 5, by + 2, 1, 6); g.fillRect(bx + 4, by + 3, 3, 1);
    g.fillRect(bx + 3, by + 4, 5, 1);
  } else if (act === 'talking') {                // speech dots
    g.fillRect(bx + 3, by + 4, 1, 1);
    if (blink) { g.fillRect(bx + 5, by + 4, 1, 1); g.fillRect(bx + 7, by + 4, 1, 1); }
  } else if (act === 'reporting') {              // lines of text
    g.fillRect(bx + 3, by + 3, 6, 1); g.fillRect(bx + 3, by + 5, 6, 1);
    if (blink) g.fillRect(bx + 3, by + 7, 3, 1);
  } else {
    g.fillRect(bx + 4, by + 4, 3, 1);
    if (blink) g.fillRect(bx + 4, by + 6, 3, 1);
  }
}

function drawNameplate(g, sx, sy, data) {
  const label = data.name.length > 18 ? data.name.slice(0, 17) + '…' : data.name;
  g.font = '7px ui-monospace, monospace';
  const w = g.measureText(label).width;
  const x = Math.round(sx + SW / 2 - w / 2), y = sy - 10;
  g.fillStyle = 'rgba(11,18,32,0.72)';
  g.fillRect(x - 2, y - 6, w + 4, 8);
  g.fillStyle = data.principal ? '#E3C46B' : 'rgba(232,236,244,0.82)';
  g.fillText(label, x, y);
}

loadWorld().then(() => { poll(); setInterval(poll, 400); draw(); });
</script>
</body>
</html>
"""
