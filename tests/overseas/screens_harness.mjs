// 화면이 **열리는지** 확인한다. frontend-html을 정적 서버로 띄우고 헤드리스 브라우저로 화면을
// 하나씩 방문해서, JS 에러가 났는지 · 본문이 비어 있지 않은지만 본다.
//
// 왜 글자를 저장해 두고 비교하지 않는가(2026-11-18): 이 프로젝트의 화면은 자주 바뀐다. "전과
// 같은 글자가 나와야 한다"로 묶어 두면 문구 하나 고칠 때마다 정답 파일도 같이 고쳐야 하고,
// 안 고치면 테스트가 틀렸다고 울린다 — 금방 아무도 안 보게 된다. 숫자가 맞는지는 백엔드
// 테스트가 이미 지키므로, 여기서는 **"코드를 지웠더니 어느 화면이 깨졌다"** 한 가지만 잡는다.
//
// 쓰는 쪽: test_screens.py가 --api(백엔드 주소)를 주고 stdout의 JSON을 읽는다.
// playwright가 없으면 그 쪽에서 스킵한다(설치: npm i -D playwright && npx playwright install chromium).

import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FE = path.resolve(HERE, '../../../frontend-html');  // tests/overseas → tests → backend → S.LSI

const args = Object.fromEntries(
  process.argv.slice(2).map((a) => {
    const i = a.indexOf('=');
    return i < 0 ? [a.replace(/^--/, ''), true] : [a.slice(2, i), a.slice(i + 1)];
  })
);
const API = args.api || 'http://127.0.0.1:8000';

// 화면 하나 = [제목, 주소, 그 화면 상태를 만드는 스크립트]
// 주소만으로 못 가는 화면(단계·탭·모달)은 스크립트로 상태를 세운다 — 실제 사용자는 버튼을 누르지만
// 여기서는 "그 화면이 그려지는가"만 보면 되므로 상태를 직접 세우는 게 빠르고 덜 깨진다.
const SCREENS = [
  ['전체 현황', '/index.html#/', null],
  ['감가상각비', '/index.html#/dep', null],
  ['위탁용역비', '/index.html#/a/outsource', null],
  ['생소비', '/index.html#/a/consum', null],
  ['정률기술료', '/index.html#/a/ratetech', null],
  ['시장조사비', '/index.html#/a/market', null],
  ['CAE', '/index.html#/a/cae', null],
  ['해외연구소(계정 메뉴)', '/index.html#/a/overseas', null],
  ['기준정보 · 비용계정', '/index.html#/ref/acct', null],
  ['기준정보 · 환율', '/index.html#/ref/fx', null],
  ['기준정보 · 버전', '/index.html#/ref/ver', null],
  ['기준정보 · 제품정보', '/index.html#/ref/prod', null],
  ['기준정보 · PLM 과제', '/index.html#/ref/plm', null],
  ['실적 · 전년', '/index.html#/data/prior', null],
  ['실적 · 경영계획', '/index.html#/data/plan', null],
  ['분석 · 증감분석', '/index.html#/ana/overseas', null],
  ['마감 1단계 · 취합', '/index.html#/close', 'clsSt().step=0;render();'],
  ['마감 2단계 · 매핑(해외연구소)', '/index.html#/close', 'const C=clsSt();C.step=1;C.tab="overseas";render();'],
  ['해외연구소 1단계 · 취합', '/overseas.html#/', 'S.ov.step=0;render();'],
  ['해외연구소 2단계 · 집계', '/overseas.html#/', 'S.ov.step=1;render();'],
  ['해외연구소 · 인건비율 팝업', '/overseas.html#/', 'S.ov.step=1;S.ov.lrPop=true;S.ov.lrOpen={};S.ov.lrOpen[OV_ALLPK.join(",")]=1;render();'],
  ['해외연구소 · 기준정보', '/overseas.html#/', 'S.ov.view="ref";render();'],
];

const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8' };

function serveFrontend() {
  const srv = http.createServer((req, res) => {
    const url = req.url.split('?')[0].split('#')[0];
    const file = path.join(FE, url === '/' ? 'index.html' : url);
    if (!file.startsWith(FE)) { res.writeHead(403); res.end(); return; }
    fs.readFile(file, (err, buf) => {
      if (err) { res.writeHead(404); res.end('not found'); return; }
      res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream' });
      res.end(buf);
    });
  });
  return new Promise((r) => srv.listen(0, '127.0.0.1', () => r(srv)));
}

const out = { api: API, screens: [] };
let chromium;
try {
  ({ chromium } = await import('playwright'));
} catch (e) {
  out.skip = 'playwright가 없다: ' + e.message;
  console.log(JSON.stringify(out));
  process.exit(0);
}

const srv = await serveFrontend();
const base = 'http://127.0.0.1:' + srv.address().port;
let browser;
try {
  browser = await chromium.launch();
} catch (e) {
  srv.close();
  out.skip = '브라우저를 띄울 수 없다(npx playwright install chromium): ' + e.message;
  console.log(JSON.stringify(out));
  process.exit(0);
}

for (const [name, url, script] of SCREENS) {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e && e.message || e)));
  // core.js는 window.OV_API_BASE가 없으면 localhost:8000으로 간다 — 테스트가 띄운 백엔드로 돌린다.
  await page.addInitScript((api) => { window.OV_API_BASE = api; }, API);
  try {
    await page.goto(base + url, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(1200);
    if (script) await page.evaluate(script);
    await page.waitForTimeout(2500);
    const text = await page.evaluate(() => document.body.innerText.replace(/\s+/g, ' ').trim());
    out.screens.push({ name, url, len: text.length, errors });
  } catch (e) {
    out.screens.push({ name, url, len: 0, errors: errors.concat(['열지 못했다: ' + e.message]) });
  }
  await page.close();
}
await browser.close();
srv.close();
console.log(JSON.stringify(out));
