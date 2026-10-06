"""Refund agent on ADK with Resume enabled, a SQLite ledger and injectable crashes.

Tool variants for issue_refund:
  naive        appends a refund row every time it runs
  key_call_id  uses the framework's function call id as an idempotency key
  key_order    uses a business key, one refund per order, as an idempotency key

Crash points, set with CRASH_AT in the environment of the first process only:
  before_tool   just before issue_refund runs
  after_effect  inside issue_refund, after the ledger write commits, before it returns
  after_tool    after issue_refund returns, before the agent continues
  before_reply  when the model is called to write the reply after the refund
"""
import os
import sqlite3
import time

from google.adk.agents import LlmAgent
from google.adk.apps.app import App, ResumabilityConfig
from google.adk.tools import ToolContext

ORDERS = {"O-1001": {"order_id": "O-1001", "customer": "Ana Ruiz", "item": "Ceramic teapot", "amount_usd": 40, "status": "delivered_damaged"}}

INSTRUCTION = """You are a refund assistant for an online shop.
When a customer asks for a refund, look up the order with get_order, then issue a full refund with issue_refund using the order amount, then confirm to the customer in one short sentence.
Issue at most one refund per order."""


def ledger_conn(path):
    c = sqlite3.connect(path, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS refunds (id INTEGER PRIMARY KEY, order_id TEXT, amount_usd INTEGER, idem_key TEXT UNIQUE, ts REAL)")
    return c


def crash(point):
    if os.environ.get("CRASH_AT") == point:
        os._exit(137)


def build_app(variant, ledger_path, model):
    def get_order(order_id: str) -> dict:
        """Look up an order by id."""
        return ORDERS.get(order_id, {"error": "order not found"})

    def issue_refund(order_id: str, amount_usd: int, tool_context: ToolContext) -> dict:
        """Refund an order. Moves money back to the customer."""
        if variant == "naive":
            key = None
        elif variant == "key_call_id":
            key = f"call:{tool_context.function_call_id}"
        elif variant == "key_order":
            key = f"refund:{order_id}"
        else:
            raise ValueError(variant)
        c = ledger_conn(ledger_path)
        try:
            cur = c.execute("INSERT OR IGNORE INTO refunds (order_id, amount_usd, idem_key, ts) VALUES (?,?,?,?)",
                            (order_id, amount_usd, key, time.time()))
            c.commit()
            duplicate = cur.rowcount == 0
        finally:
            c.close()
        crash("after_effect")
        if duplicate:
            return {"status": "already_refunded", "order_id": order_id}
        return {"status": "refunded", "order_id": order_id, "amount_usd": amount_usd}

    def before_tool(tool, args, tool_context):
        if tool.name == "issue_refund":
            crash("before_tool")
        return None

    def after_tool(tool, args, tool_context, tool_response):
        if tool.name == "issue_refund":
            crash("after_tool")
        return None

    def before_model(callback_context, llm_request):
        last = llm_request.contents[-1] if llm_request.contents else None
        if last and any(getattr(p, "function_response", None) and p.function_response.name == "issue_refund" for p in (last.parts or [])):
            crash("before_reply")
        return None

    agent = LlmAgent(
        name="refund_agent", model=model, instruction=INSTRUCTION, tools=[get_order, issue_refund],
        before_tool_callback=before_tool, after_tool_callback=after_tool, before_model_callback=before_model,
    )
    return App(name="refunds", root_agent=agent, resumability_config=ResumabilityConfig(is_resumable=True))
