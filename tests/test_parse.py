"""Run: python -m pytest tests  (or python tests/test_parse.py)"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
from parse import parse_assignment  # noqa: E402

URL = "https://www.webassign.net/web/Student/Assignment-Responses/last?dep=99999"


def load():
    return parse_assignment((ROOT / "tests/fixtures/sample.html").read_text(), URL)


def test_parse_sample():
    a = load()
    assert a["name"] == "Sample Systems"
    assert len(a["questions"]) == 2
    q1, q2 = a["questions"]
    assert q1["box"] == "question111_0" and q1["canSubmit"]
    assert q1["submissions"] == "1/5"
    p = q1["parts"][0]
    assert p["name"] == "RA_111_0_0_5" and p["kind"] == "math" and p["mark"] == "incorrect"
    assert p["value"].startswith("<math")
    assert 'data-name="RA_111_0_0_5"' in q1["html"]
    assert "mathtype-wrapper" not in q1["html"] and "onclick" not in q1["html"]
    assert "ap-tex" in q1["html"]
    r = q2["parts"][0]
    assert r["kind"] == "radio" and r["value"] == "1" and r["mark"] == "correct"
    assert "/api/proxy?u=https%3A%2F%2Fwww.webassign.net%2Fstatic%2Feq1.gif" in q2["html"]
    assert a["styles"] and a["styles"][0].startswith("/api/proxy")


def test_watex_structure():
    """Real WebAssign layout: fraction tables and paren images around the answer box stay."""
    a = parse_assignment((ROOT / "tests/fixtures/watex.html").read_text(encoding="utf-8"), URL)
    q = a["questions"][0]
    assert [p["name"] for p in q["parts"]] == ["RA_333_0_0_7"]
    h = q["html"]
    assert h.count('class="watexfraction"') == 2
    assert h.index("leftparen2.gif") < h.index('data-name="RA_333_0_0_7"') < h.index("rightparen2.gif")
    # watex.css is added even when the page only links the big CSS bundle.
    assert a["styles"] == ["/api/proxy?u=https%3A%2F%2Fwww.webassign.net%2Fout%2F"
                           "wacachea4f8446e5bade082a0ba9b81b4706499%2Fcss%2Fquestions%2Fwatex.css"]


def test_no_secrets():
    s = json.dumps(load())
    assert "SECRET" not in s


if __name__ == "__main__":
    test_parse_sample(); test_no_secrets()
    print(json.dumps(load(), indent=1)[:2500])


def test_number_and_matrix_boxes():
    html = (ROOT / "tests/fixtures/types.html").read_text(encoding="utf-8")
    q = parse_assignment(html, "https://www.webassign.net/web/Student/x")["questions"][0]
    parts = {p["name"]: p for p in q["parts"]}
    assert parts["RN_11_0_0_5"]["kind"] == "text" and parts["RN_11_0_0_5"]["enabled"]
    assert parts["RN_11_0_1_5"]["enabled"] is False and parts["RN_11_0_1_5"]["value"] == "3"
    m = parts["RB_11_0_2_5"]
    assert (m["kind"], m["rows"], m["cols"]) == ("matrix", 2, 3)
    assert (parts["RB_11_0_3_5"]["rows"], parts["RB_11_0_3_5"]["cols"]) == (2, 2)
    assert parts["RC_11_0_4_5"]["kind"] == "select"
    for name in parts:
        assert f'data-name="{name}"' in q["html"] or name.startswith("RC_")
    assert "matrix_input" not in q["html"] and "<input" not in q["html"].replace('type="radio"', "")


def test_calcpad_select_all_dropdown_graph():
    """RQ calcPad math, select-all checkboxes with a hidden twin, drop-down and graph boxes, marks by <name>_mark."""
    from parse import hidden_fields
    html = (ROOT / "tests/fixtures/types2.html").read_text(encoding="utf-8")
    qs = parse_assignment(html, URL)["questions"]
    rq = qs[0]["parts"][0]
    assert (rq["name"], rq["kind"], rq["mark"], rq["enabled"]) == ("RQ_21_0_0_7", "math", "incorrect", True)
    assert rq["value"].startswith("<math") and 'data-name="RQ_21_0_0_7"' in qs[0]["html"]
    parts = {p["name"]: p for p in qs[1]["parts"]}
    assert parts["RS_22_1_0_8"]["kind"] == "checkbox" and parts["RS_22_1_0_8"]["value"] == ["1"]
    assert parts["RS_22_1_0_8"]["mark"] == "correct"
    assert parts["RC_22_1_1_8"]["kind"] == "select" and parts["RC_22_1_1_8"]["value"] == "1"
    assert parts["RC_22_1_1_8"]["mark"] == "incorrect"
    assert 'type="hidden"' not in qs[1]["html"]
    g = qs[2]["parts"][0]
    assert (g["kind"], g["enabled"], g["mark"]) == ("graph", True, None)
    assert 'data-name="RG_23_2_0_9"' in qs[2]["html"] and "graphpad_div" not in qs[2]["html"]
    assert ("RS_22_1_0_8", "1") in hidden_fields(html)


def test_parse_due():
    from parse import parse_due
    assert parse_due("Sunday, November 1, 2026 at 10:59 PM CST") == 1793595540
    assert parse_due("Sun, Nov 1, 2026, 10:59 PM CST") == 1793595540
    assert parse_due("Friday, October 23, 2026 at 12:12 AM CDT") == 1792732320
    assert parse_due("Mon, Aug 24") is None
