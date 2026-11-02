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
        self.last_login_try = 0.0
        self.last_error = ""

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
            # The home page is built by JavaScript, so check where we land, not the text.
            # A lost session redirects to account.cengage.com/logout.
            r = await self.ctx.request.get(HOME, max_redirects=10)
            u = urlparse(r.url)
            self.logged_in = r.ok and (u.hostname or "").endswith("webassign.net") and "student.pl" in u.path
            if not self.logged_in:
                self.last_error = f"WebAssign sent the server to {u.hostname}{u.path}: session not valid."
        except Exception as e:  # network error
            log.warning("session check failed: %s", e)
            self.logged_in = False
            self.last_error = f"Cannot reach WebAssign: {e}"
        self.last_check = time.time()
        if (not self.logged_in and os.environ.get("CENGAGE_EMAIL") and not force
                and time.time() - self.last_login_try > 300):
            await self.login()
        return self.logged_in

    async def login(self, email=None, password=None):
        """Log in with a Cengage email + password (Okta form). Fallback when the extension is not used."""
        email = email or os.environ.get("CENGAGE_EMAIL")
        password = password or os.environ.get("CENGAGE_PASSWORD")
        if not (email and password):
            self.last_error = "CENGAGE_EMAIL and CENGAGE_PASSWORD are not set on the server."
            return False
        self.last_login_try = time.time()
        async with self.lock:
            page = await self.ctx.new_page()
            step = "open login page"
            try:
                # login.pl starts a SAML login that returns to WebAssign. student.pl does not.
                await page.goto(BASE + "/v4cgi/login.pl", wait_until="domcontentloaded")
                user = page.locator('input[name="username"], input[type="email"]').first
                await user.wait_for(timeout=30000)
                step = "enter email"
                await user.fill(email)
                pw = page.locator('input[type="password"]').first
                if not await pw.is_visible():
                    await page.locator('#idp-discovery-submit, input[type="submit"]').first.click()
                    step = "wait for password field"
                    await pw.wait_for(timeout=30000)
                step = "enter password"
                await pw.fill(password)
                await page.locator('#okta-signin-submit, input[type="submit"]').first.click()
                step = "wait for WebAssign after sign in"
                await page.wait_for_url(lambda url: "webassign.net" in url, timeout=60000)
                await page.wait_for_load_state("domcontentloaded")
                await self._save_cookies()
                self.last_error = ""
            except Exception as e:
                text = ""
                try:
                    await page.screenshot(path=str(self.data / "login-error.png"))
                    text = (await page.locator('.okta-form-infobox-error, .o-form-error-container, [role=alert]')
                            .first.inner_text(timeout=2000)).strip()
                except Exception:
                    pass
                self.last_error = f"Cengage login failed at step '{step}'. {text or type(e).__name__}"
                log.warning("%s (%s)", self.last_error, e)
            finally:
                await page.close()
        return await self.check(force=True)

    # ---------- reading ----------
    async def list_assignments(self, retry=True):
        async with self.lock:
            page = await self.ctx.new_page()
            try:
                await page.goto(HOME, wait_until="domcontentloaded")
                try:
                    await page.wait_for_selector('a[href*="Assignment-Responses"]', timeout=30000)
                except Exception:
                    if (urlparse(page.url).hostname or "").endswith("webassign.net"):
                        raise RuntimeError("WebAssign home page did not show assignments.")
                    self.logged_in = False
                else:
                    return await self._list_from(page)
            finally:
                await page.close()
        # Session lost: log in with email/password and try once more.
        if retry and await self.login():
            return await self.list_assignments(retry=False)
        raise PermissionError("WebAssign session expired. " + self.last_error)

    async def _list_from(self, page):
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

    async def get_assignment(self, dep: str, retry=True):
        """Open the assignment in the headless browser, like a person would, and parse it."""
        url = ASG_URL.format(dep=dep)
        docs = []  # HTML documents as WebAssign sent them, before page JavaScript changes them
        async with self.lock:
            page = await self.ctx.new_page()
            page.on("response", lambda r: docs.append(r) if r.request.resource_type == "document"
                    and "Assignment-Responses" in r.url else None)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                # Wait for the questions, or stop early when WebAssign sends us to the Cengage login.
                for _ in range(60):
                    if not (urlparse(page.url).hostname or "").endswith("webassign.net"):
                        break
                    try:
                        if await page.query_selector(".waQBox"):
                            break
                    except Exception:  # page is navigating
                        pass
                    await page.wait_for_timeout(500)
                final = page.url
                html = ""
                lost = not (urlparse(final).hostname or "").endswith("webassign.net")
                for r in ([] if lost else reversed(docs)):
                    if r.status != 200:
                        continue  # redirects have no body; reading one can hang
                    try:
                        html = await asyncio.wait_for(r.text(), 10)
                        break
                    except Exception:
                        continue
                if not lost and "waQBox" not in html:
                    try:
                        html = await page.content()  # page built by JavaScript
                    except Exception:
                        pass
                if not lost and "waQBox" not in html:
                    await page.screenshot(path=str(self.data / "assignment-error.png"), full_page=True)
                    title = await page.title()
                await self._save_cookies()
            finally:
                await page.close()
        if not (urlparse(final).hostname or "").endswith("webassign.net"):
            self.logged_in = False
            if retry and await self.login():
                return await self.get_assignment(dep, retry=False)
            raise PermissionError("WebAssign session expired. " + self.last_error)
        if "waQBox" not in html:
            raise RuntimeError(f"No questions found on the WebAssign page ('{title}'). "
                               "See the screenshot link.")
        data = parse_assignment(html, final)
        data.update(dep=dep, url=url, synced=time.time())
        return data

    async def proxy(self, url: str):
        r = await self.ctx.request.get(url)
        return await r.body(), r.headers.get("content-type", "application/octet-stream"), r.status

    # ---------- writing ----------
    async def submit(self, dep: str, box: str, answers: dict, action: str = "submit"):
        """Fill answers into the real WebAssign page and click its Submit (or Save) button."""
        if action not in ("submit", "save"):
            raise ValueError("bad action")
        notes = []  # WebAssign pop-ups and page script errors, shown to the user on failure
        step = "open assignment"
        async with self.lock:
            page = await self.ctx.new_page()
            page.on("dialog", lambda d: notes.append(f"WebAssign pop-up: {d.message}"))
            page.on("pageerror", lambda e: notes.append(f"WebAssign page error: {e}"))
            try:
                await page.goto(ASG_URL.format(dep=dep), wait_until="domcontentloaded")
                q = page.locator(f"#{box}")
                await q.wait_for(timeout=30000)
                step = "wait for question scripts"
                # WebAssign never goes network-idle (analytics); wait for its question object instead.
                try:
                    await page.wait_for_function(
                        "id => { const e = document.getElementById(id); return e && e.retrieve && e.retrieve('question') }",
                        arg=box, timeout=20000)
                except Exception:
                    await page.wait_for_timeout(3000)
                for name, value in answers.items():
                    step = f"fill {name}"
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
                        # WebAssign loads MooTools, which replaces window.Event, so `new Event()`
                        # crashes there. document.createEvent makes a native event.
                        await page.evaluate("""([n, v]) => {
                            const e = document.getElementsByName(n)[0];
                            e.value = v;
                            for (const type of ['input', 'change']) {
                              const ev = document.createEvent('HTMLEvents');
                              ev.initEvent(type, true, true);
                              e.dispatchEvent(ev);
                            }
                          }""", [name, value])
                step = f"click {action}"
                btn = q.locator(f'input[name="{action}"]').first
                if not await btn.count():
                    raise RuntimeError(f"WebAssign shows no {action} button for this question "
                                       "(past due, or no submissions left?)")
                async with page.expect_response(lambda r: r.request.method == "POST", timeout=45000):
                    await btn.click()
                step = "wait for WebAssign result"
                await page.wait_for_load_state("domcontentloaded", timeout=45000)
                await page.locator(f"#{box}").wait_for(timeout=30000)
                await page.wait_for_timeout(1500)  # let WebAssign update the marks
                html = await page.content()
                await self._save_cookies()
            except Exception as e:
                try:
                    await page.screenshot(path=str(self.data / "submit-error.png"), full_page=True)
                except Exception:
                    pass
                # Playwright errors have a long call log after the first line.
                msg = f"Submit failed at step '{step}': {str(e).splitlines()[0] if str(e) else type(e).__name__}"
                if notes:
                    msg += " | " + " | ".join(notes[-3:])
                log.warning(msg)
                raise RuntimeError(msg) from None
            finally:
                await page.close()
        if notes:
            log.info("submit notes: %s", notes)
        data = parse_assignment(html, ASG_URL.format(dep=dep))
        data["notes"] = notes[-3:]
        data.update(dep=dep, url=ASG_URL.format(dep=dep), synced=time.time())
        return data
