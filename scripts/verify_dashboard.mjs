import { chromium } from 'playwright';

const URL = 'https://agent-flight-recorder-tsd3.vercel.app/#/runs';
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 860 } });
const check = (name, ok) => {
  console.log((ok ? 'PASS' : 'FAIL') + '  ' + name);
  if (!ok) process.exitCode = 1;
};

await page.goto(URL, { waitUntil: 'networkidle', timeout: 60000 });
const body = () => page.textContent('body');

// 1. run list: 10 demos + the real failure capture under Your runs
await page.waitForSelector('text=Demo runs', { timeout: 30000 });
let t = await body();
check('10 demo runs listed', t.includes('Demo runs (10)'));
check('real failure capture listed', t.includes('real: agent fails to find payment PAY-404'));

// 2. demo-6: auto-investigation with baseline fires without any click
await page.getByText('diverging run (read stale archive', { exact: false }).first().click();
await page.waitForSelector('text=INVESTIGATION', { timeout: 20000 });
await page.waitForTimeout(2500); // let the auto-investigation round-trip land
t = await body();
check('demo-6 auto-fires baseline_divergence', t.includes('baseline_divergence'));
check('demo-6 shows a critical finding pill', /1 critical/.test(t));

// 3. side-by-side compare
await page.getByText('Compare side by side', { exact: false }).first().click();
await page.waitForSelector('text=Side-by-side comparison', { timeout: 15000 });
t = await body();
check('divergence banner pins decision #5', t.includes('First divergence at decision #5'));
check('compare shows baseline + this run columns', t.includes('BASELINE') && t.includes('THIS RUN'));
await page.screenshot({ path: '/tmp/compare_view.png' });

// 4. demo-4: the 20x loop collapses to one row
await page.getByText('stuck loop, same call 20 times', { exact: false }).first().click();
await page.waitForSelector('text=TIMELINE', { timeout: 15000 });
await page.waitForTimeout(1500);
t = await body();
check('20x loop collapsed to one row', t.includes('×20 repeated'));
await page.getByText('×20 repeated', { exact: false }).first().click();
await page.waitForTimeout(600);
t = await body();
check('collapsed row expands to individual calls', (t.match(/gemini-2.0-flash/g) || []).length >= 20);
await page.screenshot({ path: '/tmp/timeline_collapse.png' });

// 5. cost in dollars on a run (demo-1) + security section on About
await page.getByText('healthy support agent run', { exact: false }).first().click();
await page.waitForSelector('text=TIMELINE', { timeout: 15000 });
await page.waitForTimeout(2000);
t = await body();
check('est. cost stat renders', t.includes('Est. cost'));
check('cost is a dollar figure', /\$0\.0{3,}/.test(t) || /\$[0-9]/.test(t));
check('model breakdown chip renders', t.includes('gemini-2.0-flash'));
await page.screenshot({ path: '/tmp/run_detail.png' });

await page.goto(URL.replace('#/runs', '#/'), { waitUntil: 'networkidle' });
t = await body();
check('About has security section', t.includes('Security, honestly'));
check('About says twelve rules', t.includes('twelve deterministic rules'));

await browser.close();
console.log('done');
