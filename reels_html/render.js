// Headless-Chrome 逐幀渲染 HTML 動畫模板 → PNG 幀序列。
// 用法: node render.js <template.html> <out_dir> <seconds> <fps>
const puppeteer = require('puppeteer-core');
const path = require('path');
const fs = require('fs');

const CHROME = process.env.CHROME_PATH ||
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

(async () => {
  const tpl = path.resolve(process.argv[2]);
  const outDir = path.resolve(process.argv[3] || 'frames');
  const seconds = parseFloat(process.argv[4] || '4');
  const fps = parseInt(process.argv[5] || '30', 10);
  const dataPath = process.argv[6];   // optional JSON file with scene data
  const data = dataPath ? JSON.parse(fs.readFileSync(dataPath, 'utf8')) : null;
  fs.mkdirSync(outDir, { recursive: true });

  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--no-sandbox', '--force-color-profile=srgb', '--hide-scrollbars'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1080, height: 1920, deviceScaleFactor: 1 });
  await page.goto('file://' + tpl, { waitUntil: 'networkidle0' });
  await page.evaluate(async () => { if (document.fonts) await document.fonts.ready; });
  if (data) await page.evaluate((d) => window.setData(d), data);
  await page.evaluate(async () => { if (document.fonts) await document.fonts.ready; });

  const N = Math.round(seconds * fps);
  for (let i = 0; i < N; i++) {
    const t = N === 1 ? 1 : i / (N - 1);
    await page.evaluate((tt) => window.seek(tt), t);
    await page.screenshot({ path: path.join(outDir, `f_${String(i).padStart(5, '0')}.png`) });
  }
  await browser.close();
  console.log(`rendered ${N} frames @ ${fps}fps → ${outDir}`);
})();
