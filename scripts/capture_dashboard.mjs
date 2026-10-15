import { chromium } from 'playwright';

const URL = 'https://agent-flight-recorder-tsd3.vercel.app/#/runs';
const OUT = 'docs/dashboard.png';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 860 } });
await page.goto(URL, { waitUntil: 'networkidle', timeout: 45000 });

// open the run in the sidebar
await page.waitForSelector('text=demo-deterministic-circle-area', { timeout: 20000 });
await page.getByText('demo-deterministic-circle-area', { exact: false }).first().click();
try {
  await page.waitForSelector('text=TIMELINE', { timeout: 15000 });
} catch (e) {
  console.error('TIMELINE never appeared. Body said:');
  console.error((await page.textContent('body')).slice(0, 600));
  process.exit(1);
}
await page.waitForTimeout(800);

// sanity: the trace view must be showing the run's detail
const body = await page.textContent('body');
if (!body.toLowerCase().includes('timeline') || !body.includes('demo-deterministic-circle-area')) {
  console.error('TRACE VIEW NOT VISIBLE — refusing to save a wrong screenshot');
  console.error('BODY DUMP:', body.slice(0, 900));
  process.exit(1);
}
await page.screenshot({ path: OUT });
console.log('saved', OUT);
await browser.close();
