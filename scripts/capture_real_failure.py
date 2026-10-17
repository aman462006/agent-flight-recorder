"""Capture a REAL agent failure with the flight recorder (live, not synthetic).

The reviewer's question was fair: "has it ever caught a real failure?"
This script runs a real LLM agent (Groq, gpt-oss-20b) whose job is to find a
payment record in a transaction ledger. The ledger genuinely has no such
record, the agent's searches all come back empty, and the agent then
answers the customer with "no matching transactions".

The recorder captures the whole thing with real token counts, real
latencies, and the investigator fires empty_search_conclusion on a run
that was never scripted to fail - which is the point.

Streamed to the hosted API with tags that say exactly what it is:
    origin: real-failure-capture
    model:  openai/gpt-oss-20b (Groq)

Run:  python3 scripts/capture_real_failure.py [api_url]
"""

from __future__ import annotations

import json
import os
import sys

from groq import Groq

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from afrec import Recorder, HttpStore  # noqa: E402

API_URL = sys.argv[1] if len(sys.argv) > 1 else "https://afrec-api.onrender.com"
MODEL = "openai/gpt-oss-20b"

LEDGER = [
    {"id": "TX-1001", "ref": "PAY-771", "amount": 12500, "state": "SETTLED"},
    {"id": "TX-1002", "ref": "PAY-772", "amount": 4300, "state": "SETTLED"},
    {"id": "TX-1003", "ref": "PAY-773", "amount": 900, "state": "REFUNDED"},
    # PAY-404 does not exist — the corpus genuinely has no answer.
]


def search_transactions(query: str, limit: int = 5):
    """A real, working tool. It just never finds PAY-404."""
    hits = [row for row in LEDGER
            if query.lower() in row["ref"].lower() or query.lower() in row["id"].lower()]
    return hits[:limit]


def main() -> None:
    client = Groq()  # GROQ_API_KEY from the environment
    store = HttpStore(API_URL)
    rec = Recorder(store=store)

    tools = [{
        "type": "function",
        "function": {
            "name": "search_transactions",
            "description": "Search the transaction ledger by payment reference or transaction id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "payment reference or transaction id, e.g. PAY-771"},
                    "limit": {"type": "integer", "description": "max rows to return", "default": 5},
                },
                "required": ["query"],
            },
        },
    }]

    messages = [
        {"role": "system", "content":
            "You are a payments support agent. Find the requested payment in the "
            "transaction ledger using search_transactions, then give the customer "
            "a short final answer about the payment status."},
        {"role": "user", "content": "Please check the status of my payment PAY-404 and tell me what happened to it."},
    ]

    with rec.run(
        "real: agent fails to find payment PAY-404 (empty-search failure, live Groq capture)",
        tags={"origin": "real-failure-capture", "model": MODEL, "provider": "groq",
              "note": "real agent run, deliberately pointed at a record the ledger does not contain; not a seeded demo"},
    ) as run:
        def llm_round():
            with run.llm_call(MODEL, messages) as span:
                resp = client.chat.completions.create(model=MODEL, messages=messages, tools=tools)
                msg = resp.choices[0].message
                usage = resp.usage
                span.result(
                    {"text": msg.content or "",
                     "tool_calls": [t.function.model_dump() for t in (msg.tool_calls or [])]},
                    input_tokens=usage.prompt_tokens,
                    output_tokens=(usage.completion_tokens or 0) or None,
                    cached_tokens=0, model=MODEL,
                )
            return msg

        msg = llm_round()
        rounds = 0
        while msg.tool_calls and rounds < 4:
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
            })
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                with run.tool_call("search_transactions", args) as tspan:
                    result = search_transactions(**args) if "query" in args else []
                    tspan.result(result)
                messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })
            rounds += 1
            msg = llm_round()

        if msg.content:
            print("FINAL ANSWER:", msg.content[:200])

    store.flush()
    store.close()
    print("run streamed to", API_URL)


if __name__ == "__main__":
    main()
