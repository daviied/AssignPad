"""Parse WebAssign assignment HTML into a clean structure for the AssignPad UI."""
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, quote

from bs4 import BeautifulSoup

BASE = "https://www.webassign.net"
# Hidden inputs that hold session data. Never send these to the client.
SECRET_INPUTS = {"sid", "UserPass", "user", "host", "usertype"}

# US zone names WebAssign prints after due times, as UTC offsets in hours.
_ZONES = {"UTC": 0, "GMT": 0, "EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6,
          "PST": -8, "PDT": -7, "AKST": -9, "AKDT": -8, "HST": -10}
_DUE = re.compile(r"([A-Z][a-z]{2,8})\.?\s+(\d{1,2}),?\s+(\d{4}),?\s+(?:at\s+)?(\d{1,2}):(\d{2})\s*([AP]M)"
                  r"(?:\s+([A-Z]{2,4})\b)?")


def parse_due(text: str):
    """'Sunday, November 1, 2026 at 10:59 PM CST' -> Unix time. No zone name = UTC. None if no time."""
    m = _DUE.search(text or "")
    if not m:
        return None
    mon, day, year, hh, mm, ap, zone = m.groups()
    try:
        month = datetime.strptime(mon[:3], "%b").month
    except ValueError:
        return None
    hour = int(hh) % 12 + (12 if ap == "PM" else 0)
    off = _ZONES.get(zone or "UTC", 0)
    return int(datetime(int(year), month, int(day), hour, int(mm),
                        tzinfo=timezone(timedelta(hours=off))).timestamp())


def proxy(url: str, page_url: str) -> str:
    if not url or url.startswith(("data:", "#", "javascript:")):
        return url
    return "/api/proxy?u=" + quote(urljoin(page_url, url), safe="")


def _cells(tr):
    return [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]


def parse_details(qbox):
    """Read the 'Question Part / Points / Submissions Used' table."""
    out = {}
    table = qbox.select_one(".questionPartDetails table")
    if not table:
        return out
    for tr in table.find_all("tr"):
        cells = _cells(tr)
        if len(cells) >= 2 and cells[0]:
            out[cells[0]] = cells[1:]
    return out


def _mark_near(el):
    """Find the correct/incorrect mark that follows an answer box."""
    for sib in el.find_all_next(limit=12):
        if sib.name in ("input", "select") and re.match(r"R[A-Z]_", str(sib.get("name", ""))):
            return None
        cls = " ".join(sib.get("class", []))
        src = sib.get("src", "") or ""
        if "mCorrect" in cls or "mCorrect" in src:
            return "correct"
        if "mIncorrect" in cls or "mIncorrect" in src or "mWrong" in src:
            return "incorrect"
    return None


def _mark_of(qbox, name, near):
    """WebAssign's mark for an answer box: its "<name>_mark" span, else a mark near the box."""
    m = qbox.find(id=f"{name}_mark")
    if m is not None:
        cls = " ".join(m.get("class", []))
        if "mCorrect" in cls:
            return "correct"
        if "mIncorrect" in cls or "mWrong" in cls:
            return "incorrect"
        if "waMark" in cls:
            return None
    return _mark_near(near)


def parse_question(qbox, page_url):
    qid_full = qbox.get("id", "")  # question5224578_0
    m = re.match(r"question(\d+)_(\d+)", qid_full)
    qid, pos = (m.group(1), int(m.group(2))) if m else (qid_full, 0)
    details = parse_details(qbox)
    content = qbox.select_one(".studentQuestionContent") or qbox
    parts = []

    # Math answer boxes (MathType / calcPad): RA_, RQ_, ... Value is MathML in a hidden input.
    for inp in content.select('input[name]'):
        name = inp["name"]
        if not re.match(r"R[A-Z]_", name) or not (name.startswith("RA_") or qbox.find(id=f"editable-math-{name}")):
            continue
        if (inp.get("type") or "text").lower() not in ("hidden", "text") or any(p["name"] == name for p in parts):
            continue
        settings = qbox.find(id=f"{name}_settings")
        part = {
            "name": name,
            "kind": "math",
            "value": inp.get("value", ""),
            "enabled": True,
            "mark": _mark_of(qbox, name, inp),
            "pad": None,
        }
        en = qbox.find(id=f"enabled_{name}")
        if en is not None and en.get("value") == "0":
            part["enabled"] = False
        if settings is not None:
            part["pad"] = settings.get("value")
        if inp.get("type") in ("text", None) and not qbox.find(id=f"editable-math-{name}"):
            part["kind"] = "text"
        ph = BeautifulSoup(f'<span class="ap-box" data-name="{name}"></span>', "html.parser").span
        editable = qbox.find(id=f"editable-math-{name}")
        wrap = editable.find_parent(class_="mathtype-wrapper") if editable else None
        (wrap or inp).insert_before(ph)
        if wrap:
            wrap.decompose()
        parts.append(part)

    # Matrix boxes: hidden input with "[a,b;c,d]" (rows split by ';'), edited in a grid of cells.
    for div in content.select('div.matrix_input[id^="matrix_"]'):
        name = div["id"][len("matrix_"):]
        inp = qbox.find("input", attrs={"name": name})
        if inp is None:
            continue
        value = inp.get("value", "")
        m = re.search(r"matrix_initialize\(\s*['\"]" + re.escape(name) + r"['\"]\s*,\s*(\d+)\s*,\s*(\d+)", str(qbox))
        cols, rows = (int(m.group(1)), int(m.group(2))) if m else (2, 2)
        if value.strip("[] "):
            cells = [r.split(",") for r in value.strip("[] ").split(";")]
            rows, cols = len(cells), max(len(r) for r in cells)
        en = qbox.find(id=f"enabled_{name}")
        parts.append({
            "name": name, "kind": "matrix", "value": value, "rows": rows, "cols": cols,
            "enabled": not (div.select_one("input[disabled]") or (en is not None and en.get("value") == "0")),
            "mark": _mark_of(qbox, name, inp), "pad": None,
        })
        holder = div.parent if div.parent and "qTextField" in div.parent.get("class", []) else div
        holder.replace_with(BeautifulSoup(f'<span class="ap-box" data-name="{name}"></span>', "html.parser").span)

    # Graphing tool (RG_): drawn by WebAssign's own graph pad. Shown as a box to answer in WebAssign.
    for div in content.select('div[id^="graphpad_div_"]'):
        name = div["id"][len("graphpad_div_"):]
        inp = qbox.find("input", attrs={"name": name})
        mode = qbox.find(id=f"graphpad_mode_{name}")
        parts.append({"name": name, "kind": "graph", "value": None,
                      "enabled": not (mode is not None and mode.get("value") == "disabled"),
                      "mark": _mark_of(qbox, name, inp or div), "pad": None})
        div.replace_with(BeautifulSoup(f'<span class="ap-box" data-name="{name}"></span>', "html.parser").span)

    # Plain text answer boxes: numeric (RN_), short text, etc. WebAssign reads their value directly.
    for inp in content.select('input[name]'):
        name = inp["name"]
        if (inp.get("type") or "text").lower() != "text" or not re.match(r"R[A-Z]_", name) \
                or any(p["name"] == name for p in parts):
            continue
        en = qbox.find(id=f"enabled_{name}")
        parts.append({
            "name": name, "kind": "text", "value": inp.get("value", ""),
            "enabled": not (inp.has_attr("disabled") or inp.has_attr("readonly") or (en is not None and en.get("value") == "0")),
            "mark": _mark_of(qbox, name, inp), "pad": None,
        })
        inp.replace_with(BeautifulSoup(f'<span class="ap-box" data-name="{name}"></span>', "html.parser").span)

    # Multiple choice (radio / checkbox) and dropdowns.
    groups = {}
    for inp in content.select('input[type="radio"], input[type="checkbox"]'):
        name = inp.get("name", "")
        if not name:
            continue
        g = groups.setdefault(name, {"name": name, "kind": inp["type"], "value": None,
                                     "enabled": not inp.has_attr("disabled"), "mark": None})
        if inp.has_attr("checked"):
            g["value"] = inp.get("value") if inp["type"] == "radio" else (g["value"] or []) + [inp.get("value")]
    for g in groups.values():
        last = content.select(f'input[name="{g["name"]}"]')[-1]
        g["mark"] = _mark_of(qbox, g["name"], last)
        parts.append(g)
    for sel in content.select("select[name]"):
        opt = sel.find("option", selected=True)
        parts.append({"name": sel["name"], "kind": "select", "value": opt.get("value") if opt else None,
                      "enabled": not sel.has_attr("disabled"), "mark": _mark_of(qbox, sel["name"], sel)})

    # Clean the content HTML. MathJax output (live page) is removed; its source scripts stay.
    for t in content.select("button, textarea.latex-source, .mathtype-overlay-trigger, iframe, object, embed, "
                            ".MathJax_Preview, .MathJax, .MathJax_Display, .MathJax_CHTML, mjx-container, "
                            ".MJX_Assistive_MathML"):
        t.decompose()
    for t in content.select('input[type="hidden"], input[type="submit"]'):
        t.decompose()
    for s in content.find_all("script"):
        typ = (s.get("type") or "").lower()
        if typ.startswith("math/tex"):
            span = BeautifulSoup("<span class='ap-tex'></span>", "html.parser").span
            span.string = ("\\displaystyle " if "display" in typ else "") + (s.string or "")
            s.replace_with(span)
        elif typ.startswith("math/mml"):
            s.replace_with(BeautifulSoup(s.string or "", "html.parser"))
        else:
            s.decompose()
    for el in content.find_all(True):
        for attr in list(el.attrs):
            if attr.lower().startswith("on"):
                del el[attr]
        if el.name in ("img", "source") and el.get("src"):
            el["src"] = proxy(el["src"], page_url)
            el.attrs.pop("srcset", None)
        if el.name == "a" and el.get("href"):
            el["href"] = urljoin(page_url, el["href"])
            el["target"] = "_blank"
            el["rel"] = "noopener"
        if el.name == "link" and el.get("href"):
            el["href"] = proxy(el["href"], page_url)

    used = details.get("Submissions Used", [])
    return {
        "qid": qid,
        "pos": pos,
        "box": qid_full,
        "number": pos + 1,
        "details": details,
        "submissions": used[0] if used else None,
        "html": content.decode_contents(),
        "parts": parts,
        "canSubmit": bool(qbox.select('input[name="submit"]')),
        "text": content.get_text(" ", strip=True)[:200],
    }


def form_fields(html: str):
    """Name/value pairs the first form on the page would post (hidden inputs, text, checked boxes, selects)."""
    form = BeautifulSoup(html, "html.parser").find("form")
    pairs = []
    if form is None:
        return pairs
    for el in form.find_all(["input", "select", "textarea"]):
        name = el.get("name")
        if not name or el.has_attr("disabled"):
            continue
        typ = (el.get("type") or "text").lower()
        if el.name == "select":
            opt = el.find("option", selected=True) or el.find("option")
            pairs.append((name, opt.get("value", opt.get_text()) if opt else ""))
        elif el.name == "textarea":
            pairs.append((name, el.get_text()))
        elif typ in ("radio", "checkbox"):
            if el.has_attr("checked"):
                pairs.append((name, el.get("value", "on")))
        elif typ not in ("submit", "button", "image", "reset", "file"):
            pairs.append((name, el.get("value", "")))
    return pairs


def hidden_fields(html: str):
    """(name, value) pairs of the form's hidden inputs."""
    form = BeautifulSoup(html, "html.parser").find("form")
    return {(el["name"], el.get("value", "")) for el in (form.find_all("input", type="hidden") if form else [])
            if el.get("name")}


def parse_assignment(html: str, page_url: str, selector: str = ".waQBox[id^=question]"):
    soup = BeautifulSoup(html, "html.parser")
    for name in SECRET_INPUTS:
        for el in soup.select(f'input[name="{name}"]'):
            el.decompose()
    asg = soup.select_one(".assignment[id^=assignment]")
    name = asg.get("data-assignment-name") if asg else None
    if not name:
        t = soup.title.get_text(strip=True) if soup.title else ""
        name = t.split(" - ")[0]
    styles = []
    for link in soup.select('link[rel~="stylesheet"]'):
        href = link.get("href", "")
        if re.search(r"/css/questions/|watex", href):
            styles.append(proxy(href, page_url))
    # Assignment pages load the math layout CSS inside a large bundle (student_screen_min.css).
    # Load watex.css itself from the same WebAssign version folder.
    if not any("watex.css" in s for s in styles):
        m = re.search(r"/(?:out|wastatic|static)/(wacache[0-9a-f]+)/", html)
        if m:
            styles.append(proxy(f"/out/{m.group(1)}/css/questions/watex.css", page_url))
    questions = [parse_question(q, page_url) for q in soup.select(selector)]
    return {"name": name, "styles": styles, "questions": questions}
