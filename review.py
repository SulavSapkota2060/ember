"""Independent claim checking. Pure code: no LLM. Used by the reviewer subagent's tool and testable on its own.

check_numbers(text, evidence) finds every number in a draft that does NOT appear in the evidence bundle (the actual tool
outputs). It flags, it does not judge: a sum or difference of two evidence numbers shows up as unmatched and the LLM
reviewer decides whether the arithmetic is fine.
Ignored on purpose: years, building ids appear in the evidence anyway, and small whole numbers (0-10) that are list
markers or counts ("3 reasons", "zone 1")."""
import re

NUM = re.compile(r"(?<![\w.])[-−–]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
ALWAYS_OK = {100.0}  # "x / 100" scale


def _walk(obj, out):
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        out.append(abs(float(obj)))
    elif isinstance(obj, str):
        for m in NUM.finditer(obj):
            out.append(abs(float(m.group().replace(",", "").replace("−", "-").replace("–", "-"))))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _walk(k, out)
            _walk(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk(v, out)


def evidence_numbers(evidence) -> list[float]:
    out: list[float] = []
    _walk(evidence, out)
    return out


def check_numbers(text: str, evidence) -> dict:
    ev = evidence_numbers(evidence)
    checked, unmatched = 0, []
    for m in NUM.finditer(text):
        raw = m.group().replace(",", "").replace("−", "-").replace("–", "-")
        try:
            x = abs(float(raw))
        except ValueError:
            continue
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        if decimals == 0 and (x <= 10 or 1900 <= x <= 2100) or x in ALWAYS_OK:
            continue
        checked += 1
        if not any(abs(round(v, decimals) - x) < 1e-6 for v in ev):
            s = max(0, m.start() - 25)
            unmatched.append({"number": m.group(), "context": text[s:m.end() + 15].replace("\n", " ")})
    return {"checked": checked, "unmatched_count": len(unmatched), "unmatched": unmatched[:12]}
