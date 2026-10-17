import { chromium } from 'playwright';

// Captures docs/dashboard.png: the side-by-side compare view on demo-6,
// the money shot — first divergence pinned in red, amber fallout below.
const URL = 'https://agent-flight-recorder-tsd3.vercel.app/#/runs';
const OUT = 'docs/dashboard.png';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 860 } });
await page.goto(URL, { waitUntil: 'networkidle', timeout: 45000 });

await page.waitForSelector('text=Demo runs', { timeout: 30000 });
await page.getByText('diverging run (read stale archive', { exact: false }).first().click();
await page.waitForSelector('text=Investigation', { timeout: 15000 });
await page.waitForTimeout(2500); // auto-investigation round-trip

const body = await page.textContent('body');
if (!body.includes('baseline_divergence')) {
  console.error('auto-investigation did not fire — refusing to save a stale screenshot');
  process.exit(1);
}
await page.getByText('Compare side by side', { exact: false }).first().click();
await page.waitForSelector('text=Side-by-side comparison', { timeout: 15000 });
await page.waitForTimeout(600);
await page.screenshot({ path: OUT });
console.log('saved', OUT);

// phone-width sanity: nothing may scroll sideways, ever
const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
await phone.goto(URL, { waitUntil: 'networkidle', timeout: 45000 });
await phone.waitForTimeout(2500);
const overflow = await phone.evaluate(() => document.body.scrollWidth > window.innerWidth + 2);
console.log(overflow ? 'FAIL: horizontal overflow at 390px' : 'PASS: no horizontal overflow at 390px');
if (overflow) process.exitCode = 1;

await browser.close();
