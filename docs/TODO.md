# Docs TODO

## `docs/dashboard.png` — CAPTURED 2026-10-15

Real screenshot of the live dashboard (https://agent-flight-recorder-tsd3.vercel.app)
showing a real recorded run: `demo-deterministic-circle-area`, streamed to the
ingestion API with the SDK (`afrec.HttpStore`), one tool call, timeline visible.
Not a mockup.

To recapture (the API's storage is ephemeral, so the run may need recreating):

    # 1) stream a fresh run (see scripts/ capture script header for the SDK snippet)
    # 2) npm i -D playwright && npx playwright install chromium
    node scripts/capture_dashboard.mjs
