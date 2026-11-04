import { MathfieldElement, convertLatexToMathMl, convertLatexToMarkup } from '/vendor/mathlive/mathlive.min.mjs';
import { mmlToLatex } from '/mml2tex.js';

MathfieldElement.fontsDirectory = '/vendor/mathlive/fonts';
MathfieldElement.soundsDirectory = null;

const $ = s => document.querySelector(s);
const view = $('#view');
const store = {
  get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};

function toast(msg, ms = 3000) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), ms);
}

async function api(path, opts = {}) {
  const r = await fetch('/api' + path, {
    credentials: 'same-origin', ...opts,
    headers: { 'content-type': 'application/json', ...(opts.headers || {}) },
  });
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && path !== '/login') { showLogin(); throw new Error('Log in first'); }
  if (!r.ok) throw new Error(data.detail || data.error || r.statusText);
  return data;
}

function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`); }

function setBar(title, subtitle = '', back = false) {
  $('#title').textContent = title; $('#subtitle').textContent = subtitle;
  $('#back').hidden = !back; document.title = title === 'AssignPad' ? title : `${title} · AssignPad`;
}

async function checkSession() {
  try {
    const s = await api('/status');
    $('#session').className = 'dot ' + (s.loggedIn ? 'ok' : 'bad');
    $('#session').title = s.loggedIn ? 'WebAssign session OK' : 'WebAssign session expired';
    const b = $('#banner');
    b.hidden = s.loggedIn;
    if (s.loggedIn) return;
    b.innerHTML = `<strong>Not connected to WebAssign.</strong> <span>${esc(s.error)}</span>
      <div class="banner-actions">
        ${s.passwordLogin ? '<button class="btn primary" id="walogin">Log in to WebAssign now</button>' : ''}
        ${s.hasScreenshot ? '<a class="btn" href="/api/login-error.png" target="_blank">Last login screenshot</a>' : ''}
      </div>
      <small>${s.passwordLogin ? '' : 'Send your session with the AssignPad Bridge extension, or set CENGAGE_EMAIL and CENGAGE_PASSWORD on the server.'}</small>`;
    const btn = $('#walogin');
    if (btn) btn.onclick = async () => {
      btn.disabled = true; btn.textContent = 'Logging in… (up to 1 min)';
      try {
        const r = await api('/wa-login', { method: 'POST', body: '{}' });
        toast(r.loggedIn ? 'Connected to WebAssign' : r.error, 8000);
      } catch (e) { toast(e.message, 8000); }
      await checkSession();
      if ($('#session').classList.contains('ok')) route(true);
    };
  } catch {}
}

// ---------- Login ----------
function showLogin() {
  setBar('AssignPad');
  view.innerHTML = `<form class="login"><h2>AssignPad</h2>
    <label>Password<br><input name="t" type="password" autocomplete="current-password" required style="width:100%"></label>
    <button class="btn primary">Log in</button></form>`;
  view.querySelector('form').onsubmit = async e => {
    e.preventDefault();
    try {
      await api('/login', { method: 'POST', body: JSON.stringify({ password: e.target.t.value }) });
      route();
    } catch (err) { toast(err.message); }
  };
}

// ---------- Time zone ----------
// '' = this device's zone. The server sends due times as Unix time (dueTs).
const deviceTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
const tz = () => store.get('tz') || deviceTz;
function fmtDue(a) {
  if (!a?.dueTs) return a?.due || '';
  try {
    return new Intl.DateTimeFormat(undefined, { timeZone: tz(), weekday: 'short', month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }).format(new Date(a.dueTs * 1000));
  } catch { return a.due || ''; }
}
function tzPicker() {
  const zones = Intl.supportedValuesOf ? Intl.supportedValuesOf('timeZone') : [deviceTz];
  const cur = store.get('tz') || '';
  return `<label class="note tz">Time zone <select id="tz">
    <option value="">This device (${esc(deviceTz)})</option>
    ${zones.map(z => `<option value="${esc(z)}"${z === cur ? ' selected' : ''}>${esc(z.replace(/_/g, ' '))}</option>`).join('')}
  </select></label>`;
}

// ---------- Assignment list ----------
function renderList(data) {
  setBar(data.course || 'AssignPad', data.synced ? 'Synced ' + new Date(data.synced * 1000).toLocaleString() : '');
  const rows = data.assignments || [];
  const sec = (label, items) => items.length ? `<h2 class="section">${label}</h2><div class="list">${items.map(a =>
    `<a class="row" href="#/a/${a.dep}"><span class="name">${esc(a.name)}</span>
      <span class="meta">${esc(fmtDue(a))}${a.score ? '<br>' + esc(a.score) : ''}</span></a>`).join('')}</div>` : '';
  view.innerHTML = rows.length
    ? sec('Current', rows.filter(a => a.group === 'current')) + sec('Past', rows.filter(a => a.group !== 'current'))
    : '<p class="empty">No assignments yet. Press sync.</p>';
  view.insertAdjacentHTML('afterbegin', '<div class="card ext" id="ext-auto"></div>');
  view.insertAdjacentHTML('beforeend', tzPicker() + `<p class="note">AssignPad ${VERSION}</p>`);
  view.querySelector('#tz').onchange = e => { store.set('tz', e.target.value); renderList(data); };
  renderAutoExt(data);
}

// ---------- Extensions ----------
// Auto extension: X hours after the due date, request WebAssign's automatic extension
// for assignments that are not 100%. One default, with an override per assignment.
async function renderAutoExt(list) {
  const box = $('#ext-auto');
  if (!box) return;
  let s;
  try { s = await api('/extensions'); } catch { box.remove(); return; }
  const names = Object.fromEntries((list.assignments || []).map(a => [a.dep, a.name]));
  const log = Object.entries(s.log).sort((a, b) => b[1].at - a[1].at).slice(0, 3);
  box.innerHTML = `<div class="body ext-row">
      <label><input type="checkbox" id="ext-on" ${s.auto.enabled ? 'checked' : ''}> Auto extension</label>
      <label><input type="number" id="ext-hours" min="0" max="240" step="1" value="${s.auto.hours}"> hours after due</label>
      <span class="muted small">Only for assignments under 100%, and only WebAssign's automatic extension.</span>
      ${log.map(([dep, l]) => `<span class="small ${l.ok ? 'ok' : 'bad'}">${l.ok ? '✓' : '✗'} ${esc(names[dep] || dep)}:
        ${esc(l.message)} (${new Date(l.at * 1000).toLocaleString()})</span>`).join('')}
    </div>`;
  const save = async () => {
    try {
      await api('/extensions', { method: 'PUT', body: JSON.stringify({ enabled: $('#ext-on').checked, hours: +$('#ext-hours').value || 0 }) });
      toast($('#ext-on').checked ? `Auto extension on: ${+$('#ext-hours').value || 0} h after due` : 'Auto extension off');
    } catch (e) { toast(e.message); }
  };
  $('#ext-on').onchange = save;
  $('#ext-hours').onchange = save;
}

async function renderExtension(dep, box) {
  let d;
  try { d = await api(`/assignments/${dep}/extension`); } catch (e) { box.remove(); return; }
  const i = d.info;
  const auto = i && ['automated', 'both'].includes(i.extension);
  const left = i && (i.unlimited_requests ? 99 : +i.requests_remaining || 0);
  const ov = d.override || {};
  const mode = ov.enabled === true ? 'on' : ov.enabled === false ? 'off' : 'default';
  const penalty = i && i.points_deducted ? ` WebAssign takes off ${esc(i.points_deducted)}${i.penalty_method === 'del' ? '%' : ' points'} for an extension.` : '';
  box.innerHTML = `<div class="body ext-row">
      <strong>Extension</strong>
      <span class="small">${!i ? esc(d.error || 'Not available') : !i.available ? 'WebAssign has no extensions for this assignment.'
        : esc(i.extension_message || '') + penalty + ` Requests left: ${i.unlimited_requests ? 'unlimited' : left}.`}</span>
      ${d.last ? `<span class="small ${d.last.ok ? 'ok' : 'bad'}">${d.last.ok ? '✓' : '✗'} ${esc(d.last.message)}
        (${new Date(d.last.at * 1000).toLocaleString()})</span>` : ''}
      <span class="ext-ctl">
        ${i && i.available && auto && left > 0 ? '<button class="btn primary" id="ext-req">Request extension</button>'
          : i && i.available && !auto ? '<span class="muted small">Only instructor-approved extensions: not used.</span>' : ''}
        <label>Auto <select id="ext-mode">
          <option value="default">Default (${d.auto.enabled ? 'on' : 'off'})</option>
          <option value="on">On</option><option value="off">Off</option></select></label>
        <label><input type="number" id="ext-h" min="0" max="240" step="1" value="${d.hours}"> h after due</label>
      </span>
    </div>`;
  box.querySelector('#ext-mode').value = mode;
  const save = async () => {
    const m = box.querySelector('#ext-mode').value, h = +box.querySelector('#ext-h').value;
    const body = m === 'default' && h === d.auto.hours ? { clear: true } : { enabled: m === 'default' ? null : m === 'on', hours: h };
    try { await api(`/assignments/${dep}/extension`, { method: 'PUT', body: JSON.stringify(body) }); toast('Saved'); }
    catch (e) { toast(e.message); }
  };
  box.querySelector('#ext-mode').onchange = save;
  box.querySelector('#ext-h').onchange = save;
  const req = box.querySelector('#ext-req');
  if (req) req.onclick = async () => {
    if (!confirm(`Request the automatic extension for this assignment now?${penalty ? '\n' + penalty.trim() : ''}`)) return;
    req.disabled = true; req.textContent = 'Requesting…';
    try {
      await api(`/assignments/${dep}/extension`, { method: 'POST', body: '{}' });
      toast('Extension granted', 5000);
      store.set('list', null);
    } catch (e) { toast(e.message, 8000); }
    renderExtension(dep, box);
  };
}

async function showList(refresh = false) {
  const cached = store.get('list');
  if (cached) renderList(cached); else view.innerHTML = '<p class="note">Loading…</p>';
  if (cached && !refresh) refresh = Date.now() / 1000 - (cached.synced || 0) > 600;
  try {
    const data = await api('/assignments' + (refresh ? '?refresh=1' : ''));
    store.set('list', data);
    if (location.hash === '' || location.hash === '#/') renderList(data);
  } catch (e) { toast(e.message); }
}

// ---------- Assignment ----------
const VERSION = 'v16';  // keep equal to SHELL in sw.js
const draftKey = (dep, name) => `draft:${dep}:${name}`;
let lastInput = 0;
const MML = 'http://www.w3.org/1998/Math/MathML';

function latexToMathml(latex) {
  const tpl = document.createElement('template');
  tpl.innerHTML = `<math xmlns="http://www.w3.org/1998/Math/MathML">${convertLatexToMathMl(latex)}</math>`;
  const m = tpl.content.firstElementChild;
  // MathType does not send invisible operators. Remove them.
  m.querySelectorAll('mo').forEach(o => {
    if (/^[⁡⁢⁣]$/.test(o.textContent)) o.remove();
    // MathType writes function names (sin, ln, ...) as <mi>.
    else if (/^[a-z]{2,}$/i.test(o.textContent)) {
      const mi = document.createElementNS(MML, 'mi'); mi.textContent = o.textContent; o.replaceWith(mi);
    }
  });
  return new XMLSerializer().serializeToString(m).replace(/ xmlns="http:\/\/www.w3.org\/1999\/xhtml"/g, '');
}

function qStyles(styles) {
  return styles.map(h => `<link rel="stylesheet" href="${esc(h)}">`).join('') +
    `<link rel="stylesheet" href="/vendor/mathlive/mathlive-static.css">
    <style>
      :host { display: block; color: var(--text); }
      .ap-box { display: inline-flex; align-items: center; gap: 4px; vertical-align: middle; margin: 2px 0; cursor: text;
        white-space: normal; }
      /* Keep "( [answer] )" on one line. A long answer scrolls inside its box. */
      .watexline:has(.ap-box), .watexinlineblock:has(.ap-box) { white-space: nowrap; }
      .ap-box math-field { max-width: min(65vw, 560px); }
      @media (max-width: 600px) { .ap-box math-field { max-width: 42vw; min-width: 110px; } }
      /* WebAssign draws fraction bars, roots and borders in black: use the text color (dark mode). */
      .q [class*="watex"] { border-color: currentColor !important; }
      /* WebAssign CSS sets black text on some parts. Use one text color; keep the author's inline colors. */
      .q *:not([style*="color"]):not(font[color]):not(.ap-mark) { color: inherit !important; }
      .q { color: var(--text) !important; }
      .ap-lock { font-size: 12px; color: var(--muted); }
      math-field { min-width: 140px; font-size: 1.15em; padding: 2px 6px; border-radius: 6px;
        border: 1px solid var(--line); background: var(--card); color: var(--text);
        --caret-color: var(--accent); --selection-background-color: color-mix(in srgb, var(--accent) 25%, transparent);
        --selection-color: var(--text);
        /* Highlight of the group (parens, fraction) that holds the caret. The default is near-white. */
        --contains-highlight-background-color: color-mix(in srgb, var(--accent) 14%, transparent);
        --highlight-text: color-mix(in srgb, var(--accent) 14%, transparent); }
      math-field:focus-within { outline: 2px solid var(--accent); outline-offset: 1px; }
      math-field[readonly] { opacity: .7; border-style: dashed; cursor: not-allowed; }
      math-field::part(menu-toggle) { display: none; }
      math-field::part(container) { cursor: text; }
      .ap-mx { border-collapse: separate; border-spacing: 4px; border-left: 2px solid currentColor;
        border-right: 2px solid currentColor; border-radius: 6px; display: inline-table; vertical-align: middle; }
      .ap-cell { width: 5.5em; }
      .ap-mx-btns { display: inline-flex; flex-direction: column; gap: 2px; vertical-align: middle; margin-left: 4px; }
      .ap-mx-btns button { font-size: 11px; padding: 1px 6px; border-radius: 4px; border: 1px solid var(--line);
        background: var(--card); color: var(--text); cursor: pointer; }
      input.ap-text { font: inherit; padding: 4px 8px; border-radius: 6px; border: 1px solid var(--line);
        background: var(--card); color: var(--text); }
      .ap-graph { display: inline-block; padding: 6px 10px; border: 1px dashed var(--line); border-radius: 8px; color: var(--accent); text-decoration: none; }
      .ap-mark { font-weight: 700; } .ap-mark.correct { color: var(--ok); } .ap-mark.incorrect { color: var(--bad); }
      img { max-width: 100%; height: auto; }
      /* The app shows its own ✓/✗ next to each answer box. Hide WebAssign's marks. */
      .waMark, .padMark, .waMarkWrap, img[src*="mCorrect"], img[src*="mIncorrect"] { display: none !important; }
      table { color: inherit; }
      .questionDetail, .qUtility .preContent { display: none; }
      @media (prefers-color-scheme: dark) { img:not([src*=".svg"]) { filter: invert(.9) hue-rotate(180deg); } }
    </style>`;
}

// WebAssign writes black text inline (style="color:black", <font color>). Use the theme text
// color for black, white and near-gray colors; keep real colors (red, blue hints).
const colorCtx = document.createElement('canvas').getContext('2d');
function fixInlineColors(root) {
  root.querySelectorAll('[style*="color"], font[color]').forEach(el => {
    const v = el.style.color || el.getAttribute('color');
    if (!v) return;
    colorCtx.fillStyle = '#123456'; colorCtx.fillStyle = v;
    const m = /^#(..)(..)(..)$/.exec(colorCtx.fillStyle);
    if (!m) return;
    const [r, g, b] = m.slice(1).map(h => parseInt(h, 16));
    if (Math.max(r, g, b) - Math.min(r, g, b) < 40) {  // no hue: black, white or gray
      el.style.color = ''; el.removeAttribute('color');
    }
  });
}

// WebAssign matrix answer: "[a,b;c,d]" (',' between columns, ';' between rows), "" when empty.
function matrixEditor(value, rows, cols, readonly, onChange) {
  let cells = String(value || '').replace(/[\[\]\s]/g, '')
    ? value.replace(/[\[\]]/g, '').split(';').map(r => r.split(',').map(c => c.trim())) : [];
  rows = cells.length || rows || 2; cols = Math.max(0, ...cells.map(r => r.length)) || cols || 2;
  const el = document.createElement('span');
  el.className = 'ap-matrix';
  const read = () => [...el.querySelectorAll('tr')].map(tr => [...tr.querySelectorAll('input')].map(i => i.value));
  const value_ = () => {
    const c = read();
    return c.flat().some(v => /\w/.test(v)) ? '[' + c.map(r => r.map(v => v.replace(/[,;\[\]]/g, '')).join(',')).join(';') + ']' : '';
  };
  const draw = () => {
    el.innerHTML = `<table class="ap-mx">${Array.from({ length: rows }, (_, r) => `<tr>${Array.from({ length: cols }, (_, c) =>
      `<td><input class="ap-text ap-cell" inputmode="decimal" aria-label="row ${r + 1} column ${c + 1}"
        value="${esc(cells[r]?.[c] ?? '')}" ${readonly ? 'readonly' : ''}></td>`).join('')}</tr>`).join('')}</table>
      ${readonly ? '' : `<span class="ap-mx-btns">
        <button type="button" data-d="0,1" title="Add row">+row</button><button type="button" data-d="0,-1" title="Remove row">−row</button>
        <button type="button" data-d="1,0" title="Add column">+col</button><button type="button" data-d="-1,0" title="Remove column">−col</button></span>`}`;
  };
  draw();
  el.addEventListener('input', () => onChange(value_()));
  el.addEventListener('click', e => {
    const b = e.target.closest('button[data-d]');
    if (!b) return;
    const [dc, dr] = b.dataset.d.split(',').map(Number);
    cells = read();
    rows = Math.max(1, rows + dr); cols = Math.max(1, cols + dc);
    draw(); onChange(value_());
  });
  return { el, get value() { return value_(); } };
}

function buildQuestion(asg, q, practice = null) {
  const card = document.createElement('section');
  card.className = practice ? 'card practice' : 'card'; card.id = practice ? 'practice-' + practice.of.box : q.box;
  // Practice versions are not saved as drafts: each one has new numbers.
  const draft = practice ? { get: () => null, set() {} } : { get: n => store.get(draftKey(asg.dep, n)), set: (n, v) => store.set(draftKey(asg.dep, n), v) };
  const pts = (q.details.Points || []).slice(-1)[0] || '';
  const allOk = q.parts.length && q.parts.every(p => p.mark === 'correct');
  const anyBad = q.parts.some(p => p.mark === 'incorrect');
  card.innerHTML = `<header><span class="num">${practice ? 'Practice ' + practice.of.number : q.number + '.'}</span>
      <span class="info">${practice ? 'Not graded' : (pts ? esc(pts) + ' pts' : '') + (q.submissions ? ' · submissions ' + esc(q.submissions) : '')}</span>
      ${allOk ? '<span class="badge ok">Correct</span>' : anyBad ? '<span class="badge bad">Incorrect</span>' : ''}
    </header><div class="body"></div>
    <footer>${practice ? `<button class="btn" data-pav>New version</button><button class="btn" data-close>Close</button>
        <button class="btn primary" data-check>Check answer</button>` :
      `<button class="btn" data-pav>Practice another</button>
      ${q.canSubmit ? '<button class="btn" data-act="save">Save</button><button class="btn primary" data-act="submit">Submit</button>' : ''}
      <a class="btn" href="${esc(asg.url)}#${esc(q.box)}" target="_blank" rel="noopener">Open in WebAssign</a>`}</footer>`;
  const host = card.querySelector('.body');
  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = qStyles(asg.styles || []) + `<div class="q qContent">${q.html}</div>`;
  fixInlineColors(root);
  root.querySelectorAll('.ap-tex').forEach(el => {
    try { el.innerHTML = convertLatexToMarkup(el.textContent); } catch {}
  });

  const fields = {};
  for (const p of q.parts) {
    if (p.kind === 'graph') {
      // WebAssign's graphing tool cannot run here: answer it on WebAssign.
      const slot = root.querySelector(`.ap-box[data-name="${p.name}"]`);
      if (slot) {
        slot.insertAdjacentHTML('beforeend', `<a class="ap-graph" href="${esc(asg.url)}#${esc(q.box)}" target="_blank" rel="noopener">📈 Graph answer: open in WebAssign</a>`);
        if (p.mark) slot.insertAdjacentHTML('beforeend', `<span class="ap-mark ${p.mark}" title="${p.mark}">${p.mark === 'correct' ? '✓' : '✗'}</span>`);
      }
      fields[p.name] = { part: p };
    } else if (p.kind === 'matrix') {
      const slot = root.querySelector(`.ap-box[data-name="${p.name}"]`);
      if (!slot) continue;
      const m = matrixEditor(draft.get(p.name) ?? p.value, p.rows, p.cols, !p.enabled,
        v => { lastInput = Date.now(); draft.set(p.name, v); });
      slot.append(m.el);
      if (!p.enabled) slot.insertAdjacentHTML('beforeend', '<span class="ap-lock">🔒 locked</span>');
      if (p.mark) slot.insertAdjacentHTML('beforeend',
        `<span class="ap-mark ${p.mark}" title="${p.mark}">${p.mark === 'correct' ? '✓' : '✗'}</span>`);
      fields[p.name] = { part: p, el: m };
    } else if (p.kind === 'math' || p.kind === 'text') {
      const slot = root.querySelector(`.ap-box[data-name="${p.name}"]`);
      if (!slot) continue;
      const saved = draft.get(p.name);
      let el;
      if (p.kind === 'math') {
        el = new MathfieldElement();
        el.mathVirtualKeyboardPolicy = 'auto';
        el.smartFence = true;
        el.value = saved ?? mmlToLatex(p.value);
        el.addEventListener('input', () => draft.set(p.name, el.value));
      } else {
        el = document.createElement('input');
        el.className = 'ap-text';
        el.value = saved ?? p.value ?? '';
        el.addEventListener('input', () => draft.set(p.name, el.value));
      }
      el.addEventListener('input', () => { lastInput = Date.now(); });
      slot.append(el);
      if (!p.enabled) {
        el.readOnly = true;
        el.title = 'Locked by WebAssign (already correct or no submissions left)';
        slot.insertAdjacentHTML('beforeend', '<span class="ap-lock">🔒 locked</span>');
      } else {
        // MathLive bug: after a click on the empty padding of the field, typed characters
        // are dropped (arrow keys still work). We handle padding clicks ourselves: focus by
        // code and put the caret at the end. Clicks on the formula still go to MathLive.
        slot.addEventListener('pointerdown', e => {
          const t = e.composedPath()[0];
          const cls = typeof t.className === 'string' ? t.className : '';
          if (t === slot || t === el || /\bML__(container|content)\b/.test(cls)) {
            e.preventDefault(); e.stopPropagation();
            el.focus();
            el.executeCommand?.('moveToMathfieldEnd');
          }
        }, true);
      }
      if (p.mark) slot.insertAdjacentHTML('beforeend',
        `<span class="ap-mark ${p.mark}" title="${p.mark}">${p.mark === 'correct' ? '✓' : '✗'}</span>`);
      fields[p.name] = { part: p, el };
    } else {
      fields[p.name] = { part: p };
      const saved = draft.get(p.name);
      if (saved != null) root.querySelectorAll(`[name="${p.name}"]`).forEach(i => {
        if (i.type === 'radio') i.checked = i.value === saved;
        else if (i.type === 'checkbox') i.checked = saved.includes(i.value);
        else i.value = saved;
      });
      root.querySelectorAll(`[name="${p.name}"]`).forEach(i => i.addEventListener('change', () =>
        draft.set(p.name, readChoice(root, p))));
      // WebAssign's own marks are hidden: show ✓/✗ after the last option (or the drop-down)
      const els = root.querySelectorAll(`[name="${p.name}"]`), last = els[els.length - 1];
      if (last && p.mark) (last.closest('label, .ms') || last).insertAdjacentHTML('afterend',
        ` <span class="ap-mark ${p.mark}" title="${p.mark}">${p.mark === 'correct' ? '✓' : '✗'}</span>`);
    }
  }

  const of = practice ? practice.of : q;
  card.querySelector('[data-pav]').onclick = e => startPractice(asg, of, card, e.target);
  if (practice) {
    card.querySelector('[data-close]').onclick = () => card.remove();
    card.querySelector('[data-check]').onclick = e => checkPractice(asg, practice, card, root, fields, e.target);
  } else card.querySelectorAll('footer button[data-act]').forEach(b => b.onclick = () => submitQuestion(asg, q, root, fields, b));
  return card;
}

function collectAnswers(root, fields) {
  const answers = {};
  for (const [name, f] of Object.entries(fields)) {
    if (!f.part.enabled) continue;
    if (f.part.kind === 'math') {
      const latex = f.el.value.trim();
      answers[name] = latex ? latexToMathml(latex) : '';
    } else if (f.part.kind === 'text' || f.part.kind === 'matrix') answers[name] = f.el.value;
    else { const v = readChoice(root, f.part); if (v != null) answers[name] = v; }
  }
  return answers;
}

function showCardError(card, title, msg) {
  card.querySelector('.ap-error')?.remove();
  card.querySelector('footer').insertAdjacentHTML('beforebegin',
    `<div class="ap-error"><strong>${esc(title)}</strong> ${esc(msg)}</div>`);
}

// "Practice Another": WebAssign makes a new version of the question with new numbers. Not graded.
async function startPractice(asg, of, card, btn) {
  const old = btn.textContent; btn.disabled = true; btn.textContent = 'Loading…';
  try {
    const r = await api(`/assignments/${asg.dep}/questions/${of.box}/practice`, { method: 'POST' });
    const pc = buildQuestion({ ...asg, styles: r.styles }, r.question, { id: r.id, of });
    const target = card.classList.contains('practice') ? card : document.getElementById('practice-' + of.box);
    if (target) target.replaceWith(pc); else card.after(pc);
    pc.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) {
    showCardError(card, 'Practice failed.', e.message);
    btn.disabled = false; btn.textContent = old;
  }
}

async function checkPractice(asg, practice, card, root, fields, btn) {
  const btns = card.querySelectorAll('footer button');
  btns.forEach(b => b.disabled = true); btn.textContent = 'Checking…';
  try {
    const r = await api(`/practice/${practice.id}`, { method: 'POST', body: JSON.stringify({ answers: collectAnswers(root, fields) }) });
    const pc = buildQuestion({ ...asg, styles: r.styles }, r.question, practice);
    card.replaceWith(pc);
    toast(describeResult(r.question));
  } catch (e) {
    showCardError(card, 'Check failed.', e.message);
    btns.forEach(b => b.disabled = false); btn.textContent = 'Check answer';
  }
}

function readChoice(root, p) {
  const els = [...root.querySelectorAll(`[name="${p.name}"]`)];
  if (p.kind === 'radio') return els.find(i => i.checked)?.value ?? null;
  if (p.kind === 'checkbox') return els.filter(i => i.checked).map(i => i.value);
  return els[0]?.value ?? null;
}

async function submitQuestion(asg, q, root, fields, btn) {
  const action = btn.dataset.act;
  const answers = collectAnswers(root, fields);
  if (action === 'submit' && !confirm(`Submit question ${q.number} to WebAssign?`)) return;
  const btns = btn.parentElement.querySelectorAll('button');
  btns.forEach(b => b.disabled = true);
  const old = btn.textContent; btn.textContent = action === 'submit' ? 'Submitting…' : 'Saving…';
  try {
    const data = await api(`/assignments/${asg.dep}/questions/${q.box}`, {
      method: 'POST', body: JSON.stringify({ answers, action }),
    });
    store.set('asg:' + asg.dep, data);
    // WebAssign has the answer now. Clear drafts so the page shows its values.
    Object.keys(fields).forEach(n => localStorage.removeItem(draftKey(asg.dep, n)));
    const nq = data.questions.find(x => x.box === q.box);
    toast(action === 'submit' ? describeResult(nq) : 'Saved to WebAssign');
    renderAssignment(data, q.box);
  } catch (e) {
    // Keep the full error on the card, with WebAssign's screenshot, until the next try.
    const card = btn.closest('.card');
    card.querySelector('.ap-error')?.remove();
    card.querySelector('footer').insertAdjacentHTML('beforebegin', `<div class="ap-error">
      <strong>${action === 'submit' ? 'Submit' : 'Save'} failed.</strong> ${esc(e.message)}
      <a href="/api/submit-error.png?t=${Date.now()}" target="_blank">WebAssign screenshot</a></div>`);
    btns.forEach(b => b.disabled = false); btn.textContent = old;
  }
}

function describeResult(q) {
  if (!q) return 'Submitted';
  const marks = q.parts.map(p => p.mark).filter(Boolean);
  if (marks.length && marks.every(m => m === 'correct')) return 'Correct ✓';
  if (marks.includes('incorrect')) return `Incorrect ✗ (${marks.filter(m => m === 'correct').length}/${marks.length} parts correct)`;
  return 'Submitted';
}

function renderAssignment(asg, focusBox) {
  const list = store.get('list');
  const meta = list?.assignments?.find(a => a.dep === asg.dep) || {};
  setBar(asg.name || meta.name || 'Assignment', [fmtDue(meta), meta.score].filter(Boolean).join(' · '), true);
  const scroll = window.scrollY;
  view.innerHTML = `<div class="hdr"><span>${asg.questions.length} questions</span>
    <span>Synced ${new Date(asg.synced * 1000).toLocaleTimeString()}</span></div>
    <div class="card ext" id="ext-one"></div>`;
  renderExtension(asg.dep, $('#ext-one'));
  if (!asg.questions.length) view.insertAdjacentHTML('beforeend', '<p class="empty">No questions found.</p>');
  for (const q of asg.questions) view.append(buildQuestion(asg, q));
  if (focusBox) window.scrollTo(0, scroll);
}

async function showAssignment(dep, refresh = true) {
  let cached = store.get('asg:' + dep);
  if (cached) renderAssignment(cached);
  else {
    view.innerHTML = '<p class="note">Loading from WebAssign…</p>';
    try { // Server cache first: it is fast. Then refresh from WebAssign.
      cached = await api(`/assignments/${dep}`);
      store.set('asg:' + dep, cached); renderAssignment(cached);
      if (Date.now() / 1000 - cached.synced < 60) return; // just loaded from WebAssign
    } catch (e) {
      showLoadError(dep, e.message);
      return;
    }
  }
  try {
    const data = await api(`/assignments/${dep}${refresh ? '?refresh=1' : ''}`);
    store.set('asg:' + dep, data);
    if (location.hash === '#/a/' + dep && JSON.stringify([data.questions, data.styles]) !== JSON.stringify([cached?.questions, cached?.styles])) {
      // Never rebuild the answer boxes while you work in them.
      const busy = document.activeElement?.closest?.('.card') || Date.now() - lastInput < 120000;
      if (!busy) renderAssignment(data, true);
      else toast('New data from WebAssign. Press sync to show it.');
    }
  } catch (e) { toast(e.message, 5000); }
}

function showLoadError(dep, msg) {
  if (location.hash !== '#/a/' + dep) return;
  view.innerHTML = `<div class="card"><div class="body">
      <p><strong>Could not load this assignment.</strong></p><p>${esc(msg)}</p></div>
    <footer><a class="btn" href="/api/assignment-error.png" target="_blank">Screenshot</a>
      <a class="btn" href="https://www.webassign.net/web/Student/Assignment-Responses/last?dep=${esc(dep)}" target="_blank" rel="noopener">Open in WebAssign</a>
      <button class="btn primary" id="retry">Try again</button></footer></div>`;
  $('#retry').onclick = () => showAssignment(dep, true);
  checkSession();
}

// ---------- Routing ----------
function route(refresh = false) {
  const m = location.hash.match(/^#\/a\/(\d+)/);
  if (m) showAssignment(m[1], true);
  else showList(refresh);
}

$('#back').onclick = () => { location.hash = '#/'; };
$('#sync').onclick = async () => {
  const b = $('#sync'); b.classList.add('spin');
  const m = location.hash.match(/^#\/a\/(\d+)/);
  try { m ? await showAssignment(m[1], true) : await showList(true); await checkSession(); }
  finally { b.classList.remove('spin'); }
};
window.addEventListener('hashchange', () => route());
route();
checkSession();
if ('serviceWorker' in navigator) {
  // A new version activates at once (skipWaiting). Reload one time so the page uses it.
  const hadSW = !!navigator.serviceWorker.controller;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (hadSW && !sessionStorage.getItem('sw-reloaded')) { sessionStorage.setItem('sw-reloaded', '1'); location.reload(); }
  });
  navigator.serviceWorker.register('/sw.js');
  setTimeout(() => sessionStorage.removeItem('sw-reloaded'), 10000);
}
