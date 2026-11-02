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

// ---------- Assignment list ----------
function renderList(data) {
  setBar(data.course || 'AssignPad', data.synced ? 'Synced ' + new Date(data.synced * 1000).toLocaleString() : '');
  const rows = data.assignments || [];
  const sec = (label, items) => items.length ? `<h2 class="section">${label}</h2><div class="list">${items.map(a =>
    `<a class="row" href="#/a/${a.dep}"><span class="name">${esc(a.name)}</span>
      <span class="meta">${esc(a.due)}${a.score ? '<br>' + esc(a.score) : ''}</span></a>`).join('')}</div>` : '';
  view.innerHTML = rows.length
    ? sec('Current', rows.filter(a => a.group === 'current')) + sec('Past', rows.filter(a => a.group !== 'current'))
    : '<p class="empty">No assignments yet. Press sync.</p>';
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
      .ap-lock { font-size: 12px; color: var(--muted); }
      math-field { min-width: 140px; font-size: 1.15em; padding: 2px 6px; border-radius: 6px;
        border: 1px solid var(--line); background: var(--card); color: var(--text);
        --caret-color: var(--accent); --selection-background-color: color-mix(in srgb, var(--accent) 25%, transparent); }
      math-field:focus-within { outline: 2px solid var(--accent); outline-offset: 1px; }
      math-field[readonly] { opacity: .7; border-style: dashed; cursor: not-allowed; }
      math-field::part(menu-toggle) { display: none; }
      math-field::part(container) { cursor: text; }
      input.ap-text { font: inherit; padding: 4px 8px; border-radius: 6px; border: 1px solid var(--line);
        background: var(--card); color: var(--text); }
      .ap-mark { font-weight: 700; } .ap-mark.correct { color: var(--ok); } .ap-mark.incorrect { color: var(--bad); }
      img { max-width: 100%; height: auto; }
      /* The app shows its own ✓/✗ next to each answer box. Hide WebAssign's marks. */
      .waMark, .padMark, .waMarkWrap, img[src*="mCorrect"], img[src*="mIncorrect"] { display: none !important; }
      table { color: inherit; }
      .questionDetail, .qUtility .preContent { display: none; }
      @media (prefers-color-scheme: dark) { img:not([src*=".svg"]) { filter: invert(.9) hue-rotate(180deg); } }
    </style>`;
}

function buildQuestion(asg, q) {
  const card = document.createElement('section');
  card.className = 'card'; card.id = q.box;
  const pts = (q.details.Points || []).slice(-1)[0] || '';
  const allOk = q.parts.length && q.parts.every(p => p.mark === 'correct');
  const anyBad = q.parts.some(p => p.mark === 'incorrect');
  card.innerHTML = `<header><span class="num">${q.number}.</span>
      <span class="info">${pts ? esc(pts) + ' pts' : ''}${q.submissions ? ' · submissions ' + esc(q.submissions) : ''}</span>
      ${allOk ? '<span class="badge ok">Correct</span>' : anyBad ? '<span class="badge bad">Incorrect</span>' : ''}
    </header><div class="body"></div>
    <footer>${q.canSubmit ? '<button class="btn" data-act="save">Save</button><button class="btn primary" data-act="submit">Submit</button>' : ''}
      <a class="btn" href="${esc(asg.url)}#${esc(q.box)}" target="_blank" rel="noopener">Open in WebAssign</a></footer>`;
  const host = card.querySelector('.body');
  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = qStyles(asg.styles || []) + `<div class="q">${q.html}</div>`;
  root.querySelectorAll('.ap-tex').forEach(el => {
    try { el.innerHTML = convertLatexToMarkup(el.textContent); } catch {}
  });

  const fields = {};
  for (const p of q.parts) {
    if (p.kind === 'math' || p.kind === 'text') {
      const slot = root.querySelector(`.ap-box[data-name="${p.name}"]`);
      if (!slot) continue;
      const saved = store.get(draftKey(asg.dep, p.name));
      let el;
      if (p.kind === 'math') {
        el = new MathfieldElement();
        el.mathVirtualKeyboardPolicy = 'auto';
        el.smartFence = true;
        el.value = saved ?? mmlToLatex(p.value);
        el.addEventListener('input', () => store.set(draftKey(asg.dep, p.name), el.value));
      } else {
        el = document.createElement('input');
        el.className = 'ap-text';
        el.value = saved ?? p.value ?? '';
        el.addEventListener('input', () => store.set(draftKey(asg.dep, p.name), el.value));
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
      const saved = store.get(draftKey(asg.dep, p.name));
      if (saved != null) root.querySelectorAll(`[name="${p.name}"]`).forEach(i => {
        if (i.type === 'radio') i.checked = i.value === saved;
        else if (i.type === 'checkbox') i.checked = saved.includes(i.value);
        else i.value = saved;
      });
      root.querySelectorAll(`[name="${p.name}"]`).forEach(i => i.addEventListener('change', () =>
        store.set(draftKey(asg.dep, p.name), readChoice(root, p))));
    }
  }

  card.querySelectorAll('footer button').forEach(b => b.onclick = () => submitQuestion(asg, q, root, fields, b));
  return card;
}

function readChoice(root, p) {
  const els = [...root.querySelectorAll(`[name="${p.name}"]`)];
  if (p.kind === 'radio') return els.find(i => i.checked)?.value ?? null;
  if (p.kind === 'checkbox') return els.filter(i => i.checked).map(i => i.value);
  return els[0]?.value ?? null;
}

async function submitQuestion(asg, q, root, fields, btn) {
  const action = btn.dataset.act;
  const answers = {};
  for (const [name, f] of Object.entries(fields)) {
    if (!f.part.enabled) continue;
    if (f.part.kind === 'math') {
      const latex = f.el.value.trim();
      answers[name] = latex ? latexToMathml(latex) : '';
    } else if (f.part.kind === 'text') answers[name] = f.el.value;
    else { const v = readChoice(root, f.part); if (v != null) answers[name] = v; }
  }
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
    toast(e.message, 6000);
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
  setBar(asg.name || meta.name || 'Assignment', [meta.due, meta.score].filter(Boolean).join(' · '), true);
  const scroll = window.scrollY;
  view.innerHTML = `<div class="hdr"><span>${asg.questions.length} questions</span>
    <span>Synced ${new Date(asg.synced * 1000).toLocaleTimeString()}</span></div>`;
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
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js');
