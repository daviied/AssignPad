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


def test_no_secrets():
    s = json.dumps(load())
    assert "SECRET" not in s


if __name__ == "__main__":
    test_parse_sample(); test_no_secrets()
    print(json.dumps(load(), indent=1)[:2500])
