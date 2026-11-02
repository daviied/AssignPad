"""Extensions: request WebAssign's automatic extension, by hand or X hours after the due date."""
import asyncio
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger("ext")
MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
TZ = {"EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6, "PST": -8, "PDT": -7,
      "AKST": -9, "AKDT": -8, "HST": -10, "UTC": 0, "GMT": 0}
DUE = re.compile(r"([A-Za-z]{3})[a-z]*\.? (\d{1,2}),? (\d{4})?,?\s*(?:at )?(\d{1,2}):(\d{2})\s*(AM|PM)\s*([A-Z]{2,4})?", re.I)


def parse_due(text, now=None):
    """'Fri, Nov 6, 2026, 11:59 PM CST' -> unix time, or None. No year: the nearest one."""
    m = DUE.search(text or "")
    if not m or m.group(1).lower()[:3] not in MONTHS:
        return None
    mon, day = MONTHS[m.group(1).lower()[:3]], int(m.group(2))
    h, mi = int(m.group(4)) % 12 + (12 if m.group(6).upper() == "PM" else 0), int(m.group(5))
    tz = dt.timezone(dt.timedelta(hours=TZ.get((m.group(7) or "CST").upper(), -6)))
    now = now or time.time()
    years = [int(m.group(3))] if m.group(3) else [dt.datetime.fromtimestamp(now).year + d for d in (-1, 0, 1)]
    try:
        stamps = [dt.datetime(y, mon, day, h, mi, tzinfo=tz).timestamp() for y in years]
    except ValueError:
        return None
    return min(stamps, key=lambda t: abs(t - now))


def complete(score):
    """'100 / 100' or '100%' -> True. Unknown -> False."""
    m = re.search(r"([\d.]+)\s*/\s*([\d.]+)", score or "")
    if m and float(m.group(2)) > 0:
        return float(m.group(1)) >= float(m.group(2)) - 1e-9
    m = re.search(r"([\d.]+)\s*%", score or "")
    return bool(m) and float(m.group(1)) >= 100


class Extensions:
    def __init__(self, data: Path, wa):
        self.file = data / "extensions.json"
        self.wa = wa

    def state(self):
        s = json.loads(self.file.read_text()) if self.file.exists() else {}
        s.setdefault("auto", {"enabled": False, "hours": 12})
        s.setdefault("overrides", {})  # dep -> {"enabled": bool, "hours": number}
        s.setdefault("log", {})        # dep -> {"at", "ok", "message", "auto"}
        return s

    def save(self, s):
        self.file.write_text(json.dumps(s))
        return s

    def set_settings(self, auto=None, dep=None, override=None):
        s = self.state()
        if auto is not None:
            s["auto"] = {"enabled": bool(auto.get("enabled")), "hours": max(0.0, min(240.0, float(auto.get("hours", 12))))}
        if dep is not None:
            if override is None:
                s["overrides"].pop(dep, None)
            else:
                o = {}
                if "enabled" in override:
                    o["enabled"] = bool(override["enabled"])
                if override.get("hours") is not None:
                    o["hours"] = max(0.0, min(240.0, float(override["hours"])))
                s["overrides"][dep] = o
        return self.save(s)

    def rule(self, s, dep):
        o = s["overrides"].get(dep, {})
        return o.get("enabled", s["auto"]["enabled"]), o.get("hours", s["auto"]["hours"])

    def record(self, dep, ok, message, auto, final=True):
        s = self.state()
        s["log"][dep] = {"at": time.time(), "ok": ok, "message": message, "auto": auto, "final": ok or final}
        self.save(s)

    async def request(self, dep, auto=False):
        """Request the automatic extension. Refused when WebAssign has only instructor-approved ones."""
        info = await self.wa.extension_info(dep)
        if not info.get("available") or not info.get("active"):
            raise ValueError("WebAssign has no extensions for this assignment.")
        if info.get("extension") not in ("automated", "both"):
            raise ValueError("This assignment has only instructor-approved extensions. AssignPad uses only automatic ones.")
        if not info.get("unlimited_requests") and int(info.get("requests_remaining") or 0) <= 0:
            raise ValueError("No extension requests left for this assignment.")
        res = await self.wa.request_extension(dep)
        msg = res.get("message") or res.get("result") or "Extension granted."
        self.record(dep, True, str(msg)[:300], auto)
        return {"result": res, "info": await self.wa.extension_info(dep)}

    async def due_now(self, lst, now=None):
        """Assignments whose auto extension time has come."""
        now = now or time.time()
        s = self.state()
        out = []
        for a in lst.get("assignments", []):
            dep = a["dep"]
            on, hours = self.rule(s, dep)
            due = parse_due(a.get("due"), now)
            if not on or due is None or s["log"].get(dep, {}).get("final"):
                continue
            # Wait the set hours; give up 7 days after the due date (WebAssign's window is shorter).
            if due + hours * 3600 <= now < due + 7 * 86400 and not complete(a.get("score")):
                out.append(dep)
        return out

    async def run_once(self, get_list):
        lst = await get_list(False)
        if not await self.due_now(lst):
            return
        lst = await get_list(True)  # fresh scores before requesting
        for dep in await self.due_now(lst):
            try:
                await self.request(dep, auto=True)
                log.info("auto extension %s: granted", dep)
            except ValueError as e:  # WebAssign will not give one: do not ask again
                self.record(dep, False, str(e)[:300], True)
                log.warning("auto extension %s: %s", dep, e)
            except Exception as e:  # session or network: try again next check
                self.record(dep, False, str(e)[:300], True, final=False)
                log.warning("auto extension %s: %s", dep, e)

    async def loop(self, get_list, every=600):
        while True:
            await asyncio.sleep(every)
            try:
                if self.state()["auto"]["enabled"] or any(o.get("enabled") for o in self.state()["overrides"].values()):
                    await self.run_once(get_list)
            except Exception as e:
                log.warning("auto extension check failed: %s", e)
