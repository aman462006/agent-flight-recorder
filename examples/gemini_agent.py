"""Example: record a real Gemini-powered agent and stream it to the live API.

This is the integration Aman's own projects use: a real model call with
real token usage, streamed to the deployed dashboard as it happens.

Setup:
    pip install google-genai          # the Gemini SDK
    export AFREC_API_URL=https://afrec-api.onrender.com
    export GEMINI_API_KEY=...         # from Google AI Studio

Run:
    python -m examples.gemini_agent

Then open https://agent-flight-recorder-tsd3.vercel.app — the run is
there with real token counts per LLM call. If the API is unreachable,
the run is still fully recorded in the local mirror (afrec.sqlite3).

Honest notes:
  - Token counts come from Gemini's usage_metadata; passing them is what
    makes the dashboard show real numbers instead of "usage unknown".
  - The recorder redacts api-key-looking fields before anything leaves
    the process; the API redacts again on ingest.
"""

from __future__ import annotations

import os

from afrec import get_recorder


def main() -> None:
    try:
        from google import genai
    except ImportError:
        raise SystemExit("pip install google-genai first")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("Set GEMINI_API_KEY (get one at aistudio.google.com).")

    client = genai.Client(api_key=api_key)
    model = "gemini-2.0-flash"
    rec = get_recorder()  # AFREC_API_URL set -> HttpStore with local mirror

    task = "Explain in one sentence why observability matters for AI agents."
    messages = [{"role": "user", "content": task}]

    with rec.run(task, tags={"provider": "gemini", "model": model}) as run:
        with run.llm_call(model, messages=messages) as span:
            resp = client.models.generate_content(model=model, contents=task)
            usage = resp.usage_metadata
            span.result(
                resp.text,
                input_tokens=getattr(usage, "prompt_token_count", None),
                output_tokens=getattr(usage, "candidates_token_count", None),
                cached_tokens=getattr(usage, "cached_content_token_count", None),
                model=model,
            )

        # A span for context the agent "saw", to show nesting in the timeline.
        with run.span("session-info", source="examples/gemini_agent.py"):
            pass

    # The recorder posts on a background thread; flush before the process
    # exits so nothing is still queued.
    store = rec.store
    if hasattr(store, "flush"):
        ok = store.flush(timeout=30)
        if hasattr(store, "close"):
            store.close()
        if not ok:
            raise SystemExit("queue did not drain — run is in the local mirror")

    print(f"run {run.run_id} recorded and streamed.")
    print("Open the dashboard: https://agent-flight-recorder-tsd3.vercel.app")


if __name__ == "__main__":
    main()
