// Send WebAssign cookies to the AssignPad server when they change, and every 30 minutes.
const DOMAINS = ['webassign.net', 'cengage.com'];

async function collect() {
  const all = [];
  for (const domain of DOMAINS) all.push(...await chrome.cookies.getAll({ domain }));
  return all.map(({ name, value, domain, path, secure, httpOnly, sameSite, expirationDate }) =>
    ({ name, value, domain, path, secure, httpOnly, sameSite, expirationDate }));
}

async function push() {
  const { server, token } = await chrome.storage.local.get(['server', 'token']);
  if (!server) return { ok: false, msg: 'Set the server URL first.' };
  const cookies = await collect();
  if (!cookies.some(c => c.domain.includes('webassign'))) return { ok: false, msg: 'Log in to WebAssign first.' };
  try {
    const r = await fetch(server.replace(/\/$/, '') + '/api/session', {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: 'Bearer ' + (token || '') },
      body: JSON.stringify({ cookies }),
    });
    const data = await r.json().catch(() => ({}));
    const res = r.ok
      ? { ok: !!data.loggedIn, msg: data.loggedIn
          ? `Session sent (${data.count} cookies). Server is logged in.`
          : `Sent ${data.count} cookies, but the server is not logged in. ${data.error || ''}` }
      : { ok: false, msg: r.status === 401 ? 'Wrong AssignPad password.'
          : `Server error ${r.status}: ${data.detail || data.error || r.statusText}` };
    await chrome.storage.local.set({ last: { ...res, at: Date.now() } });
    return res;
  } catch (e) {
    const res = { ok: false, msg: 'Cannot reach server: ' + e.message };
    await chrome.storage.local.set({ last: { ...res, at: Date.now() } });
    return res;
  }
}

let timer;
chrome.cookies.onChanged.addListener(({ cookie, removed }) => {
  if (removed || !cookie.domain.includes('webassign')) return;
  clearTimeout(timer);
  timer = setTimeout(push, 5000);
});
chrome.alarms.create('push', { periodInMinutes: 30 });
chrome.alarms.onAlarm.addListener(a => { if (a.name === 'push') push(); });
chrome.runtime.onMessage.addListener((msg, _s, reply) => {
  if (msg === 'push') { push().then(reply); return true; }
});
