// 인건비율 모달의 연구소별 체크박스 · 클립보드 복사. test_lr_copy.py가 부른다.
// 읽기만 한다 — 표를 보고 체크박스를 누르고 복사 버튼을 누르는 것까지라 DB에 아무것도 남기지 않는다.
// 클립보드는 권한 대신 navigator.clipboard.writeText를 가로채서 받는다(브라우저 클립보드 권한이
// 환경마다 달라서, 권한을 요구하면 코드가 아니라 환경 때문에 테스트가 깨진다).
// 화면은 당월로 뜨는데 개발 DB의 당월은 비어 있을 수 있어서, **숫자가 들어 있는 버전**을
// 버전 목록에서 찾아 고른다 — 특정 달을 박아 두면 DB가 바뀔 때마다 테스트가 깨진다.
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
await page.addInitScript((a) => {
  window.OV_API_BASE = a;
  window.__COPIED = null;
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: (t) => { window.__COPIED = t; return Promise.resolve(); } },
  });
}, API);
await page.goto('http://127.0.0.1:' + srv.address().port + '/overseas.html#/', { waitUntil: 'networkidle', timeout: 30000 });

const bye = async (o) => { console.log(JSON.stringify(o)); await browser.close(); srv.close(); process.exit(0); };

// ④ 전체 계정 탭의 [인건비율] 팝업을 연다. 비율이 들어 있는 버전을 찾을 때까지 버전을 바꿔 본다.
const show = (vid) => page.evaluate((id) => {
  if (id) S.vers.sel = id;
  S.ov.view = 'plan'; S.ov.step = 1; S.ov.tab = 't1'; S.ov.lrOpen = {}; S.ov.lrSel = {}; S.ov.lrPop = true; render();
}, vid);
const ready = () => page.evaluate(() => {
  const t = OV_LR_T[ovVerQ()];
  if (!t || t.loading) return null;                 // 아직 받는 중
  if (t.error || !t.blocks) return 0;
  return (t.blocks || []).some((b) => (b.ratio || []).some((v) => v != null))
    ? (t.blocks || []).filter((b) => b.level === 1).length : 0;
});
const open = async (vid) => {
  await show(vid);
  for (let i = 0; i < 20; i++) {
    const n = await ready();
    if (n != null) return n;
    await page.waitForTimeout(400);
  }
  return 0;
};
let labs = await open(null);
if (!labs) {
  for (const vid of await page.evaluate(() => (S.vers.list || []).map((v) => v.id))) {
    labs = await open(vid); if (labs) break;
  }
}
if (!labs) await bye({ skip: '비율이 들어 있는 버전이 없다 — 개발 DB에 해외연구소 데이터가 없다', errs });
const out = { labs, ver: await page.evaluate(() => ovVerQ()) };

const rowsOf = () => page.evaluate(() => [...document.querySelectorAll('.modal table.ovt tbody tr')].map((tr) => ({
  cls: tr.className,
  ck: ((tr.querySelector('input[data-vlrck]') || {}).dataset || {}).vlrck || null,
  checked: !!(tr.querySelector('input[data-vlrck]') || {}).checked,
  inLb: !!tr.querySelector('td.lb input[data-vlrck]'),     // 체크박스가 구분 칸 안에 있나
  inCk: !!tr.querySelector('td.ck input[data-vlrck]'),     // 체크 열(자기 칸)에 있나
  label: ((tr.querySelector('td.lb') || {}).textContent || '').replace(/[▸▾]/g, '').trim(),
})));

// 체크박스는 전체 줄과 연구소 줄에만 있다 — 파트 · 펼친 금액 줄에는 없다.
// 그리고 구분 칸 안이 아니라 맨 왼쪽 체크 열에 선다(안 그러면 그 줄만 이름이 밀린다).
const R0 = await rowsOf();
out.ckRows = R0.filter((r) => r.ck).map((r) => r.ck);
out.ckOnTotal = R0.filter((r) => r.ck === 'ALL' && /lv0/.test(r.cls)).length;
out.ckOnPart = R0.filter((r) => r.ck && /lv2/.test(r.cls)).length;
out.ckInLb = R0.filter((r) => r.inLb).length;
out.ckInCkCol = R0.filter((r) => r.inCk).length;
// 구분 칸이 가로 스크롤에 붙어 있어야 한다 — 체크 열이 생기면서 첫 칸이 아니게 됐다.
out.lbSticky = await page.evaluate(() => {
  const td = document.querySelector('#ovLRT td.lb'), ck = document.querySelector('#ovLRT td.ck');
  const g = (x) => x && getComputedStyle(x);
  return { lb: g(td).position + ' ' + g(td).left, ck: g(ck).position + ' ' + g(ck).left };
});

// 전체 줄을 체크하면 연구소가 모두 체크된다.
await page.click('.modal input[data-vlrck="ALL"]'); await page.waitForTimeout(500);
const RA = await rowsOf();
out.allOn = { checked: RA.filter((r) => r.checked).map((r) => r.ck), sel: await page.evaluate(() => Object.keys(S.ov.lrSel || {})) };
// 연구소 하나를 풀면 전체 체크도 같이 풀린다.
await page.click('.modal input[data-vlrck="' + out.ckRows[1] + '"]'); await page.waitForTimeout(500);
const RB = await rowsOf();
out.oneOff = { all: !!(RB.find((r) => r.ck === 'ALL') || {}).checked, checked: RB.filter((r) => r.checked).map((r) => r.ck) };
// 다시 전체를 누르면(지금은 일부만 체크) 전부 체크된다. 한 번 더 누르면 전부 풀린다.
await page.click('.modal input[data-vlrck="ALL"]'); await page.waitForTimeout(500);
out.reAll = (await rowsOf()).filter((r) => r.checked).length;
await page.click('.modal input[data-vlrck="ALL"]'); await page.waitForTimeout(500);
out.clearAll = (await rowsOf()).filter((r) => r.checked).length;

// 아무것도 안 고른 상태의 복사 버튼은 글자 없는 아이콘 버튼이다(고르면 숫자 뱃지가 붙는다).
out.btnIconOnly = await page.evaluate(() => {
  const b = document.querySelector('.modal [data-vact="lrCopy"]');
  return !!b && !!b.querySelector('svg') && !b.textContent.trim();
});

// 연구소 둘을 체크한다 — 줄이 같이 펼쳐지면 안 된다(줄 전체가 펼치기 버튼이다).
const A = out.ckRows[1], B = out.ckRows[2];   // [0]은 전체 줄이다
out.names = {};
for (const k of [A, B]) out.names[k] = (R0.find((r) => r.ck === k) || {}).label;
await page.click('.modal input[data-vlrck="' + A + '"]'); await page.waitForTimeout(400);
await page.click('.modal input[data-vlrck="' + B + '"]'); await page.waitForTimeout(400);
out.opened = await page.evaluate(() => Object.keys(S.ov.lrOpen || {}).length);
out.sel = await page.evaluate(() => Object.keys(S.ov.lrSel || {}));
out.checkedAfter = (await rowsOf()).filter((r) => r.checked).map((r) => r.ck);

// 복사 — 체크한 두 연구소만(파트 줄까지) 담겨야 한다.
await page.click('.modal [data-vact="lrCopy"]'); await page.waitForTimeout(600);
out.copied = await page.evaluate(() => window.__COPIED);
out.unchecked = R0.filter((r) => r.ck && r.ck !== 'ALL' && r.ck !== A && r.ck !== B).map((r) => r.label);

// 체크를 모두 풀면 표 전체가 복사된다.
await page.evaluate(() => { window.__COPIED = null; S.ov.lrSel = {}; render(); });
await page.waitForTimeout(400);
await page.click('.modal [data-vact="lrCopy"]'); await page.waitForTimeout(600);
out.copiedAll = await page.evaluate(() => window.__COPIED);

// 펼친 금액 줄도 같이 담긴다(보이는 표를 그대로 복사한다).
await page.evaluate((a) => { window.__COPIED = null; S.ov.lrSel = {}; S.ov.lrSel[a] = 1; render(); }, A);
await page.waitForTimeout(300);
await page.evaluate(() => { const b = document.querySelector('.modal [data-vlrall]'); if (b) b.click(); });
await page.waitForTimeout(700);
await page.click('.modal [data-vact="lrCopy"]'); await page.waitForTimeout(600);
out.copiedOpen = await page.evaluate(() => window.__COPIED);

out.errs = errs;
await bye(out);
