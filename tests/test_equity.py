"""Plain-assert tests for equity parsing and failure handling. Run: .venv/bin/python -I tests/test_equity.py"""
import sys
sys.path.insert(0, ".")
import equity

ok = equity._parse({"tract": 6037190100, "CIscore": 43.06, "CIscoreP": 79.589, "CIdecile": 8, "PollutionP": 70.04, "PopCharP": 81.2, "povP": 69.16})
assert ok["status"] == "ok" and ok["burden_percentile"] == 79.6 and ok["tract"] == "6037190100" and ok["burden_decile"] == 8, ok
assert equity._parse({"tract": 6037000100, "CIscoreP": None})["status"] == "no_score", "unscored tract must not become 0"
assert [equity.ordinal(n) for n in (1, 2, 3, 11, 12, 13, 21, 22, 79.6)] == ["1st", "2nd", "3rd", "11th", "12th", "13th", "21st", "22nd", "80th"]
# a network failure must come back as status 'error', not raise and not look like a score
orig = equity._lookup
def boom(lat, lon): raise ConnectionError("down")
equity._lookup = boom
assert equity.lookup(34.0, -118.0)["status"] == "error"
equity._lookup = orig
print("equity tests passed")
