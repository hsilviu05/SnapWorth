// Renders post.html to out/halloween_2026.png at 1080x1350.
//   node marketing/ig/halloween-2026/render.js
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
(async () => {
  const dir = __dirname;
  fs.mkdirSync(path.join(dir, 'out'), { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1080, height: 1350 }, deviceScaleFactor: 1 });
  await page.goto('file://' + path.join(dir, 'post.html'));
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: path.join(dir, 'out', 'halloween_2026.png') });
  await browser.close();
})();
