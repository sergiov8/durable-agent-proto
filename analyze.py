"""Summarize crash-and-recover trials.

  python analyze.py results/main.jsonl
"""
import collections
import json
import re
import sys

CLAIM = re.compile(r"\b(refund(ed)?|processed|issued)\b", re.I)
DENY = re.compile(r"\b(not|unable|couldn't|could not|failed|already)\b", re.I)


def claims_refund(text):
    return bool(CLAIM.search(text or "")) and not DENY.search(text or "")


def outcome(r):
    expected_crash = r["crash_point"] != "none"
    if r["error"] or r["first_error"] or r["crashed"] != expected_crash:
        return "invalid"
    if r["rows_final"] > 1:
        return "duplicate"
    if r["rows_final"] == 0:
        return "lost_but_confirmed" if claims_refund(r["final_text"]) else "lost"
    return "correct"


def main(path):
    rs = [json.loads(l) for l in open(path)]
    for r in rs:
        r["outcome"] = outcome(r)
    print(f"trials={len(rs)} invalid={sum(r['outcome'] == 'invalid' for r in rs)}")
    order = ["correct", "duplicate", "lost", "lost_but_confirmed", "invalid"]

    def table(keyf, title):
        g = collections.defaultdict(list)
        for r in rs:
            g[keyf(r)].append(r)
        print(f"\n{title}")
        print(f"  {'group':40s} {'n':>3s} " + " ".join(f"{o[:12]:>12s}" for o in order))
        for k in sorted(g):
            c = collections.Counter(r["outcome"] for r in g[k])
            print(f"  {' / '.join(k):40s} {len(g[k]):3d} " + " ".join(f"{c[o]:12d}" for o in order))

    table(lambda r: (r["mode"], r["variant"]), "By recovery mode and tool")
    table(lambda r: (r["mode"], r["variant"], r["crash_point"]), "By recovery mode, tool and crash point")


if __name__ == "__main__":
    main(sys.argv[1])
