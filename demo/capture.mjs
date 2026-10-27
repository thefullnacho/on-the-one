// 4K capture of the app for the demo's punch-ins: Chrome's own screencast (every repaint as a JPEG with a timestamp)
// at 2x pixel density, so a 3x punch-in on one control stays sharp. Also records where each feature sits on screen.
// run (app on :8701, notes hidden): node capture.mjs <out_dir>
//   -> <out_dir>/frames/*.jpg, frames.json [{file, t}], marks.json [{name, t, rects: {key: [x, y, w, h] in 4K px}}]
// Nothing is saved in the song: the typed story line is cleared before the debounce can save it.
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'fs';

const out = process.argv[2] || '.';
mkdirSync(`${out}/frames`, { recursive: true });
// a 2x context alone still screencasts at 1x; forcing the scale factor at launch gives real 3840-wide frames.
// The window is 87 px taller than 1080 for the browser chrome, so the page itself is 1920x1080.
const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome',
  args: ['--autoplay-policy=no-user-gesture-required', '--force-device-scale-factor=2', '--window-size=1920,1167'] });
const ctx = await b.newContext({ viewport: null });
const p = await ctx.newPage();
const cdp = await ctx.newCDPSession(p);
const frames = [], marks = [];
let n = 0, t0 = null, last = null;   // t0: epoch seconds of the first frame (frame timestamps are epoch seconds)
cdp.on('Page.screencastFrame', async (f) => {
  const t = f.metadata.timestamp; if (t0 === null) t0 = t;
  const file = `f${String(n++).padStart(5, '0')}.jpg`;
  writeFileSync(`${out}/frames/${file}`, Buffer.from(f.data, 'base64'));
  frames.push({ file, t: t - t0 }); last = t - t0;
  await cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {});
});
const wait = (ms) => p.waitForTimeout(ms);
const rect = async (sel) => {
  const r = await p.$eval(sel, (e) => { const b = e.getBoundingClientRect(); return [b.x, b.y, b.width, b.height]; }).catch(() => null);
  return r && r.map((v) => Math.round(v * 2));
};
const mark = async (name, sels = {}) => {
  const rects = {}; for (const [k, s] of Object.entries(sels)) rects[k] = await rect(s);
  marks.push({ name, t: Date.now() / 1000 - t0, rects });   // wall clock: a still screen sends no frames, so the last frame lags
};
const nudge = () => p.evaluate(() => { document.body.style.outline = document.body.style.outline ? '' : '0px solid transparent'; });

await p.goto('http://127.0.0.1:8701/?song=green_flag&step=song'); await wait(2500);
console.log('page', await p.evaluate(() => [innerWidth, innerHeight, devicePixelRatio]));
// the screencast only sends a frame when something repaints, so a still screen (Cast) would never be captured:
// an invisible 1 px pixel that animates forever keeps frames coming
await p.addStyleTag({ content: '#oto-tick{position:fixed;right:0;bottom:0;width:1px;height:1px;background:#000;z-index:99;animation:oto 0.1s steps(2) infinite}@keyframes oto{to{opacity:.99}}' });
await p.evaluate(() => { const d = document.createElement('div'); d.id = 'oto-tick'; document.body.append(d); });
for (const st of ['story', 'look', 'cast', 'shots', 'song']) { await p.click(`#steps [data-step="${st}"]`); await wait(2500); }   // warm every image first
await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 92, maxWidth: 3840, maxHeight: 2160, everyNthFrame: 1 });
await wait(600); await nudge(); await wait(300);
await mark('song', { viewer: '#viewer', words: '#words', transport: '.transport', timeline: '.timeline-wrap', pad: 'aside' });
await p.click('#play'); await wait(7000); await p.click('#play'); await wait(300);

await p.click('#steps [data-step="story"]'); await wait(1500);
await mark('story', { sec: '.sec', beats: '.sec .beats', pad: '.sec textarea', logline: '#logline' });
await p.click('.sec textarea'); await wait(300);
await p.keyboard.type('Blip reaches through the glass and pulls him out of the feed.', { delay: 55 });
await wait(400); await mark('story_typed', { pad: '.sec textarea' });
await p.keyboard.press('Control+A'); await p.keyboard.press('Delete'); await wait(200);   // cleared before the 600 ms save

await p.click('#steps [data-step="look"]'); await wait(2500);
await mark('look', { locked: '.lookcard.locked', hero: '.lookcard.locked .hero', cards: '#looks', artdir: '#artdir' });
await wait(3000);

await p.click('#steps [data-step="cast"]'); await wait(2500);
await mark('cast', { blip: '.person', strip: '.person .strip', grid: '#castgrid' });
await wait(1500);

await p.click('#steps [data-step="shots"]'); await wait(800);
for (let k = 0; k < 6; k++) { await p.keyboard.press('ArrowRight'); await wait(150); }   // lands on a shot with options + clip
await mark('shot_id', {}); marks[marks.length - 1].id = await p.$eval('#padId', (e) => e.textContent);
await wait(1200);
await mark('shots', { opts: '#opts', viewer: '#viewer', pad: 'aside' });
for (const btn of await p.$$('#opts button')) { await btn.hover(); await wait(650); }
await wait(600);
await mark('end');
await cdp.send('Page.stopScreencast');
writeFileSync(`${out}/frames.json`, JSON.stringify(frames));
writeFileSync(`${out}/marks.json`, JSON.stringify(marks, null, 1));
await b.close();
console.log(`${frames.length} frames, ${last.toFixed(1)} s;`, marks.map((m) => `${m.name} ${m.t.toFixed(1)}`).join(' | '));
