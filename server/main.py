"""AssignPad API server."""
import asyncio
import hashlib
import hmac
import secrets
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from extensions import Extensions
from wa import WebAssign, host_allowed

logging.basicConfig(level=logging.INFO)
DATA = Path(os.environ.get("DATA_DIR", "/data"))
(DATA / "cache").mkdir(parents=True, exist_ok=True)
STATIC = Path(__file__).resolve().parent.parent / "public"
PASSWORD = os.environ.get("APP_PASSWORD", "")
if len(PASSWORD) < 8:
    raise SystemExit("Set APP_PASSWORD (8+ characters). The server will not start without it.")
DEV = os.environ.get("DEV") == "1"
wa = WebAssign(DATA)
ext = Extensions(DATA, wa)

# Session cookie = HMAC of a random server secret. The password is never stored in the cookie.
_secret_file = DATA / "secret"
if not _secret_file.exists():
    _secret_file.write_text(secrets.token_hex(32))
    os.chmod(_secret_file, 0o600)
SESSION = hmac.new(_secret_file.read_text().encode(), PASSWORD.encode(), hashlib.sha256).hexdigest()
_fails: dict[str, list[float]] = {}


def client_ip(request: Request):
    return (request.headers.get("x-real-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "?"))


def rate_limited(ip):
    now = time.time()
    _fails[ip] = [t for t in _fails.get(ip, []) if now - t < 900]
    return len(_fails[ip]) >= 10


async def keepalive():
    """Keep the WebAssign session alive so it does not time out."""
    while True:
        await asyncio.sleep(15 * 60)
        try:
            await wa.check(force=False)
        except Exception as e:
            logging.warning("keepalive: %s", e)


PREFETCH_EVERY = float(os.environ.get("PREFETCH_HOURS", "24")) * 3600


async def prefetch_all():
    """Download the assignment list and every assignment into the cache, so pages open at once.
    Current assignments are fetched each run. Past ones only when not yet cached (they do not change)."""
    if not await wa.check():
        logging.info("prefetch: not logged in to WebAssign, skipped")
        return
    data = write_cache("_list", await asyncio.wait_for(wa.list_assignments(), 150))
    done = 0
    for a in data.get("assignments", []):
        if a.get("group") == "past" and read_cache(a["dep"]):
            continue
        try:
            write_cache(a["dep"], await asyncio.wait_for(wa.get_assignment(a["dep"]), 120))
            done += 1
        except Exception as e:
            logging.warning("prefetch %s: %s", a["dep"], e)
        await asyncio.sleep(2)  # be gentle with WebAssign; also lets user requests take the lock
    (DATA / "prefetch.json").write_text(json.dumps({"at": time.time(), "count": done}))
    logging.info("prefetch: %d assignments cached", done)


async def prefetcher():
    """Run prefetch_all once per PREFETCH_HOURS (default 24). Set PREFETCH_HOURS=0 to turn it off."""
    if PREFETCH_EVERY <= 0:
        return
    while True:
        f = DATA / "prefetch.json"
        last = json.loads(f.read_text()).get("at", 0) if f.exists() else 0
        wait = last + PREFETCH_EVERY - time.time()
        await asyncio.sleep(max(wait, 60))  # small delay at startup so login finishes first
        try:
            await prefetch_all()
        except Exception as e:
            logging.warning("prefetch failed: %s", e)
            (DATA / "prefetch.json").write_text(json.dumps({"at": time.time() - PREFETCH_EVERY + 3600}))


@asynccontextmanager
async def lifespan(app):
    await wa.start()
    # Log in at startup when CENGAGE_EMAIL/CENGAGE_PASSWORD are set. No extension needed.
    first = asyncio.create_task(wa.check())
    tasks = [asyncio.create_task(keepalive()), asyncio.create_task(prefetcher()),
             asyncio.create_task(ext.loop(lambda refresh: assignments(int(refresh))))]
    yield
    for t in tasks:
        t.cancel()
    await wa.stop()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def auth(request: Request, call_next):
    p = request.url.path
    if p.startswith("/api/") and p != "/api/login" and request.method != "OPTIONS":
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        ok = hmac.compare_digest(request.cookies.get("ap_session", ""), SESSION)
        if not ok and bearer:
            ip = client_ip(request)
            if rate_limited(ip):
                return JSONResponse({"error": "too many attempts"}, status_code=429)
            ok = hmac.compare_digest(bearer, PASSWORD)
            if not ok:
                _fails[ip].append(time.time())
        if not ok:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    resp = await call_next(request)
    if p.startswith("/api/session"):
        resp.headers["Access-Control-Allow-Origin"] = "*"
    if not p.startswith("/api/") and "cache-control" not in resp.headers:
        # App files: the browser must check for a new version (ETag) before it uses a cached copy.
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def cache_path(name):
    return DATA / "cache" / f"{re.sub(r'[^0-9A-Za-z_-]', '', name)}.json"


def read_cache(name):
    f = cache_path(name)
    return json.loads(f.read_text()) if f.exists() else None


def write_cache(name, data):
    cache_path(name).write_text(json.dumps(data))
    return data


class Login(BaseModel):
    password: str


@app.post("/api/login")
async def login(body: Login, request: Request, response: Response):
    ip = client_ip(request)
    if rate_limited(ip):
        raise HTTPException(429, "Too many attempts. Wait 15 minutes.")
    if not hmac.compare_digest(body.password, PASSWORD):
        _fails[ip].append(time.time())
        raise HTTPException(401, "Wrong password")
    # "auto": Secure cookie on HTTPS (nginx sends X-Forwarded-Proto), plain cookie on LAN http.
    mode = os.environ.get("COOKIE_SECURE", "auto")
    secure = request.url.scheme == "https" if mode == "auto" else mode == "1"
    response.set_cookie("ap_session", SESSION, max_age=90 * 86400, httponly=True, samesite="strict",
                        secure=secure)
    return {"ok": True}


@app.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie("ap_session")
    return {"ok": True}


@app.get("/api/status")
async def status():
    ok = await wa.check()
    return {"loggedIn": ok, "lastCheck": wa.last_check, "error": "" if ok else wa.last_error,
            "passwordLogin": bool(os.environ.get("CENGAGE_EMAIL") and os.environ.get("CENGAGE_PASSWORD")),
            "hasScreenshot": (DATA / "login-error.png").exists()}


@app.get("/api/login-error.png")
async def login_error_png():
    """Screenshot of the last failed Cengage login, for debugging."""
    f = DATA / "login-error.png"
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "no-store"})


class Session(BaseModel):
    cookies: list


@app.options("/api/session")
async def session_preflight():
    return Response(headers={"Access-Control-Allow-Origin": "*",
                             "Access-Control-Allow-Headers": "authorization, content-type",
                             "Access-Control-Allow-Methods": "POST"})


@app.post("/api/session")
async def set_session(body: Session):
    """The browser extension sends your WebAssign cookies here."""
    try:
        ok = await wa.set_cookies(body.cookies)
    except Exception as e:
        logging.exception("set_cookies failed")
        raise HTTPException(400, f"Could not use the cookies: {e}")
    return {"loggedIn": ok, "error": "" if ok else wa.last_error, "count": len(body.cookies)}


class Creds(BaseModel):
    email: str | None = None
    password: str | None = None


@app.post("/api/wa-login")
async def wa_login(body: Creds):
    ok = await wa.login(body.email, body.password)
    return {"loggedIn": ok, "error": "" if ok else wa.last_error}


def need_login():
    if not wa.logged_in:
        raise HTTPException(409, "WebAssign session expired. " + (wa.last_error or
                            "Send a new session from the browser extension."))


@app.get("/api/assignments")
async def assignments(refresh: int = 0):
    cached = read_cache("_list")
    if cached and not refresh:
        return cached
    try:
        return write_cache("_list", await asyncio.wait_for(wa.list_assignments(), 150))
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        logging.exception("list failed")
        raise HTTPException(502, f"Could not read the assignment list: {e}")


@app.get("/api/assignments/{dep}")
async def assignment(dep: str, refresh: int = 0):
    if not dep.isdigit():
        raise HTTPException(400)
    cached = read_cache(dep)
    if cached and not refresh:
        return cached
    try:
        return write_cache(dep, await asyncio.wait_for(wa.get_assignment(dep), 120))
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        logging.exception("assignment %s failed", dep)
        raise HTTPException(502, f"Could not load the assignment: {e}")


class ExtSettings(BaseModel):
    enabled: bool = False
    hours: float = 12


class ExtOverride(BaseModel):
    enabled: bool | None = None
    hours: float | None = None
    clear: bool = False


def ext_view(dep=None):
    s = ext.state()
    out = {"auto": s["auto"], "overrides": s["overrides"], "log": s["log"]}
    if dep:
        on, hours = ext.rule(s, dep)
        out.update(dep=dep, enabled=on, hours=hours, override=s["overrides"].get(dep), last=s["log"].get(dep))
    return out


@app.get("/api/extensions")
async def extensions_get():
    return ext_view()


@app.put("/api/extensions")
async def extensions_put(body: ExtSettings):
    ext.set_settings(auto=body.model_dump())
    return ext_view()


@app.get("/api/assignments/{dep}/extension")
async def extension_get(dep: str):
    if not dep.isdigit():
        raise HTTPException(400)
    out = ext_view(dep)
    try:
        out["info"] = await wa.extension_info(dep)
    except Exception as e:
        out["info"], out["error"] = None, str(e)
    return out


@app.put("/api/assignments/{dep}/extension")
async def extension_put(dep: str, body: ExtOverride):
    if not dep.isdigit():
        raise HTTPException(400)
    ext.set_settings(dep=dep, override=None if body.clear else body.model_dump(exclude_none=True, exclude={"clear"}))
    return ext_view(dep)


@app.post("/api/assignments/{dep}/extension")
async def extension_request(dep: str):
    """Request WebAssign's automatic extension now."""
    if not dep.isdigit():
        raise HTTPException(400)
    await wa.check()
    need_login()
    try:
        res = await ext.request(dep)
    except ValueError as e:
        raise HTTPException(409, str(e))
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        logging.exception("extension %s failed", dep)
        ext.record(dep, False, str(e)[:300], False)
        raise HTTPException(502, str(e))
    try:
        write_cache("_list", await asyncio.wait_for(wa.list_assignments(), 150))  # new due date
    except Exception as e:
        logging.warning("list refresh after extension: %s", e)
    return {**ext_view(dep), **res}


@app.get("/api/assignment-error.png")
async def assignment_error_png():
    """Screenshot of the last assignment page that had no questions, for debugging."""
    f = DATA / "assignment-error.png"
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "no-store"})


class Submit(BaseModel):
    answers: dict
    action: str = "submit"


@app.post("/api/assignments/{dep}/questions/{box}")
async def submit(dep: str, box: str, body: Submit):
    if not dep.isdigit() or not re.fullmatch(r"question\d+_\d+", box):
        raise HTTPException(400)
    for k in body.answers:
        if not re.fullmatch(r"R[A-Z]_[\d_]+", k):
            raise HTTPException(400, f"bad answer name {k}")
    if not body.answers:
        raise HTTPException(400, "No answers to send. This question has no answer boxes that AssignPad knows.")
    await wa.check()
    need_login()
    try:
        data = await wa.submit(dep, box, body.answers, body.action)
    except Exception as e:
        logging.exception("submit failed")
        raise HTTPException(502, str(e))
    return write_cache(dep, data)


@app.post("/api/assignments/{dep}/questions/{box}/practice")
async def practice_start(dep: str, box: str):
    m = re.fullmatch(r"question\d+_(\d+)", box)
    if not dep.isdigit() or not m:
        raise HTTPException(400)
    await wa.check()
    need_login()
    try:
        return await asyncio.wait_for(wa.practice_start(dep, int(m.group(1)) + 1), 90)
    except Exception as e:
        logging.exception("practice failed")
        raise HTTPException(502, f"Could not open a practice version: {str(e).splitlines()[0] if str(e) else type(e).__name__}")


class Practice(BaseModel):
    answers: dict


@app.post("/api/practice/{pid}")
async def practice_check(pid: str, body: Practice):
    for k in body.answers:
        if not re.fullmatch(r"R[A-Z]_[\d_]+", k):
            raise HTTPException(400, f"bad answer name {k}")
    try:
        return await wa.practice_check(pid, body.answers)
    except KeyError as e:
        raise HTTPException(410, str(e.args[0]))
    except Exception as e:
        logging.exception("practice check failed")
        raise HTTPException(502, f"Check failed: {e}")


@app.get("/api/submit-error.png")
async def submit_error_png():
    """Screenshot of the WebAssign page when the last submit failed, for debugging."""
    f = DATA / "submit-error.png"
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "no-store"})


@app.get("/api/proxy")
async def proxy(u: str):
    if not host_allowed(u):
        raise HTTPException(400, "host not allowed")
    body, ctype, code = await wa.proxy(u)
    return Response(body, status_code=code, media_type=ctype,
                    headers={"Cache-Control": "private, max-age=604800"})


if DEV:
    @app.post("/dev/fixture/{name}")
    async def fixture(name: str, request: Request):
        d = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{re.sub(r'[^0-9A-Za-z_-]', '', name)}.html").write_bytes(await request.body())
        return Response("ok", headers={"Access-Control-Allow-Origin": "*",
                                       "Access-Control-Allow-Private-Network": "true"})

    @app.options("/dev/fixture/{name}")
    async def fixture_pre(name: str):
        return Response(headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*",
                                 "Access-Control-Allow-Methods": "POST",
                                 "Access-Control-Allow-Private-Network": "true"})


@app.get("/sw.js")
async def sw():
    return FileResponse(STATIC / "sw.js", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
