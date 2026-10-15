"""
bench/real/agent.py — a small tool-using agent run against a REAL LLM.

Why this exists: the scripted benchmark (bench/run_benchmark.py) scores
the investigator on failures we planted ourselves, which is circular.
This agent makes genuinely model-driven runs: the LLM decides which tool
to call, with what arguments, and when to stop. Whatever it does wrong,
it does wrong on its own.

The environment (the search corpus) is fixed and honest about its own
dates: some entities carry BOTH an old and a fresh record, so stale-data
traps exist but nothing forces the model to fall into one.

Provider: Groq (OpenAI-compatible REST, matching RevGuard's production
setup, Llama 3.3), key read from the GROQ_API_KEY environment variable,
NEVER hardcoded. Gemini remains supported via GEMINI_API_KEY. make_llm()
auto-detects which provider to use. Model name and temperature are
configurable so runs are reproducible and recorded.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from typing import Any, Dict, List, Optional

from afrec import Recorder
from afrec.models import new_id
from afrec.recorder import SqliteStore

# ---------------------------------------------------------------------------
# The search corpus: flat, dated records. Staleness is real (old and new
# records coexist for some entities) but failures are NOT scripted.
# ---------------------------------------------------------------------------

CORPUS: List[Dict[str, Any]] = [
    # companies with fresh + stale records (the model must notice dates)
    {"company": "Vikram Solar", "metric": "revenue_inr_cr", "value": 2100, "year": 2022},
    {"company": "Vikram Solar", "metric": "revenue_inr_cr", "value": 3110, "year": 2025},
    {"company": "Waaree Energies", "metric": "revenue_inr_cr", "value": 3500, "year": 2023},
    {"company": "Waaree Energies", "metric": "revenue_inr_cr", "value": 5678, "year": 2025},
    {"company": "Adani Green", "metric": "revenue_inr_cr", "value": 12345, "year": 2025},
    {"company": "Tata Power Renewable", "metric": "revenue_inr_cr", "value": 8912, "year": 2025},
    {"company": "Renew Power", "metric": "revenue_inr_cr", "value": 6600, "year": 2024},
    {"company": "Renew Power", "metric": "revenue_inr_cr", "value": 7500, "year": 2025},
    {"company": "Ola Electric", "metric": "revenue_inr_cr", "value": 4500, "year": 2025},
    {"company": "Ather Energy", "metric": "revenue_inr_cr", "value": 1800, "year": 2024},
    {"company": "Zoho", "metric": "employees", "value": 15000, "year": 2024},
    {"company": "Zoho", "metric": "employees", "value": 18000, "year": 2025},
    {"company": "Freshworks", "metric": "arr_usd_m", "value": 250, "year": 2023},
    {"company": "Freshworks", "metric": "arr_usd_m", "value": 310, "year": 2025},
    {"company": "Razorpay", "metric": "tpv_inr_cr", "value": 400000, "year": 2024},
    {"company": "Razorpay", "metric": "valuation_usd_b", "value": 7.5, "year": 2024},
    # conversion rates that drift over time
    {"metric": "usd_inr_rate", "value": 74.5, "year": 2023, "as_of": "2023-12-31"},
    {"metric": "usd_inr_rate", "value": 83.2, "year": 2025, "as_of": "2025-10-01"},
    # populations that changed
    {"metric": "surat_population", "value": 6.0, "unit": "million", "year": 2020},
    {"metric": "surat_population", "value": 7.5, "unit": "million", "year": 2025},
]


def _tokens(text: Any) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", str(text).lower()) if len(t) > 2}

def search(query: str) -> List[Dict[str, Any]]:
    """Token-overlap search, ranked by specificity. A query naming an entity
    returns that entity's records (stale AND fresh — deciding which to trust
    is the model's job); a broad query returns the broad set. Verbatim dates."""
    q = _tokens(query)
    if not q:
        return []
    scored = [(len(q & _tokens(" ".join(str(v) for v in rec.values()))), rec)
              for rec in CORPUS]
    best = max(s for s, _ in scored)
    if best == 0:
        return []
    if best >= 2:
        return [rec for s, rec in scored if s == best]
    return [rec for s, rec in scored if s >= 1]


def calculator(expression: str) -> float:
    """Arithmetic on digits/operators only (allowlist-guarded eval)."""
    allowed = set("0123456789+-*/(). ")
    if not set(expression) <= allowed:
        raise ValueError(f"unsupported expression: {expression!r}")
    return eval(expression, {"__builtins__": {}}, {})  # noqa: S307


# ---------------------------------------------------------------------------
# The LLM: Gemini REST. Key from env, never hardcoded, never logged.
# ---------------------------------------------------------------------------

DEFAULT_GROQ_MODEL = os.environ.get("AFREC_REAL_MODEL", "llama-3.3-70b-versatile")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
API_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class GroqLLM:
    """Groq via its OpenAI-compatible REST endpoint (no SDK dependency).
    The provider RevGuard uses in production; keys from GROQ_API_KEY only."""

    def __init__(self, model: str = DEFAULT_GROQ_MODEL, temperature: float = 0.7,
                 api_key: Optional[str] = None):
        self.model = model
        self.temperature = temperature
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY is not set. Export it and retry.")

    def complete(self, prompt: str) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": 1024,
        }
        req = urllib.request.Request(
            GROQ_URL, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:200]
            except Exception:
                pass
            if e.code == 401:
                raise RuntimeError(
                    "The provider rejected the credentials (401). The "
                    "environment needs a working key for the selected "
                    "provider — keys are read from the environment only.") from e
            raise
        usage = body.get("usage", {})
        text = body.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        return {"text": text,
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "model": body.get("model", self.model)}


def make_llm(temperature: float = 0.7) -> "GroqLLM | GeminiLLM":
    """Auto-detect the provider from the environment. Groq (RevGuard's
    production provider) is preferred; Gemini is the fallback."""
    if os.environ.get("GROQ_API_KEY"):
        return GroqLLM(temperature=temperature)
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiLLM(model=DEFAULT_GEMINI_MODEL, temperature=temperature)
    raise RuntimeError(
        "No LLM provider key in the environment. Set GROQ_API_KEY "
        "(preferred) or GEMINI_API_KEY and retry.")


class GeminiLLM:
    """Thin REST client. Returns raw text + token usage, raises on API errors."""

    def __init__(self, model: str = DEFAULT_GEMINI_MODEL, temperature: float = 0.7,
                 api_key: Optional[str] = None):
        self.model = model
        self.temperature = temperature
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set — the real-run agent needs it. "
                "Export it and retry; keys are never hardcoded.")

    def complete(self, prompt: str) -> Dict[str, Any]:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": self.temperature, "maxOutputTokens": 1024},
        }
        url = f"{API_URL}/{self.model}:generateContent"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": self.api_key},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:200]
            except Exception:
                pass
            if "API key not valid" in detail:
                raise RuntimeError(
                    "The Gemini API rejected the key (API_KEY_INVALID). "
                    "Set a valid GEMINI_API_KEY — keys are read from the "
                    "environment, never hardcoded.") from e
            raise
        usage = body.get("usageMetadata", {})
        text = ""
        parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        for p in parts:
            if "text" in p:
                text += p["text"]
        return {
            "text": text,
            "input_tokens": usage.get("promptTokenCount"),
            "output_tokens": usage.get("candidatesTokenCount"),
            "model": self.model,
        }


# ---------------------------------------------------------------------------
# The agent loop — the model decides; the harness only records and bounds.
# ---------------------------------------------------------------------------

MAX_STEPS = 8

SYSTEM_PROMPT = """You are a research agent with two tools:

1. search — search a company/market facts corpus.
   Args: {"tool": "search", "args": {"query": "..."}}
2. calculator — arithmetic, e.g. {"tool": "calculator", "args": {"expression": "3500*1.2"}}

Rules:
- Reply with ONE JSON object only: either a tool call, or a final answer:
  {"answer": "..."} — no markdown, no extra text.
- Records carry a `year`. Use the most recent data; note conflicts when you see them.
- If the corpus has no answer, say so in the answer. Do not invent figures.
- You have at most {max_steps} tool calls. Choose carefully.
"""


def _parse_action(text: str) -> Optional[Dict[str, Any]]:
    """Extract the model's JSON object; tolerate code fences. Returns None on
    garbage — the loop records it as an error event and lets the model retry,
    because recovering-or-ignoring is the model's own behaviour to exhibit."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        if isinstance(obj, dict) and ("tool" in obj or "answer" in obj):
            return obj
        return None
    except json.JSONDecodeError:
        return None


def run_task(task: str, llm: GeminiLLM, trace_dir: Optional[str] = None,
             task_id: Optional[str] = None) -> Dict[str, Any]:
    """Run one task end to end, afrec-instrumented, and return the trace."""
    import tempfile
    db_path = os.path.join(tempfile.mkdtemp(), "run.sqlite3")
    rec = Recorder(store=SqliteStore(db_path))
    tid = task_id or new_id("task")
    tags = {"bench": "real", "model": llm.model,
            "temperature": llm.temperature}

    # NB: never .format() this prompt — it contains JSON braces that format
    # would treat as replacement fields. Plain replace only.
    system = SYSTEM_PROMPT.replace("{max_steps}", str(MAX_STEPS))
    conversation = [f"{system}\nTask: {task}"]
    final_answer = None
    tool_calls = 0
    parse_errors = 0

    with rec.run(task, tags=tags) as run:
        for step in range(MAX_STEPS):
            with run.llm_call(llm.model, [{"role": "user", "content": conversation[-1]}]) as span:
                resp = llm.complete(conversation[-1])
                span.result(resp["text"], input_tokens=resp["input_tokens"],
                           output_tokens=resp["output_tokens"], model=resp["model"])

            action = _parse_action(resp["text"])
            if action is None:
                parse_errors += 1
                conversation.append(
                    "ERROR: your last reply was not a valid action JSON. "
                    'Reply with a single JSON object: {"tool": ...} or {"answer": ...}.')
                continue
            if "answer" in action:
                final_answer = str(action["answer"])
                break
            tool = str(action.get("tool", "")).strip()
            args = action.get("args", {}) if isinstance(action.get("args"), dict) else {}
            tool_calls += 1
            with run.tool_call(tool or "unknown", args) as tspan:
                if tool == "search":
                    result = search(str(args.get("query", "")))
                elif tool == "calculator":
                    try:
                        result = {"value": calculator(str(args.get("expression", "")))}
                    except Exception as e:
                        result = {"error": str(e)}
                else:
                    result = {"error": f"unknown tool {tool!r}"}
                tspan.result(result)
            conversation.append(
                f"Tool {tool} returned: {json.dumps(result, default=str)[:1200]}")
        else:
            final_answer = None  # step budget exhausted — a loop or wandering trace

    events = rec.store.get_events(run.run_id)
    return {
        "task_id": tid,
        "task": task,
        "model": llm.model,
        "temperature": llm.temperature,
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "run_id": run.run_id,
        "run_meta": {"run_id": run.run_id, "task": task, "status": run.meta.status,
                     "tags": tags},
        "events": events,
        "final_answer": final_answer,
        "tool_calls": tool_calls,
        "parse_errors": parse_errors,
        "db_path": db_path,
    }


def run_task_from_config(task_cfg: Dict[str, Any], model: Optional[str] = None,
                         temperature: Optional[float] = None) -> Dict[str, Any]:
    llm = GeminiLLM(model=model or DEFAULT_GEMINI_MODEL,
                    temperature=temperature if temperature is not None else 0.7)
    return run_task(task_cfg["task"], llm, task_id=task_cfg.get("id"))
