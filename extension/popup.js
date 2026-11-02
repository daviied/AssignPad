const $ = id => document.getElementById(id);
const show = (r) => { $('msg').textContent = r.msg; $('msg').className = r.ok ? 'ok' : 'bad'; };

chrome.storage.local.get(['server', 'token', 'last']).then(({ server, token, last }) => {
  $('server').value = server || '';
  $('token').value = token || '';
  if (last) show({ ...last, msg: last.msg + ' (' + new Date(last.at).toLocaleTimeString() + ')' });
});

$('save').onclick = async () => {
  const server = $('server').value.trim().replace(/\/$/, '');
  if (!/^https?:\/\//.test(server)) return show({ ok: false, msg: 'Server URL must start with https:// (or http:// on your LAN)' });
  // Ask for access to the server origin only.
  const granted = await chrome.permissions.request({ origins: [new URL(server).origin + '/*'] });
  if (!granted) return show({ ok: false, msg: 'Permission for the server was not given.' });
  await chrome.storage.local.set({ server, token: $('token').value });
  show({ ok: true, msg: 'Sending…' });
  show(await chrome.runtime.sendMessage('push'));
};
