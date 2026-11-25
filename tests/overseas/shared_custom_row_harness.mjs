// 추가 행은 연구소를 가로질러 **같은 줄**로 보인다 — A 연구소에서 만든 행이 B 연구소 표에도
// 빈칸으로 서고, 한쪽에서 ✕를 누르면 양쪽에서 사라진다. test_shared_custom_row.py가 부른다.
// 만든 행은 마지막에 반드시 지운다(✕ 자체가 이 테스트의 마지막 단계라 그것으로 정리된다).
import http from 'node:http'; import fs from 'node:fs'; import path from 'node:path';
const HERE = path.dirname(new URL(import.meta.url).pathname);
const FE = path.resolve(HERE, '../../../frontend-html');
const API = (process.argv.find((a) => a.startsWith('--api=')) || '').slice(6);
const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8' };
const srv = await new Promise((r) => { const s = http.createServer((q, res) => {
  const u = q.url.split('?')[0].split('#')[0];
  fs.readFile(path.join(FE, u === '/' ? 'index.html' : u), (e, b) => {
    if (e) { res.writeHead(404); res.end('nf'); return; }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(u)] || 'application/octet-stream' }); res.end(b); });
}); s.listen(0, '127.0.0.1', () => r(s)); });
let chromium;
try { ({ chromium } = await import('playwright')); }
catch (e) { console.log(JSON.stringify({ skip: 'playwright가 없다: ' + e.message })); process.exit(0); }
let browser;
try { browser = await chromium.launch(); }
catch (e) { srv.close(); console.log(JSON.stringify({ skip: '브라우저를 띄울 수 없다: ' + e.message })); process.exit(0); }
const page = await browser.newPage({ viewport: { width: 1800, height: 1000 } });
const errs = []; page.on('pageerror', (e) => errs.push(String(e.message)));
page.on('dialog', (d) => d.accept());               // 삭제 확인창은 '예'
await page.addInitScript((a) => { window.OV_API_BASE = a; }, API);
await page.goto('http://127.0.0.1:' + srv.address().port + '/overseas.html#/', { waitUntil: 'networkidle', timeout: 30000 });
for (let i = 0; i < 24; i++) {
  const ok = await page.evaluate(() => typeof OV_EL !== 'undefined' && OV_EL.length > 5 && OV_EL.every((e) => OV_ELK[e.k]) && OV_EL.every((e) => !e.calc || e.calc.every((k) => OV_ELK[k])));
  if (ok) break; await page.waitForTimeout(500);
}
const out = {};
const NAME = '공통행테스트';
const go = async (lab, part) => { await page.evaluate(([l, p]) => { S.ov.step = 0; S.ov.lab = l; if (p) S.ov.part = p; render(); }, [lab, part]); await page.waitForTimeout(2500); };
const rowsOf = () => page.evaluate(() => [...document.querySelectorAll('table.ovt tbody tr.cx')].map((tr) => ({
  name: (tr.querySelector('input.lbl') || {}).value || '?',
  vals: [...tr.querySelectorAll('input.n')].slice(0, 3).map((i) => i.value),
  anchor: (tr.querySelector('.anchorbtn') || {}).textContent || null,
  hasX: !!tr.querySelector('[data-ovcact="delRow"]'),
  hasHandle: !!tr.querySelector('[data-ovdrag]'),
})));

// 1) A 연구소에서 행을 만들고 이름·금액을 넣는다
await go('SARC', 'CPU');
const before = await page.evaluate(() => [...document.querySelectorAll('tr.cx[data-ovrow]')].map((t) => t.dataset.ovrow.slice(2)));
await page.click('[data-ovcact="addRow"]'); await page.waitForTimeout(1000);
const RK = await page.evaluate((b) => [...document.querySelectorAll('tr.cx[data-ovrow]')].map((t) => t.dataset.ovrow.slice(2)).find((k) => !b.includes(k)), before);
out.rowKey = RK;
await page.evaluate(([rk, nm]) => {
  const tr = document.querySelector('tr[data-ovrow="x:' + rk + '"]');
  const i = tr.querySelector('input.lbl'); i.value = nm; i.dispatchEvent(new Event('input', { bubbles: true })); i.dispatchEvent(new Event('change', { bubbles: true }));
  const n = tr.querySelector('input.n'); n.value = '7'; n.dispatchEvent(new Event('input', { bubbles: true }));
}, [RK, NAME]);
await page.waitForTimeout(1500);
out.A = (await rowsOf()).filter((r) => r.name === NAME);

// 2) B 연구소(DSRA)로 간다 — 같은 줄이 빈칸으로 서 있어야 한다
await go('DSRA', null);
out.B = (await rowsOf()).filter((r) => r.name === NAME);

// 3) 만든 행은 API로 치운다 — 화면의 ✕는 없앴고(삭제는 AI 챗봇 몫), 테스트가 남긴 줄은
//     다음 사람 화면에 그대로 보이므로 반드시 지운다.
out.cleanup = await page.evaluate(async (rk) => {
  const q = new URLSearchParams({ lab_pk: 'SARC:CPU', version: ovVerApi(S.ov.upVer), row_key: rk,
                                  yyyymm: ovYyyymmApi(S.ov.upVer), confirmed: 'true' });
  const r = await fetch(OV_API + '/extra-rows?' + q.toString(), { method: 'DELETE' });
  return r.status;
}, RK);
out.errs = errs;
console.log(JSON.stringify(out));   // 한 줄로 — 테스트는 마지막 줄만 읽는다
await browser.close(); srv.close();
