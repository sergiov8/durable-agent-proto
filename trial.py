"""Run crash-and-recover trials and write one JSON line per trial.

  python trial.py --variants naive key_order --modes resume restart \
      --crash-points after_effect --trials 2 --out results/pilot.jsonl
"""
import argparse
import asyncio
import json
import os
import random
import sqlite3
import sys
import tempfile
import time

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))


async def run_phase(args, env, timeout=240):
    p = await asyncio.create_subprocess_exec(PY, os.path.join(HERE, "worker.py"), *args, env=env, cwd=HERE,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        return -9, {}, "timeout"
    text = out.decode(errors="replace")
    res = next((l[7:] for l in text.splitlines() if l.startswith("RESULT ")), None)
    err = None if res else text[-600:]
    return p.returncode, (json.loads(res) if res else {}), err


def ledger_rows(d):
    path = os.path.join(d, "ledger.db")
    if not os.path.exists(path):
        return 0
    try:
        return sqlite3.connect(path).execute("SELECT count(*) FROM refunds").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


async def one(variant, mode, point, trial, a, sem):
    async with sem:
        d = tempfile.mkdtemp(prefix="dur_")
        base = dict(os.environ)
        base.pop("CRASH_AT", None)
        t0 = time.time()
        rc1, r1, e1 = await run_phase(["first", "--dir", d, "--variant", variant, "--model", a.model], {**base, "CRASH_AT": point})
        rows_after_crash = ledger_rows(d)
        crashed = rc1 == 137
        if point == "none":
            # Control: no crash and no recovery, the first run's own reply is the outcome.
            rc2, r2, e2 = rc1, r1, e1
        else:
            rc2, r2, e2 = await run_phase(["recover", "--dir", d, "--variant", variant, "--mode", mode, "--model", a.model], base)
        rows = ledger_rows(d)
        rec = {
            "variant": variant, "mode": mode, "crash_point": point, "trial": trial, "crashed": crashed,
            "first_rc": rc1, "recover_rc": rc2, "rows_after_crash": rows_after_crash, "rows_final": rows,
            "duplicate": rows > 1, "missing": rows == 0, "events_before_recovery": r2.get("events_before_recovery"),
            "mode_used": r2.get("mode_used"), "note": r2.get("note"), "final_text": r2.get("final_text", "")[:300],
            "error": e2 if rc2 != 0 else None, "first_error": e1 if (rc1 not in (0, 137)) else None,
            "seconds": round(time.time() - t0, 1), "model": a.model,
        }
        with open(a.out, "a") as f:
            f.write(json.dumps(rec) + "\n")
        print(f"{variant:12s} {mode:8s} {point:13s} t{trial} crashed={crashed} rows {rows_after_crash}->{rows} "
              f"{'ERR' if rec['error'] or rec['first_error'] else ''} {rec['seconds']}s", flush=True)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", nargs="+", default=["naive", "key_call_id", "key_order"])
    ap.add_argument("--modes", nargs="+", default=["resume", "restart"])
    ap.add_argument("--crash-points", nargs="+", default=["before_tool", "after_effect", "after_tool", "before_reply"])
    ap.add_argument("--trials", type=int, default=2)
    ap.add_argument("--model", default="gemini-3.5-flash")
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--out", default="results/results.jsonl")
    a = ap.parse_args()
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        raise SystemExit("Set GOOGLE_CLOUD_PROJECT to a project with Vertex AI enabled.")
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    sem = asyncio.Semaphore(a.concurrency)
    jobs = [one(v, m, p, t, a, sem) for t in range(a.trials) for v in a.variants for m in a.modes for p in a.crash_points]
    random.shuffle(jobs)
    await asyncio.gather(*jobs)


if __name__ == "__main__":
    asyncio.run(main())
