import { chromium } from 'playwright';

// Records docs/demo.gif source video: run list -> demo-6 -> auto-investigation
// -> side-by-side compare with the divergence pinned. ~15s, converted to GIF.
const URL = 'https://agent-flight-recorder-tsd3.vercel.app/#/runs';

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1280, height: 760 },
  recordVideo: { dir: '/tmp/afrec_video', size: { width: 1280, height: 760 } },
});
const page = await ctx.newPage();
await page.goto(URL, { waitUntil: 'networkidle', timeout: 60000 });
await page.waitForSelector('text=Demo runs', { timeout: 30000 });
await page.waitForTimeout(1200);

// slow, visible mouse moves so the GIF reads as a human demoing
await page.getByText('diverging run (read stale archive', { exact: false }).first().hover();
await page.waitForTimeout(400);
await page.getByText('diverging run (read stale archive', { exact: false }).first().click();
await page.waitForSelector('text=Investigation', { timeout: 15000 });
await page.waitForTimeout(2800); // auto-investigation lands

await page.getByText('Compare side by side', { exact: false }).hover();
await page.waitForTimeout(300);
await page.getByText('Compare side by side', { exact: false }).first().click();
await page.waitForSelector('text=Side-by-side comparison', { timeout: 15000 });
await page.waitForTimeout(2500);

await ctx.close(); // closes video file
await browser.close();
console.log('video written to /tmp/afrec_video');
