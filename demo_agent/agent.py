"""
Demo agent — a small, deterministic research agent used to (a) prove the
recorder captures everything and (b) generate benchmark scenarios later.

It uses a scripted/mock LLM so tests are fully deterministic and cost
nothing to run. Swapping in a real model provider later is a matter of
replacing `MockLLM` — the recorder API does not change.

Task it models: "Find three solar companies in India, compare revenue."
"""

from __future__ import annotations

from typing import Any, Dict, List

from afrec import get_recorder


# --- mock tools ---------------------------------------------------------------

def search(query: str) -> List[Dict[str, Any]]:
    """Deterministic 'web search'. Returns canned results, one deliberately
    stale so the failure investigator has something real to find."""
    db = {
        "solar companies india": [
            {"company": "Adani Green", "revenue_inr_cr": 12345, "year": 2025},
            {"company": "Tata Power Renewable", "revenue_inr_cr": 8912, "year": 2025},
            {"company": "Waaree Energies", "revenue_inr_cr": 5678, "year": 2025},
            # The trap: an outdated figure the model may treat as current.
            {"company": "Vikram Solar", "revenue_inr_cr": 2100, "year": 2022},
        ],
        "vikram solar revenue": [
            {"company": "Vikram Solar", "revenue_inr_cr": 3110, "year": 2025},
        ],
    }
    return db.get(query.lower(), [])


def calculator(expression: str) -> float:
    """Tiny arithmetic tool: only digits/operators, deliberately safe."""
    allowed = set("0123456789+-*/(). ")
    if not set(expression) <= allowed:
        raise ValueError("unsupported expression")
    return eval(expression, {"__builtins__": {}}, {})  # noqa: S307 - allowlist-guarded


# --- mock LLM ------------------------------------------------------------------

class MockLLM:
    """Scripted model: routes by keyword in the task, returns one 'decision'.

    Records token usage shaped like a real provider response so the cost
    profiler has realistic inputs. Deterministic: same task → same output.
    """

    def __init__(self, name: str = "mock-gpt"):
        self.name = name
        self.calls: List[Dict[str, str]] = []

    def decide(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        self.calls.append({"messages": messages})
        last = messages[-1]["content"]
        text = last.lower()
        if "search" in text:
            decision = {"action": "search", "query": "solar companies india"}
            usage = {"input_tokens": 1200, "output_tokens": 150}
        elif "compare" in text:
            decision = {"action": "compare", "payload": "comparison drafted from context"}
            usage = {"input_tokens": 800, "output_tokens": 220}
        else:
            decision = {"action": "answer", "payload": "three companies compared, sources cited"}
            usage = {"input_tokens": 500, "output_tokens": 180}
        return {
            "content": str(decision),
            "model": self.name,
            "usage": usage,
        }


# --- the agent loop --------------------------------------------------------------

def run_agent(task: str, use_stale_figure: bool = False) -> str:
    """Three-step agent execution, fully instrumented.

    With use_stale_figure=True the agent commits the failure the flight
    recorder exists to catch: it trusts the outdated 2022 result without
    the follow-up search that would have corrected it.
    """
    rec = get_recorder()
    llm = MockLLM()

    with rec.run(task, tags={"agent": "demo-research-agent", "llm": llm.name}) as run:
        # Step 1 — LLM decides to search
        with run.llm_call(llm.name, [{"role": "user", "content": f"Search: {task}"}]) as span:
            resp = llm.decide([{"role": "user", "content": f"Search: {task}"}])
            span.result(resp["content"], input_tokens=resp["usage"]["input_tokens"],
                        output_tokens=resp["usage"]["output_tokens"], model=resp["model"])

        # Step 2 — tool executes the search
        with run.tool_call("search", {"query": "solar companies india"}) as tool:
            results = search("solar companies india")
            tool.result(results)

        # Step 3 — either correct the stale figure or (failure mode) trust it
        if not use_stale_figure:
            with run.tool_call("search", {"query": "vikram solar revenue"}) as tool:
                fresh = search("vikram solar revenue")
                tool.result(fresh)
        else:
            # FAILURE: uses the 2022 revenue figure as if current, no re-query.
            with run.tool_call("calculator", {"expression": "2100 + 0"}) as tool:
                total = calculator("2100 + 0")
                tool.result({"value": total, "note": "trusted 2022 figure"})

        # Step 4 — LLM drafts the final answer
        with run.llm_call(llm.name, [{"role": "user", "content": f"Compare: {task}"}]) as span:
            resp = llm.decide([{"role": "user", "content": f"Compare: {task}"}])
            span.result(resp["content"], input_tokens=resp["usage"]["input_tokens"],
                        output_tokens=resp["usage"]["output_tokens"], model=resp["model"])

    return "answer"
