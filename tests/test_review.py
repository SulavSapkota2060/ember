"""Plain-assert tests for review.check_numbers. Run: .venv/bin/python -I tests/test_review.py"""
import sys
sys.path.insert(0, ".")
import review

ev = {"score": 29, "factors": [{"evidence": ["tree cover zone 1 6.37%, zone 2 16.85%"]}], "tonnes": 4401,
      "tenants": [{"sqft": 13478.0}], "fixes": [{"points_removed": 2}]}
ok = review.check_numbers("Score 29/100, zone 1 6.37% and zone 2 16.85%; 4,401 t; 13,478 sq ft; -2 points; built 2011.", ev)
assert ok["unmatched_count"] == 0 and ok["checked"] >= 5, ok
bad = review.check_numbers("Score 41, zone 1 63%, 4,401 t.", ev)
assert [u["number"] for u in bad["unmatched"]] == ["41", "63"], bad
assert review.check_numbers("Zone 1 is 6.4%.", ev)["unmatched_count"] == 0, "rounding to fewer decimals is fine"
assert review.check_numbers("Zone 1 is 6.5%.", ev)["unmatched_count"] == 1, "wrong rounding is flagged"
assert review.check_numbers("3 reasons, zone 2, item 7", ev)["checked"] == 0, "small ints ignored"
print("review tests passed")
