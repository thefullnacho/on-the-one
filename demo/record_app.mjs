// Scripted walkthrough of the app, recorded at 1920x1080 for the demo video. Nothing is picked or changed.
// run (with the app on :8701): node record_app.mjs <out_dir>      -> <out_dir>/app_walkthrough.webm + marks.json
import { chromium } from 'playwright';
import { writeFileSync, renameSync } from 'fs';

const out = process.argv[2] || '.';
const W = 1920, H = 1080, url = (step) => `http://127.0.0.1:8701/?song=green_flag&step=${step}`;
const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome', args: ['--autoplay-policy=no-user-gesture-required'] });
const ctx = await b.newContext({ viewport: { width: W, height: H }, recordVideo: { dir: out, size: { width: W, height: H } } });
const p = await ctx.newPage();
const t0 = Date.now(), marks = [];
const mark = (name) => marks.push({ name, t: (Date.now() - t0) / 1000 });
const wait = (ms) => p.waitForTimeout(ms);

await p.goto(url('song')); await wait(2500);
mark('song'); await p.click('#play'); await wait(9000); await p.click('#play');
mark('story'); await p.click('#steps [data-step="story"]'); await wait(2000);
await p.mouse.move(W / 2, H / 2); for (let k = 0; k < 12; k++) { await p.mouse.wheel(0, 120); await wait(140); } await wait(1200);
await p.click('#steps [data-step="look"]'); await wait(2500); mark('look'); await wait(3500);
mark('cast'); await p.click('#steps [data-step="cast"]'); await wait(3500);
mark('shots'); await p.click('#steps [data-step="shots"]'); await wait(800);
for (let k = 0; k < 4; k++) { await p.keyboard.press('ArrowRight'); await wait(500); }
for (const btn of await p.$$('#opts button')) { await btn.hover(); await wait(700); }
await wait(1200);
mark('review'); await p.click('#steps [data-step="review"]'); await wait(1500);
await p.click('#thumbs button[title="verse1-5"]'); await wait(3000);
mark('end');
const video = p.video();
await ctx.close(); await b.close();
renameSync(await video.path(), `${out}/app_walkthrough.webm`);
writeFileSync(`${out}/marks.json`, JSON.stringify(marks, null, 1));
console.log(marks.map((m) => `${m.name} ${m.t.toFixed(1)}`).join(' | '));
