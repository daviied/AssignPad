"""WebAssign session: a headless Chromium that uses your WebAssign cookies."""
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from parse import parse_assignment, BASE

log = logging.getLogger("wa")
HOME = BASE + "/v4cgi/student.pl"
ASG_URL = BASE + "/web/Student/Assignment-Responses/last?dep={dep}"
ALLOWED_HOSTS = ("webassign.net", "cengage.com", "cengage.io", "cengage.info")


def host_allowed(url: str) -> bool:
    h = (urlparse(url).hostname or "").lower()
    return urlparse(url).scheme == "https" and any(h == a or h.endswith("." + a) for a in ALLOWED_HOSTS)


def _pw_cookie(c):
    """Chrome extension cookie -> Playwright cookie."""
    same = {"no_restriction": "None", "lax": "Lax", "strict": "Strict"}.get(c.get("sameSite"), "Lax")
    out = {
        "name": c["name"], "value": c["value"], "domain": c["domain"], "path": c.get("path", "/"),
        "httpOnly": bool(c.get("httpOnly")), "secure": bool(c.get("secure")), "sameSite": same,
    }
    if c.get("expirationDate"):
        out["expires"] = float(c["expirationDate"])
    if same == "None":
        out["secure"] = True
    return out


class WebAssign:
    def __init__(self, data: Path):
        self.data = data
        self.session_file = data / "session.json"
        self.lock = asyncio.Lock()
        self.pw = self.browser = self.ctx = None
        self.logged_in = False
        self.last_check = 0.0

    # ---------- browser / session ----------
    async def start(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        await self._new_context()

    async def stop(self):
        if self.browser:
            await self.browser.close()
        if self.pw:
            await self.pw.stop()

    async def _new_context(self):
        if self.ctx:
            await self.ctx.close()
        self.ctx = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            viewport={"width": 1300, "height": 900},
        )
        # Skip heavy assets in headless pages. The answer form does not need them.
        await self.ctx.route(re.compile(r".*\.(png|jpe?g|gif|svg|woff2?|ttf|mp4)(\?.*)?$"),
                             lambda r: r.abort())
        if self.session_file.exists():
            cookies = json.loads(self.session_file.read_text())
            await self.ctx.add_cookies(cookies)
        self.ctx.on("page", lambda p: p.on("dialog", lambda d: asyncio.ensure_future(d.accept())))

    async def _save_cookies(self):
        cookies = await self.ctx.cookies()
        self.session_file.write_text(json.dumps(cookies))
        os.chmod(self.session_file, 0o600)

    async def set_cookies(self, chrome_cookies):
        cookies = [_pw_cookie(c) for c in chrome_cookies if c.get("name") and c.get("domain")]
        self.session_file.write_text(json.dumps(cookies))
        os.chmod(self.session_file, 0o600)
        async with self.lock:
            await self._new_context()
        return await self.check(force=True)

    async def check(self, force=False):
        """True when the stored session is still logged in."""
        if not force and time.time() - self.last_check < 120:
            return self.logged_in
        try:
            r = await self.ctx.request.get(HOME, max_redirects=10)
            self.logged_in = r.ok and "login" not in r.url.lower() and "My Assignments" in await r.text()
        except Exception as e:  # network error
            log.warning("session check failed: %s", e)
            self.logged_in = False
        self.last_check = time.time()
        if not self.logged_in and os.environ.get("CENGAGE_EMAIL") and not force:
            await self.login()
        return self.logged_in

    async def login(self, email=None, password=None):
        """Log in with a Cengage email + password. Fallback when the extension is not used."""
        email = email or os.environ.get("CENGAGE_EMAIL")
        password = password or os.environ.get("CENGAGE_PASSWORD")
        if not (email and password):
            return False
        async with self.lock:
            page = await self.ctx.new_page()
            try:
                await page.goto(BASE + "/v4cgi/login.pl", wait_until="domcontentloaded")
                await page.wait_for_selector('input[type="email"], input[name="email"], input[name="username"]',
                                             timeout=20000)
                await page.fill('input[type="email"], input[name="email"], input[name="username"]', email)
                pw = page.locator('input[type="password"]')
                if not await pw.is_visible():
                    await page.keyboard.press("Enter")
                    await pw.wait_for(timeout=20000)
                await pw.fill(password)
                await page.keyboard.press("Enter")
                await page.wait_for_url(re.compile(r"webassign\.net/(v4cgi/student|web/)"), timeout=45000)
                await self._save_cookies()
            except Exception as e:
                log.warning("login failed: %s", e)
            finally:
                await page.close()
        return await self.check(force=True)

    # ---------- reading ----------
    async def list_assignments(self):
        async with self.lock:
            page = await self.ctx.new_page()
            try:
                await page.goto(HOME, wait_until="domcontentloaded")
                await page.wait_for_selector('a[href*="Assignment-Responses"]', timeout=30000)
                course = (await page.title()).split(" - ")[0]
                rows = await self._read_links(page, "current")
                tab = page.get_by_text(re.compile(r"Past Assignments"))
                if await tab.count():
                    await tab.first.click()
                    try:
                        await page.wait_for_function(
                            "n => document.querySelectorAll('a[href*=\"Assignment-Responses\"]').length > n",
                            arg=len(rows), timeout=15000)
                    except Exception:
                        pass
                    seen = {r["dep"] for r in rows}
                    rows += [r for r in await self._read_links(page, "past") if r["dep"] not in seen]
                await self._save_cookies()
                return {"course": course, "assignments": rows, "synced": time.time()}
            finally:
                await page.close()

    @staticmethod
    async def _read_links(page, group):
        return await page.evaluate("""group => [...document.querySelectorAll('a[href*="Assignment-Responses"]')]
          .map(a => {
            const dep = new URL(a.href).searchParams.get('dep');
            const lines = a.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
            const row = a.closest('tr, li, [role=row]');
            const extra = row && row !== a ? row.innerText.split('\\n').map(s => s.trim()).filter(Boolean) : [];
            const due = [...lines, ...extra].find(s => /\\b(AM|PM)\\b/.test(s)) || '';
            const score = [...lines, ...extra].find(s => /\\d+\\s*\\/\\s*\\d+|%/.test(s) && !/AM|PM/.test(s)) || '';
            return {dep, name: lines[0] || a.textContent.trim(), due, score, group};
          }).filter(r => r.dep)""", group)

    async def get_assignment(self, dep: str):
        r = await self.ctx.request.get(ASG_URL.format(dep=dep))
        html = await r.text()
        # A lost session redirects to the Cengage login page.
        if not (urlparse(r.url).hostname or "").endswith("webassign.net") or 'id="assignment' not in html:
            self.logged_in = False
            raise PermissionError("WebAssign session expired")
        data = parse_assignment(html, r.url)
        data.update(dep=dep, url=ASG_URL.format(dep=dep), synced=time.time())
        return data

    async def proxy(self, url: str):
        r = await self.ctx.request.get(url)
        return await r.body(), r.headers.get("content-type", "application/octet-stream"), r.status

    # ---------- writing ----------
    async def submit(self, dep: str, box: str, answers: dict, action: str = "submit"):
        """Fill answers into the real WebAssign page and click its Submit (or Save) button."""
        if action not in ("submit", "save"):
            raise ValueError("bad action")
        async with self.lock:
            page = await self.ctx.new_page()
            try:
                await page.goto(ASG_URL.format(dep=dep), wait_until="domcontentloaded")
                q = page.locator(f"#{box}")
                await q.wait_for(timeout=30000)
                await page.wait_for_load_state("networkidle", timeout=30000)
                for name, value in answers.items():
                    kind = await page.evaluate(
                        "n => { const e = document.getElementsByName(n)[0]; return e ? (e.type || e.tagName) : null }",
                        name)
                    if kind is None:
                        raise ValueError(f"answer box {name} not found")
                    if kind in ("radio", "checkbox"):
                        vals = value if isinstance(value, list) else [value]
                        for inp in await page.locator(f'input[name="{name}"]').all():
                            want = await inp.get_attribute("value") in vals
                            if want != await inp.is_checked():
                                await inp.click(force=True)
                    elif kind.lower() == "select-one":
                        await page.select_option(f'select[name="{name}"]', value)
                    else:
                        await page.evaluate("""([n, v]) => {
                            const e = document.getElementsByName(n)[0];
                            e.value = v;
                            e.dispatchEvent(new Event('input', {bubbles: true}));
                            e.dispatchEvent(new Event('change', {bubbles: true}));
                          }""", [name, value])
                btn = q.locator(f'input[name="{action}"]').first
                async with page.expect_response(lambda r: r.request.method == "POST", timeout=45000):
                    await btn.click()
                await page.wait_for_load_state("networkidle", timeout=45000)
                html = await page.content()
                await self._save_cookies()
            finally:
                await page.close()
        data = parse_assignment(html, ASG_URL.format(dep=dep))
        data.update(dep=dep, url=ASG_URL.format(dep=dep), synced=time.time())
        return data
