#!/usr/bin/env node
// Render HTML to PNG stills, or an HTML timeline to a PNG frame sequence, with the installed Google Chrome.
// Used for this repo's README art (docs/*.html -> docs/*.png).
// Needs: node 18+, `npm install puppeteer-core` in the repo root, and Chrome. Set CHROME_PATH if Chrome is not at
// the macOS default (/Applications/Google Chrome.app/...), e.g. CHROME_PATH=/usr/bin/google-chrome on Linux.
//
// STILL (html -> png):
//   node docs/render.mjs still --html page.html --out out.png --width 1080 --height 1920 [--scale 2] [--fullpage] [--transparent] [--wait MS]
//
// FRAMES (html timeline -> png sequence, for video overlays or full motion graphics):
//   node docs/render.mjs frames --html timeline.html --out-dir frames/ --width 1080 --height 1920 --fps 30
//        [--duration 12.5] [--start 0] [--transparent] [--workers 4] [--scale 1]
//
// Timeline page contract (frames mode):
//   window.__duration = <seconds>            (optional if --duration given)
//   window.__seek = async (t) => { ... }     (draw the exact state at time t; must be deterministic)
//   window.__ready = true                    (set once fonts/images/videos are loaded)
// Then encode, e.g.:
//   ffmpeg -framerate 30 -i frames/%05d.png -c:v libx264 -pix_fmt yuv420p -crf 18 out.mp4
//   ffmpeg -i base.mp4 -framerate 30 -i frames/%05d.png -filter_complex "[0:v][1:v]overlay=0:0" -c:a copy out.mp4
const { default: puppeteer } = await import('puppeteer-core').catch(() => ({ default: null }));
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const CHROME = process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

function parse(argv) {
  const o = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const k = a.slice(2);
      const n = argv[i + 1];
      if (n === undefined || n.startsWith('--')) o[k] = true; else { o[k] = n; i++; }
    } else o._.push(a);
  }
  return o;
}

async function launch() {
  if (!puppeteer) throw new Error('puppeteer-core is missing: run `npm install` in the repo root');
  return puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    args: [
      '--no-sandbox', '--hide-scrollbars', '--force-color-profile=srgb', '--font-render-hinting=none',
      '--allow-file-access-from-files', '--disable-web-security', '--autoplay-policy=no-user-gesture-required',
      '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--mute-audio',
    ],
  });
}

async function openPage(browser, html, width, height, scale) {
  const page = await browser.newPage();
  page.on('pageerror', e => console.error('[pageerror]', e.message));
  page.on('console', m => { if (['error', 'warn'].includes(m.type())) console.error('[console.' + m.type() + ']', m.text()); });
  await page.setViewport({ width, height, deviceScaleFactor: scale });
  const url = /^https?:/.test(html) ? html : pathToFileURL(path.resolve(html)).href;
  await page.goto(url, { waitUntil: 'networkidle0', timeout: 180000 });
  await page.evaluate(() => document.fonts && document.fonts.ready);
  return page;
}

async function still(o) {
  const width = +o.width || 1080, height = +o.height || 1920, scale = +o.scale || 1;
  const browser = await launch();
  try {
    const page = await openPage(browser, o.html, width, height, scale);
    if (o.wait) await new Promise(r => setTimeout(r, +o.wait));
    const hasReady = await page.evaluate(() => 'ready' in window || '__ready' in window);
    if (hasReady) await page.waitForFunction('window.__ready === true || window.ready === true', { timeout: 180000 });
    fs.mkdirSync(path.dirname(path.resolve(o.out)), { recursive: true });
    await page.screenshot({ path: o.out, type: 'png', fullPage: !!o.fullpage, omitBackground: !!o.transparent,
      ...(o.fullpage ? {} : { clip: { x: 0, y: 0, width, height } }) });
    console.log('wrote', o.out);
  } finally { await browser.close(); }
}

async function frames(o) {
  const width = +o.width || 1080, height = +o.height || 1920, scale = +o.scale || 1;
  const fps = +o.fps || 30, workers = Math.max(1, +o.workers || 4), start = +o.start || 0;
  const outDir = o['out-dir'];
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await launch();
  try {
    const first = await openPage(browser, o.html, width, height, scale);
    await first.waitForFunction('window.__ready === true', { timeout: 180000 });
    const duration = o.duration ? +o.duration : await first.evaluate(() => window.__duration);
    if (!duration) throw new Error('no duration: pass --duration or set window.__duration');
    const total = Math.round((duration - start) * fps);
    const pages = [first];
    for (let w = 1; w < workers; w++) {
      const p = await openPage(browser, o.html, width, height, scale);
      await p.waitForFunction('window.__ready === true', { timeout: 180000 });
      pages.push(p);
    }
    let next = 0, done = 0; const t0 = Date.now();
    await Promise.all(pages.map(async (page) => {
      while (true) {
        const i = next++;
        if (i >= total) return;
        const t = start + i / fps;
        await page.evaluate(async (tt) => { await window.__seek(tt); }, t);
        await page.screenshot({ path: path.join(outDir, String(i).padStart(5, '0') + '.png'), type: 'png',
          omitBackground: !!o.transparent, clip: { x: 0, y: 0, width, height } });
        done++;
        if (done % 150 === 0) console.log(`${done}/${total} frames (${((Date.now() - t0) / 1000).toFixed(0)}s)`);
      }
    }));
    console.log(`wrote ${total} frames to ${outDir} in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  } finally { await browser.close(); }
}

const USAGE = `usage:
  node docs/render.mjs still --html page.html --out out.png --width W --height H [--scale 2] [--fullpage] [--transparent] [--wait MS]
  node docs/render.mjs frames --html timeline.html --out-dir frames/ --width W --height H --fps 30 [--duration S] [--start S] [--transparent] [--workers 4]
Chrome: $CHROME_PATH or ${CHROME}`;

const argv = process.argv.slice(2);
if (argv.includes('--help') || argv.includes('-h')) { console.log(USAGE); process.exit(0); }
const o = parse(argv);
const mode = o._[0];
if (mode === 'still') await still(o);
else if (mode === 'frames') await frames(o);
else { console.error(USAGE); process.exit(2); }
