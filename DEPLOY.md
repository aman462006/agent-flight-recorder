# Deploying the Agent Flight Recorder

Two services, both free tiers. The API on Render, the dashboard on Vercel.

Honest limits, stated first:

- Render free tier has no persistent disk: the API's SQLite file resets on
  every restart/redeploy. Fine for a demo; swap in a disk or Postgres
  before anything real.
- The dashboard's API URL is baked in at BUILD time (Vite env var), so set
  `VITE_API_URL` in Vercel before the first build.

## 1. API on Render

1. Push this repo to GitHub (already there: aman462006/agent-flight-recorder).
2. On render.com: New > Blueprint, pick this repo. The `render.yaml`
   configures everything (Python 3.12, uvicorn, /health check).
3. Note the deployed URL, e.g. `https://afrec-api.onrender.com`.
4. Verify: `curl https://<api-url>/health` returns `{"ok": true, ...}`.

## 2. Dashboard on Vercel

1. On vercel.com: Add New > Project, import the same repo. `vercel.json`
   sets the build (dashboard + npm run build, output dashboard/dist).
2. In Project Settings > Environment Variables, add:
   - `VITE_API_URL` = `https://<api-url>` (the Render URL, no trailing slash)
3. Deploy. Open the dashboard, pick a run, hit Investigate.

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
