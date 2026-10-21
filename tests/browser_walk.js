const { chromium } = require('playwright');
const OUT = process.argv[2];
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push(String(e)));
  page.on('response', r => { if (r.status() >= 400) errors.push(`${r.status()} ${r.url()}`); });
  const shot = async (n) => page.screenshot({ path: `${OUT}/${n}.png`, fullPage: true });
  const waitJob = async () => { await page.waitForTimeout(800); await page.waitForFunction(() => !document.querySelector('.job progress'), null, { timeout: 120000 }); await page.waitForLoadState('networkidle'); };

  await page.goto('http://127.0.0.1:8765/?token=devtoken');
  await page.click('text=Scan all sources');
  await waitJob();
  await shot('01-sources');

  await page.click('a.nav__link[href=\"/step/1\"]'); await page.waitForURL('**/step/1'); await page.waitForTimeout(400);
  await shot('02-step1');
  await page.click('text=/Review plan for all/'); await page.waitForURL(/\/plans\/\d+/);
  await shot('03-plan-cleanup');
  page.once('dialog', d => d.accept());
  await page.click('text=Apply plan'); await page.waitForLoadState('networkidle'); await page.waitForTimeout(500);
  await shot('04-plan-applied');

  await page.click('a.nav__link[href=\"/step/2\"]'); await page.waitForURL('**/step/2'); await page.waitForTimeout(400);
  await shot('05-step2');
  await page.click('text=/Similar shots/'); await page.waitForURL(/tab=bursts/); await page.waitForTimeout(400);
  await shot('06-step2-bursts');

  await page.click('a.nav__link[href=\"/step/3\"]'); await page.waitForURL('**/step/3'); await page.waitForTimeout(400);
  await page.click('text=/Build recommendations/');
  await waitJob();
  await shot('07-step3');
  await page.fill('#pattern-input', '{date:%Y-%m-%d %H-%M-%S} ({location_name}, {device_name}) - {original_filename}.{EXT}');
  await page.waitForTimeout(900);
  await shot('08-step3-pattern');
  await page.fill('#pattern-input', '{date:%Y-%m-%d %H-%M-%S} ({location_name}, {device_name}) - {original_filename}.{ext}');
  await page.waitForTimeout(700);
  await page.click('text=Accept all ≥ 70 %'); await page.waitForLoadState('networkidle'); await page.waitForTimeout(500);
  await page.click('text=/All captures/'); await page.waitForURL(/only=all/); await page.waitForTimeout(400);
  await shot('09-step3-accepted');

  await page.click('a.nav__link[href=\"/step/4\"]'); await page.waitForURL('**/step/4'); await page.waitForTimeout(400);
  await page.click('text=Run re-check');
  await waitJob();
  await shot('10-step4');
  await page.click('th a:has-text("Size")'); await page.waitForTimeout(700);
  await shot('11-step4-sorted');
  const firstGroup = await page.$('td a[href^="/compare"]');
  if (firstGroup) { await firstGroup.click(); await page.waitForLoadState('networkidle'); await page.waitForTimeout(600); await shot('12-compare'); }

  await page.goto('http://127.0.0.1:8765/step/3?only=all');
  await page.click('text=Review rename plan…'); await page.waitForURL(/\/plans\/\d+/);
  await shot('13-plan-rename');
  page.once('dialog', d => d.accept());
  await page.click('text=Apply plan'); await page.waitForLoadState('networkidle'); await page.waitForTimeout(500);
  await shot('14-rename-applied');
  page.once('dialog', d => d.accept());
  await page.click('text=Undo plan'); await page.waitForLoadState('networkidle'); await page.waitForTimeout(500);
  await shot('15-rename-undone');
  console.log(JSON.stringify({ errors }, null, 1));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
