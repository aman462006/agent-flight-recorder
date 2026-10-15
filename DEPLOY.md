# Deploying the Agent Flight Recorder

Two ways to deploy, both free tiers:

1. **One Vercel project, both services** (what `vercel.json` configures):
   the dashboard at the domain root and the ingestion API on every other
   path, same origin. No `VITE_API_URL` needed — the dashboard fetches
   `/runs`, `/health`, ... relative to its own origin.
2. **Standalone API on Render** (`render.yaml`): useful when streaming
   runs from real agents (`afrec.HttpStore`) to a long-lived API URL.

Honest limits, stated first:

- Serverless SQLite lives in `/tmp` (Vercel) or resets on restart/redeploy
  (Render free tier): recorded runs disappear when the instance recycles.
  Fine for a demo; swap in Postgres before anything real.
- `VITE_API_URL` is only needed if you want the dashboard to talk to a
  DIFFERENT API (e.g. the Render one); leave it unset for the single
  Vercel project.

## 1. API on Render

1. Push this repo to GitHub (already there: aman462006/agent-flight-recorder).
2. On render.com: New > Blueprint, pick this repo. The `render.yaml`
   configures everything (Python 3.12, uvicorn, /health check).
3. Note the deployed URL, e.g. `https://afrec-api.onrender.com`.
4. Verify: `curl https://<api-url>/health` returns `{"ok": true, ...}`.

## 2. Dashboard + API on Vercel (single project)

1. On vercel.com: Add New > Project, import this repo, leave the root
   directory at the repo root. `vercel.json` (services mode) builds both:
   the dashboard (Vite) and the ingestion API (FastAPI, entrypoint
   `api/main.py`).
2. No environment variables needed: the dashboard is served at the domain
   root and calls the same-origin API (`/health`, `/runs`, ...).
3. Deploy, open the dashboard, pick a run, hit Investigate.

## 3. Seeding a demo run

The API starts empty. Seed the demo agent's good + bad runs so the
Investigation panel has something to chew on:

```bash
pip install -r requirements.txt
python -m demo_agent.seed_remote --api https://<api-url>
```

(This seeds a good run and the stale-figure bad run through the public
ingestion API; see the script for the exact calls.)

## 4. Demo video script (when recording)

1. Local dashboard or Vercel: run list, pick the stale-figure bad run.
   Looks "completed". Everything green. No visible problem.
2. Hit Investigate with the good run as baseline.
3. The critical finding: earliest divergence at decision #5, the
   corrective search became a calculator call, evidence excerpt on screen.
4. Punchline: single-run rules report this run CLEAN because the fresh
   figure never entered its trace. The baseline diff is what catches it.
5. Show the benchmark table (README) for the measured numbers.
