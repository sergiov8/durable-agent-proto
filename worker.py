"""One phase of one trial, run as its own process so a crash really kills it.

  python worker.py first   --dir D --variant V
  python worker.py recover --dir D --variant V --mode resume|restart
"""
import argparse
import asyncio
import json
import os

os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")

from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types

from agent import build_app

MESSAGE = "Hi, my order O-1001 arrived broken. Please refund it."
USER, SESSION = "u1", "s1"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["first", "recover"])
    ap.add_argument("--dir", required=True)
    ap.add_argument("--variant", required=True)
    ap.add_argument("--mode", default="resume")
    ap.add_argument("--model", default="gemini-3.5-flash")
    a = ap.parse_args()

    svc = DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{a.dir}/sessions.db")
    app = build_app(a.variant, f"{a.dir}/ledger.db", a.model)
    runner = Runner(app=app, session_service=svc)
    out = {"phase": a.phase}

    if a.phase == "first":
        await svc.create_session(app_name=app.name, user_id=USER, session_id=SESSION)
        msg = types.Content(role="user", parts=[types.Part(text=MESSAGE)])
        events = [e async for e in runner.run_async(user_id=USER, session_id=SESSION, new_message=msg)]
        out["completed_without_crash"] = True
        final = [e for e in events if e.is_final_response() and e.content and e.content.parts]
        out["final_text"] = "".join(p.text or "" for p in final[-1].content.parts) if final else ""
    else:
        s = await svc.get_session(app_name=app.name, user_id=USER, session_id=SESSION)
        inv = next((e.invocation_id for e in s.events if e.author == "user"), None)
        out["events_before_recovery"] = len(s.events)
        out["invocation_id"] = inv
        if a.mode == "resume":
            if inv is None:
                out["note"] = "no persisted user event, nothing to resume, falling back to restart"
                a.mode = "restart"
            else:
                events = [e async for e in runner.run_async(user_id=USER, session_id=SESSION, invocation_id=inv)]
        if a.mode == "restart":
            msg = types.Content(role="user", parts=[types.Part(text=MESSAGE)])
            events = [e async for e in runner.run_async(user_id=USER, session_id=SESSION, new_message=msg)]
        final = [e for e in events if e.is_final_response() and e.content and e.content.parts]
        out["final_text"] = "".join(p.text or "" for p in final[-1].content.parts) if final else ""
        out["mode_used"] = a.mode
    print("RESULT " + json.dumps(out))


asyncio.run(main())
