/* worklog — vanilla JS, no dependencies, no network calls off-box. */
'use strict';

const HOUR = 48;          // px per hour; must match --hour in style.css
const SNAP = 15;          // minutes
const RATING_WORDS = ['', 'Distracted', 'Scattered', 'Okay', 'Focused', 'Deep focus'];
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
                'September', 'October', 'November', 'December'];

const state = {
  scope: store('scope') || 'week',
  anchor: todayStr(),
  blocks: [],
  projects: {},           // name -> colour slot (1..4) or null
  overview: null,
  scrollTop: null,        // week grid scroll position, kept across re-renders
  editing: null,          // the block open in the editor (no id = new)
  rating: null,
};

/* ── dates + formatting ───────────────────────────────────────────────── */
function pad(n) { return String(n).padStart(2, '0'); }
function fmtISO(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
function parseISO(s) { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); }
function todayStr() { return fmtISO(new Date()); }
function addDays(iso, n) { const d = parseISO(iso); d.setDate(d.getDate() + n); return fmtISO(d); }
function mondayOf(iso) { return addDays(iso, -((parseISO(iso).getDay() + 6) % 7)); }
function nowMin() { const d = new Date(); return d.getHours() * 60 + d.getMinutes(); }

function fmtDur(min) {
  min = Math.max(0, Math.round(min || 0));
  const h = Math.floor(min / 60), m = min % 60;
  if (h && m) return h + 'h ' + pad(m) + 'm';
  return h ? h + 'h' : m + 'm';
}
function fmtT(min) {
  const h = Math.floor(min / 60) % 24, m = min % 60;
  return (h % 12 || 12) + (m ? ':' + pad(m) : '') + (h < 12 ? 'am' : 'pm');
}
function fmtRange(a, b) { return fmtT(a) + ' – ' + fmtT(b); }
function toHHMM(min) { return pad(Math.floor(min / 60) % 24) + ':' + pad(min % 60); }
function fromHHMM(s) { const [h, m] = s.split(':').map(Number); return h * 60 + m; }
function fmtDay(iso) {
  const d = parseISO(iso);
  return WEEKDAYS[(d.getDay() + 6) % 7] + ', ' + MONTHS[d.getMonth()].slice(0, 3) + ' ' + d.getDate();
}
function dur(b) { return b.end_min - b.start_min; }
function sum(list, fn) { return list.reduce((a, x) => a + fn(x), 0); }
function avgRating(blocks) {     // weighted by time: a rated hour counts 4x a rated 15 minutes
  const rated = blocks.filter(b => b.rating);
  return rated.length ? sum(rated, b => b.rating * dur(b)) / sum(rated, dur) : null;
}
function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem('worklog.' + key);
    localStorage.setItem('worklog.' + key, value);
  } catch (e) { return null; }
}

/* ── tiny DOM helpers ─────────────────────────────────────────────────── */
function H(tag, attrs, kids) {
  const n = document.createElement(tag);
  for (const k in (attrs || {})) {
    if (k === 'html') n.innerHTML = attrs[k];
    else if (k === 'text') n.textContent = attrs[k];
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), attrs[k]);
    else if (attrs[k] != null) n.setAttribute(k, attrs[k]);
  }
  for (const kid of (kids || [])) if (kid != null) n.append(kid);
  return n;
}
const $ = id => document.getElementById(id);

const tipEl = $('tip');
function tip(el, html) {
  const place = e => {
    const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
    tipEl.style.left = Math.min(e.clientX + 14, window.innerWidth - w - 8) + 'px';
    tipEl.style.top = (e.clientY + 16 + h > window.innerHeight ? e.clientY - h - 10 : e.clientY + 16) + 'px';
  };
  el.addEventListener('pointerenter', e => {
    if (gesture) return;
    tipEl.innerHTML = html(); tipEl.hidden = false; place(e);
  });
  el.addEventListener('pointermove', e => { if (!tipEl.hidden) place(e); });
  el.addEventListener('pointerleave', () => { tipEl.hidden = true; });
}

let toastTimer = null;
function toast(text, action, fn) {
  const t = $('toast');
  t.replaceChildren(H('span', { text }));
  if (action) t.append(H('button', { text: action, onclick: () => { t.hidden = true; fn(); } }));
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, action ? 7000 : 4000);
}

/* ── data ─────────────────────────────────────────────────────────────── */
async function api(method, path, body) {
  const res = await fetch(path, {
    method, headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || ('request failed (' + res.status + ')'));
  return data;
}

/* start/end bound the period the tiles summarise; the month grid also needs
   the days of neighbouring months that share its first and last weeks. */
function bounds() {
  if (state.scope === 'week') {
    const start = mondayOf(state.anchor), end = addDays(start, 6);
    return { start, end, fetchStart: start, fetchEnd: end };
  }
  const a = parseISO(state.anchor);
  const start = fmtISO(new Date(a.getFullYear(), a.getMonth(), 1));
  const end = fmtISO(new Date(a.getFullYear(), a.getMonth() + 1, 0));
  if (state.scope === 'log') return { start, end, fetchStart: start, fetchEnd: end };
  return { start, end, fetchStart: mondayOf(start), fetchEnd: addDays(mondayOf(end), 6) };
}

function rangeLabel(b) {
  const s = parseISO(b.start), e = parseISO(b.end);
  if (state.scope !== 'week') return MONTHS[s.getMonth()] + ' ' + s.getFullYear();
  const m = d => MONTHS[d.getMonth()].slice(0, 3);
  const left = m(s) + ' ' + s.getDate() + (s.getFullYear() !== e.getFullYear() ? ', ' + s.getFullYear() : '');
  const right = (s.getMonth() !== e.getMonth() ? m(e) + ' ' : '') + e.getDate() + ', ' + e.getFullYear();
  return left + ' – ' + right;
}

let loadSeq = 0;
async function load() {
  const b = bounds(), seq = ++loadSeq;
  try {
    const [r, o] = await Promise.all([
      api('GET', '/api/blocks?start=' + b.fetchStart + '&end=' + b.fetchEnd),
      api('GET', '/api/overview'),
    ]);
    if (seq !== loadSeq) return;        // a newer navigation already superseded this one
    state.blocks = r.blocks; state.projects = r.projects; state.overview = o;
    render();
  } catch (err) {
    toast('Could not load: ' + err.message);
  }
}

function color(project) {
  const slot = state.projects[project];
  return slot ? 'var(--s' + slot + ')' : 'var(--other)';
}
function projectName(p) { return p || 'No project'; }

/* ── render ───────────────────────────────────────────────────────────── */
function render() {
  const b = bounds();
  for (const t of document.querySelectorAll('#tabs button'))
    t.setAttribute('aria-selected', String(t.dataset.scope === state.scope));
  $('range-label').textContent = rangeLabel(b);
  tipEl.hidden = true;

  const inRange = state.blocks.filter(x => x.day >= b.start && x.day <= b.end);
  renderTiles(b, inRange);
  const view = $('view');
  view.replaceChildren();
  if (state.scope === 'week') renderWeek(view, b);
  else if (state.scope === 'month') renderMonth(view, b);
  else renderLog(view, b, inRange);
  if (state.scope !== 'log') renderProjects(view, inRange);
}

function tile(k, vHtml, m) {
  return H('div', { class: 'tile' }, [
    H('div', { class: 'k', text: k }), H('div', { class: 'v', html: vHtml }),
    typeof m === 'string' ? H('div', { class: 'm', text: m }) : m,
  ]);
}

function renderTiles(b, inRange) {
  const o = state.overview, today = todayStr();
  const total = sum(inRange, dur);
  const days = new Set(inRange.map(x => x.day)).size;
  const rating = avgRating(inRange);
  const rated = inRange.filter(x => x.rating).length;
  const current = today >= b.start && today <= b.end;
  const period = state.scope === 'week' ? (current ? 'This week' : 'Week of ' + fmtDay(b.start).slice(5))
                                        : (current ? 'This month' : rangeLabel(b));

  const goal = H('input', {
    type: 'number', min: '0.5', max: '24', step: '0.5', value: +(o.goal_min / 60).toFixed(2),
    'aria-label': 'Daily goal in hours',
    onchange: async e => {
      try { await api('POST', '/api/goal', { hours: Number(e.target.value) }); }
      catch (err) { toast(err.message); }
      load();
    },
  });
  const todayTile = tile('Today', esc(fmtDur(o.today_min)),
    H('div', { class: 'm' }, ['of a ', goal, ' h daily goal']));
  todayTile.append(H('div', { class: 'meter' }, [
    H('i', { style: 'width:' + Math.min(100, o.goal_min ? o.today_min / o.goal_min * 100 : 0) + '%' })]));

  $('tiles').replaceChildren(
    todayTile,
    tile(period, esc(fmtDur(total)),
         days ? days + (days === 1 ? ' day' : ' days') + ' worked · ' + fmtDur(total / days) + ' a day'
              : 'nothing logged yet'),
    tile('Average rating', rating == null ? '–' : rating.toFixed(1) + '<small>/ 5</small>',
         inRange.length ? rated + ' of ' + inRange.length + ' blocks rated' : 'rate a block from 1 to 5'),
    tile('Streak', o.streak + '<small>' + (o.streak === 1 ? 'day' : 'days') + '</small>',
         o.streak ? 'in a row with work logged' : 'log a block today to start one'),
  );
}

function blockTip(b) {
  return '<b>' + esc(b.title || 'Work') + '</b><br>' + esc(fmtRange(b.start_min, b.end_min)) +
    ' · ' + fmtDur(dur(b)) +
    (b.project ? '<br>' + esc(b.project) : '') +
    (b.rating ? '<br>★ ' + b.rating + ' · ' + RATING_WORDS[b.rating] : '') +
    (b.notes ? '<br>' + esc(b.notes.length > 180 ? b.notes.slice(0, 180) + '…' : b.notes) : '');
}

/* ── week ─────────────────────────────────────────────────────────────── */
function placeBlock(el, start, end) {
  const d = end - start;
  el.style.top = (start / 60 * HOUR) + 'px';
  el.style.height = Math.max(8, d / 60 * HOUR - 2) + 'px';
  el.classList.toggle('short', d <= 30);
  el.classList.toggle('tiny', d <= 15);
}

function blockEl(b) {
  const el = H('div', { class: 'blk', 'data-id': b.id, style: '--c:' + color(b.project) }, [
    H('div', { class: 'ti', text: b.title || b.project || 'Work' }),
    H('div', { class: 't', text: fmtRange(b.start_min, b.end_min) + (b.rating ? ' · ★ ' + b.rating : '') }),
    H('div', { class: 'rs' }),
  ]);
  placeBlock(el, b.start_min, b.end_min);
  tip(el, () => blockTip(b));
  return el;
}

function renderWeek(view, b) {
  const today = todayStr();
  const head = H('div', { class: 'cal-head' }, [H('div')]);
  const gut = H('div', { class: 'gut' });
  for (let h = 1; h < 24; h++)
    gut.append(H('span', { style: 'top:' + h * HOUR + 'px', text: (h % 12 || 12) + (h < 12 ? ' AM' : ' PM') }));
  const body = H('div', { class: 'cal-body' }, [gut]);

  for (let i = 0; i < 7; i++) {
    const day = addDays(b.start, i);
    const mine = state.blocks.filter(x => x.day === day);
    head.append(H('div', { class: 'dayhead' + (day === today ? ' today' : '') }, [
      H('span', { text: WEEKDAYS[i] }),
      H('span', { class: 'dnum', text: parseISO(day).getDate() }),
      H('span', { class: 'dtot', text: mine.length ? fmtDur(sum(mine, dur)) : '' }),
    ]));
    const col = H('div', { class: 'col' + (day === today ? ' today' : ''), 'data-day': day });
    for (const blk of mine) col.append(blockEl(blk));
    if (day === today) col.append(H('div', { class: 'now', style: 'top:' + nowMin() / 60 * HOUR + 'px' }));
    body.append(col);
  }
  body.addEventListener('pointerdown', onCalDown);

  const scroll = H('div', { class: 'cal-scroll' }, [head, body]);
  view.append(H('div', { class: 'card cal' }, [scroll]));
  if (state.scrollTop == null) {
    // Open on the working day, not on midnight.
    const first = Math.min(8 * 60, ...state.blocks.map(x => x.start_min));
    state.scrollTop = Math.max(0, first - 45) / 60 * HOUR;
  }
  scroll.scrollTop = state.scrollTop;
  scroll.addEventListener('scroll', () => { state.scrollTop = scroll.scrollTop; });
}

/* Pointer gestures on the week grid: drag on empty space to draw a block,
   drag a block to move it, drag its bottom edge to resize, click to edit. */
let gesture = false;
function minAt(col, clientY) {
  return Math.max(0, Math.min(1440, (clientY - col.getBoundingClientRect().top) / HOUR * 60));
}
function snap(m) { return Math.round(m / SNAP) * SNAP; }
function track(move, up) {
  gesture = true; tipEl.hidden = true;
  const done = e => {
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', done);
    window.removeEventListener('pointercancel', done);
    gesture = false;
    up(e);
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', done);
  window.addEventListener('pointercancel', done);
}

function onCalDown(e) {
  const col = e.target.closest('.col');
  if (e.button !== 0 || !col || gesture) return;
  e.preventDefault();
  const el = e.target.closest('.blk');
  if (el) dragBlock(e, el); else drawBlock(e, col);
}

function drawBlock(e, col) {
  const a = Math.min(1440 - SNAP, Math.floor(minAt(col, e.clientY) / SNAP) * SNAP);
  let s = a, en = a + SNAP, moved = false;
  const ghost = H('div', { class: 'blk ghost' }, [H('div', { class: 'ti' })]);
  const place = () => { placeBlock(ghost, s, en); ghost.firstChild.textContent = fmtRange(s, en); };
  place(); col.append(ghost);
  track(ev => {
    const m = snap(minAt(col, ev.clientY));
    if (m !== a) moved = true;
    s = Math.min(a, m); en = Math.max(a, m);
    if (en === s) en = s + SNAP;
    place();
  }, () => {
    if (!moved) { en = Math.min(1440, a + 60); place(); }   // a plain click: an hour
    openEditor({ day: col.dataset.day, start_min: s, end_min: en });
  });
}

function dragBlock(e, el) {
  const blk = state.blocks.find(x => String(x.id) === el.dataset.id);
  if (!blk) return;
  const resize = e.target.classList.contains('rs');
  const cols = Array.from(document.querySelectorAll('.cal-body .col'));
  const cur = { day: blk.day, start_min: blk.start_min, end_min: blk.end_min };
  let dragged = false;
  track(ev => {
    if (!dragged && Math.hypot(ev.clientX - e.clientX, ev.clientY - e.clientY) < 4) return;
    dragged = true; el.classList.add('dragging');
    if (resize) {
      cur.end_min = Math.max(cur.start_min + SNAP, snap(minAt(el.parentNode, ev.clientY)));
    } else {
      const len = dur(blk);
      const st = blk.start_min + snap((ev.clientY - e.clientY) / HOUR * 60);
      cur.start_min = Math.max(0, Math.min(1440 - len, st));
      cur.end_min = cur.start_min + len;
      const over = cols.find(c => { const r = c.getBoundingClientRect(); return ev.clientX >= r.left && ev.clientX < r.right; });
      if (over && over !== el.parentNode) over.append(el);
      cur.day = el.parentNode.dataset.day;
    }
    placeBlock(el, cur.start_min, cur.end_min);
    el.querySelector('.t').textContent = fmtRange(cur.start_min, cur.end_min);
  }, async () => {
    if (!dragged) return openEditor(blk);
    if (cur.day !== blk.day || cur.start_min !== blk.start_min || cur.end_min !== blk.end_min) {
      try { await api('PUT', '/api/blocks/' + blk.id, cur); }
      catch (err) { toast('Not moved: ' + err.message); }
    }
    load();
  });
}

/* ── month ────────────────────────────────────────────────────────────── */
function renderMonth(view, b) {
  const today = todayStr(), grid = H('div', { class: 'month' });
  for (const w of WEEKDAYS) grid.append(H('div', { class: 'wd', text: w }));
  const byDay = {};
  for (const x of state.blocks) (byDay[x.day] = byDay[x.day] || []).push(x);
  const scale = Math.max(state.overview.goal_min, ...Object.values(byDay).map(l => sum(l, dur)));

  for (let day = mondayOf(b.start); day <= addDays(mondayOf(b.end), 6); day = addDays(day, 1)) {
    const mine = byDay[day] || [], mins = sum(mine, dur), r = avgRating(mine);
    const cell = H('button', {
      class: 'mcell' + (day < b.start || day > b.end ? ' out' : '') + (day === today ? ' today' : ''),
      'aria-label': fmtDay(day) + (mins ? ', ' + fmtDur(mins) : ', nothing logged'),
      onclick: () => { state.scope = 'week'; state.anchor = day; store('scope', 'week'); load(); },
    }, [H('span', { class: 'n', text: parseISO(day).getDate() })]);
    if (mins) {
      cell.append(
        H('span', { class: 'h', html: esc(fmtDur(mins)) + (r == null ? '' : '<span class="r">★ ' + r.toFixed(1) + '</span>') }),
        H('span', { class: 'meter' }, [H('i', { style: 'width:' + mins / scale * 100 + '%' })]));
      tip(cell, () => '<b>' + esc(fmtDay(day)) + '</b><br>' + fmtDur(mins) + ' in ' + mine.length +
        (mine.length === 1 ? ' block' : ' blocks') + (r == null ? '' : '<br>★ ' + r.toFixed(1) + ' average') +
        '<br>' + mine.map(x => esc(fmtT(x.start_min) + ' ' + (x.title || x.project || 'Work'))).slice(0, 6).join('<br>') +
        (mine.length > 6 ? '<br>…' : ''));
    }
    grid.append(cell);
  }
  view.append(H('div', { class: 'card' }, [
    H('header', {}, [H('h2', { text: 'Hours per day' }),
                     H('span', { class: 'sub right', text: 'bar fills at ' + fmtDur(scale) + ' · click a day to open its week' })]),
    grid]));
}

/* ── by project ───────────────────────────────────────────────────────── */
function renderProjects(view, inRange) {
  if (!inRange.length) return;
  const totals = {};
  for (const x of inRange) totals[x.project] = (totals[x.project] || 0) + dur(x);
  const rows = Object.entries(totals).sort((a, c) => c[1] - a[1]);
  if (rows.length === 1 && rows[0][0] === '') return;      // nothing to break down
  const all = sum(rows, r => r[1]), max = rows[0][1];
  const card = H('div', { class: 'card' }, [H('header', {}, [
    H('h2', { text: 'By project' }),
    H('span', { class: 'sub', text: state.scope === 'week' ? 'this week' : 'this month' })])]);
  for (const [name, mins] of rows) {
    const mine = inRange.filter(x => x.project === name), r = avgRating(mine);
    const row = H('div', { class: 'prow', style: '--c:' + color(name) }, [
      H('div', { class: 'name' }, [H('i'), H('span', { text: projectName(name) })]),
      H('div', { class: 'bar' }, [H('i', { style: 'width:' + mins / max * 100 + '%' })]),
      H('div', { class: 'val', html: '<b>' + esc(fmtDur(mins)) + '</b> · ' + Math.round(mins / all * 100) + '%' }),
    ]);
    tip(row, () => '<b>' + esc(projectName(name)) + '</b><br>' + fmtDur(mins) + ' in ' + mine.length +
      (mine.length === 1 ? ' block' : ' blocks') + (r == null ? '' : '<br>★ ' + r.toFixed(1) + ' average'));
    card.append(row);
  }
  view.append(card);
}

/* ── log ──────────────────────────────────────────────────────────────── */
function renderLog(view, b, inRange) {
  const card = H('div', { class: 'card' }, [H('header', {}, [
    H('h2', { text: 'Every block' }),
    H('span', { class: 'sub', text: inRange.length + (inRange.length === 1 ? ' block' : ' blocks') }),
    H('span', { class: 'sub right' }, [
      H('a', { href: '/api/export.csv?start=' + b.start + '&end=' + b.end, text: 'This month as CSV' }), ' · ',
      H('a', { href: '/api/export.csv', text: 'everything' })]),
  ])]);
  if (!inRange.length) {
    card.append(H('div', { class: 'empty', text: 'Nothing logged this month.' }));
    return view.append(card);
  }
  const body = H('tbody');
  const rows = inRange.slice().sort((x, y) => y.day.localeCompare(x.day) || x.start_min - y.start_min);
  let last = null;
  for (const x of rows) {
    body.append(H('tr', { onclick: () => openEditor(x) }, [
      H('td', { class: 'dim', text: x.day === last ? '' : fmtDay(x.day) }),
      H('td', { text: fmtRange(x.start_min, x.end_min) }),
      H('td', { class: 'num', text: fmtDur(dur(x)) }),
      H('td', { text: x.title || 'Work' }),
      H('td', { class: 'dim' }, x.project ? [H('i', { class: 'swatch', style: '--c:' + color(x.project) }), x.project] : []),
      H('td', { class: 'dim', text: x.rating ? '★ ' + x.rating : '' }),
      H('td', { class: 'wrap-ok', text: x.notes }),
    ]));
    last = x.day;
  }
  card.append(H('div', { class: 'tablewrap' }, [H('table', {}, [
    H('thead', {}, [H('tr', {}, [
      H('th', { text: 'Day' }), H('th', { text: 'Time' }), H('th', { class: 'num', text: 'Length' }),
      H('th', { text: 'What' }), H('th', { text: 'Project' }), H('th', { text: 'Rating' }), H('th', { text: 'Notes' })])]),
    body])]));
  view.append(card);
}

/* ── editor ───────────────────────────────────────────────────────────── */
const dlg = $('editor');

function setRating(r) {
  state.rating = r;
  for (const btn of $('f-rating').children)
    btn.setAttribute('aria-pressed', String(Number(btn.textContent) === r));
  $('f-rating-text').textContent = r ? RATING_WORDS[r] : 'not rated';
}

function formTimes() {
  const s = $('f-start').value, e = $('f-end').value;
  if (!s || !e) return null;
  const start = fromHHMM(s);
  let end = fromHHMM(e);
  if (end === 0) end = 1440;               // an end of 12:00am means midnight tonight
  return { start, end };
}
function showDuration() {
  const t = formTimes();
  $('f-dur').textContent = t && t.end > t.start ? fmtDur(t.end - t.start) : '';
}

function openEditor(b) {
  state.editing = b;
  $('ed-heading').textContent = b.id ? 'Edit block' : 'New block';
  $('f-title').value = b.title || '';
  $('f-day').value = b.day;
  $('f-start').value = toHHMM(b.start_min);
  $('f-end').value = toHHMM(b.end_min);
  $('f-project').value = b.project || '';
  $('f-notes').value = b.notes || '';
  $('f-delete').hidden = !b.id;
  $('f-err').hidden = true;
  $('projects').replaceChildren(...Object.keys(state.projects).filter(Boolean).sort()
    .map(p => H('option', { value: p })));
  setRating(b.rating || null);
  showDuration();
  tipEl.hidden = true;
  dlg.showModal();
  $('f-title').focus();
}

async function saveEditor(ev) {
  ev.preventDefault();
  const t = formTimes(), b = state.editing;
  const body = {
    day: $('f-day').value, start_min: t && t.start, end_min: t && t.end,
    title: $('f-title').value, project: $('f-project').value,
    rating: state.rating, notes: $('f-notes').value,
  };
  try {
    const saved = b.id ? await api('PUT', '/api/blocks/' + b.id, body)
                       : await api('POST', '/api/blocks', body);
    const v = bounds();
    if (saved.day < v.start || saved.day > v.end) state.anchor = saved.day;   // follow the block
    dlg.close();
  } catch (err) {
    $('f-err').textContent = err.message.charAt(0).toUpperCase() + err.message.slice(1) + '.';
    $('f-err').hidden = false;
  }
}

async function deleteEditing() {
  const b = state.editing;
  try { await api('DELETE', '/api/blocks/' + b.id); }
  catch (err) { $('f-err').textContent = err.message; $('f-err').hidden = false; return; }
  dlg.close();
  toast('Block deleted', 'Undo', async () => {
    try { await api('POST', '/api/blocks', b); } catch (err) { toast('Could not restore: ' + err.message); }
    load();
  });
}

function addNow() {
  const today = todayStr(), view = bounds();
  if (state.scope === 'week' && (today < view.start || today > view.end))
    return openEditor({ day: view.start, start_min: 9 * 60, end_min: 10 * 60 });
  const end = Math.max(SNAP, Math.floor(nowMin() / SNAP) * SNAP);
  openEditor({ day: today, start_min: Math.max(0, end - 60), end_min: end });
}

/* ── wiring ───────────────────────────────────────────────────────────── */
function go(dir) {
  if (state.scope === 'week') state.anchor = addDays(state.anchor, 7 * dir);
  else { const d = parseISO(state.anchor); state.anchor = fmtISO(new Date(d.getFullYear(), d.getMonth() + dir, 1)); }
  load();
}
function goToday() { state.anchor = todayStr(); state.scrollTop = null; load(); }

function init() {
  const saved = store('theme');
  if (saved) document.documentElement.dataset.theme = saved;
  $('theme').addEventListener('click', () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === 'dark'
      : matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.dataset.theme = dark ? 'light' : 'dark';
    store('theme', dark ? 'light' : 'dark');
  });

  $('tabs').addEventListener('click', e => {
    const t = e.target.closest('button[data-scope]');
    if (!t) return;
    state.scope = t.dataset.scope; store('scope', state.scope); load();
  });
  $('prev').addEventListener('click', () => go(-1));
  $('next').addEventListener('click', () => go(1));
  $('today').addEventListener('click', goToday);
  $('add').addEventListener('click', addNow);

  for (let r = 1; r <= 5; r++)
    $('f-rating').append(H('button', {
      type: 'button', text: r, title: RATING_WORDS[r], 'aria-pressed': 'false',
      onclick: () => setRating(state.rating === r ? null : r),     // click again to clear
    }));
  $('f-start').addEventListener('input', showDuration);
  $('f-end').addEventListener('input', showDuration);
  $('form').addEventListener('submit', saveEditor);
  $('f-cancel').addEventListener('click', () => dlg.close());
  $('f-delete').addEventListener('click', deleteEditing);
  dlg.addEventListener('mousedown', e => {       // a press on the backdrop, not on the padding
    const r = dlg.getBoundingClientRect();
    if (e.target === dlg && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom))
      dlg.close();
  });
  dlg.addEventListener('close', () => { state.editing = null; load(); });

  document.addEventListener('keydown', e => {
    if (dlg.open || e.metaKey || e.ctrlKey || e.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
    if (e.key === 'ArrowLeft') go(-1);
    else if (e.key === 'ArrowRight') go(1);
    else if (e.key === 't') goToday();
    else if (e.key === 'n') { e.preventDefault(); addNow(); }
  });

  // Keep the now-line and "today" honest in a tab left open for days.
  setInterval(() => { if (!dlg.open && !gesture && document.visibilityState === 'visible') load(); }, 60000);
  load();
}
init();
