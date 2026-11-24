// 업로드를 **사람이 하는 그대로** 해 본다 — 헤드리스 브라우저로 취합 화면을 열고, 파일 선택
// 칸에 실제 .xlsx를 넣고, [미리보기]를 열어 보고, [반영]을 누른 뒤 그리드 숫자를 읽어 온다.
//
// 왜 Node에서 core.js만 실행하는 하네스(js_upload_harness.mjs)로 부족한가: 그쪽은 **이미 표로
// 풀어 놓은 시트**(aoa)를 넣는다. 실제 업로드는 그 앞에 두 단계가 더 있다 —
//   ① 파일 선택 · change 이벤트 · arrayBuffer
//   ② .xlsx 압축 해제와 XML 해석(XL.read: DecompressionStream · DOMParser)
// 이 두 단계는 브라우저 기능이라 vm 스텁에서는 아예 돌지 않는다. 그리고 반영 뒤에 "그리드에
// 엑셀대로 들어왔는지"는 ovCostView가 그린 <td>를 읽어야 확인되는 것이라, 상태(S.ov.V)만 봐서는
// 사람이 보는 화면과 같다고 말할 수 없다.
//
// 서버에는 **쓰지 않는다**: GET과 이상 감지(anomaly-scan)만 실제 백엔드로 나가고, 나머지 쓰기
// 요청(PUT /overseas/state 등)은 이 하네스가 받아서 기록만 한다(writes). 그래서 이 테스트를
// 돌려도 개발 DB의 실적이 바뀌지 않는다 — 서버가 저장을 제대로 받는지는 파이썬 쪽
// (test_state_roundtrip.py · test_bulk_upsert.py)이 본다.
//
// 쓰는 쪽: test_upload_to_grid.py가 --api(백엔드) · --file(올릴 엑셀) · --lab을 주고 stdout의
// JSON 한 줄을 읽는다. playwright가 없거나 브라우저를 못 띄우면 skip을 담아 0으로 끝낸다.

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
const FILE = args.file;
const LAB = args.lab || 'SIRC';

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

const out = { api: API, file: FILE, errors: [], writes: [], dialogs: [] };
const die = (msg) => { out.errors.push(msg); console.log(JSON.stringify(out)); process.exit(0); };

if (!FILE || !fs.existsSync(FILE)) die('올릴 파일이 없다: ' + FILE);

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

// 화면에서 표 하나를 읽어 오는 함수 — 미리보기 모달과 취합 그리드가 같은 구조(.ovt)라 같이 쓴다.
// 칸마다 글자 · 이상 표시 class(ovf-*) · 설명(title)을 가져온다: "검증에 걸린 칸"은 사람이 그
// 셋으로 알아보기 때문이다.
const SCRAPE = (sel) => {
  // 한 화면에 .ovt 표가 여럿이다(업로드 현황 격자 · 비용 그리드 · 미리보기). 머리글에
  // 'Cost Element'가 있는 표만 고른다 — 순서로 집으면 패널이 하나 늘 때 조용히 엉뚱한 표를 읽는다.
  const tb = [...document.querySelectorAll(sel)]
    .find((t) => [...t.querySelectorAll('thead th')].some((th) => /Cost Element/i.test(th.textContent)));
  if (!tb) return null;
  const head = [...tb.querySelectorAll('thead th')].map((th) => th.textContent.trim());
  const rows = [...tb.querySelectorAll('tbody tr')].map((tr) => {
    const td = [...tr.children];
    const lb = td.find((x) => x.classList.contains('lb'));
    const en = td.find((x) => x.classList.contains('en'));
    return {
      ko: lb ? lb.textContent.trim() : '',
      en: en ? en.textContent.trim() : '',
      cells: td.filter((x) => x.classList.contains('n')).map((x) => ({
        txt: x.textContent.trim(),
        flag: [...x.classList].filter((c) => c.startsWith('ovf-') || c === 'mc-bad').join(' '),
        title: x.getAttribute('title') || '',
      })),
    };
  });
  return { head, rows };
};

const page = await browser.newPage();
page.on('pageerror', (e) => out.errors.push('JS 에러: ' + String((e && e.message) || e)));
// 이미 올라온 연구소에 또 올리면 "덮어쓸까요?"를 묻는다(ovOverwriteOk). 그냥 두면 playwright가
// 자동으로 취소를 눌러서 반영이 안 된다 — 사람이 [확인]을 누르는 쪽으로 받아 준다.
page.on('dialog', (d) => { out.dialogs.push(d.message().replace(/\s+/g, ' ').trim().slice(0, 200)); d.accept(); });
await page.addInitScript((api) => { window.OV_API_BASE = api; }, API);

// 반영을 누른 뒤부터는 /overseas/state 재조회를 막는다. 저장이 성공하면 화면은 서버 값을 다시
// 받아 맞추는데(ovAfterSave), 이 테스트는 서버에 쓰지 않으므로 그 GET은 **방금 올린 값이 아닌
// 예전 DB 값**을 돌려준다 — 그걸 적용하면 그리드가 업로드 전 숫자로 되돌아간다. 그래서 막는다.
// core.js는 재조회 실패를 "저장은 끝난 일"로 보고 화면 값을 그대로 둔다(ovAfterSave의 catch).
// 저장 → 재조회 왕복 자체는 서버 쪽 테스트(test_state_roundtrip.py)가 본다.
let blockStateGet = false;

// 쓰기 요청은 서버로 보내지 않는다(위 주석 참고). anomaly-scan은 POST지만 읽기라 통과시킨다.
await page.route('**/api/v1/**', async (route) => {
  const req = route.request();
  const url = req.url();
  if (req.method() === 'GET' && blockStateGet && /\/overseas\/state/.test(url)) {
    return route.fulfill({ status: 503, contentType: 'application/json', body: '{}' });
  }
  if (req.method() === 'GET' || url.includes('anomaly-scan')) return route.continue();
  out.writes.push(req.method() + ' ' + url.replace(API, ''));
  await route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ ok: true, state_token: 'harness-token' }),
  });
});

try {
  await page.goto(base + '/overseas.html#/', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(1500);
  // 1단계(취합) · 비용 시트 · 대상 연구소를 세워 둔다. 파일명으로도 연구소가 잡히지만(SIRC),
  // 그 판정 자체는 js_upload_harness 쪽에서 보므로 여기서는 화면 상태를 분명히 해 둔다.
  await page.evaluate((lab) => { S.ov.step = 0; S.ov.sheet = 'cost'; S.ov.lab = lab; render(); }, LAB);
  await page.waitForTimeout(800);

  out.unit = await page.evaluate(() => ovUsdUnitName());

  // ---------- 반영 전: 그리드는 비어 있어야 한다 ----------
  // "미리보기에서 걸러진다"는 말은 **반영을 누르기 전에는 화면 값이 바뀌지 않는다**는 뜻도 된다.
  out.before = await page.evaluate(SCRAPE, '.panel table.ovt');

  // ---------- ① 파일 선택 (사람이 하는 그대로) ----------
  await page.setInputFiles('#ovFile', FILE);
  await page.waitForSelector('.pend', { timeout: 20000 });
  out.card = await page.evaluate(() => {
    const p = document.querySelector('.pend');
    return { text: p.textContent.replace(/\s+/g, ' ').trim().slice(0, 600) };
  });

  // ---------- ② 미리보기 모달 ----------
  // 업로드하면 미리보기가 **저절로** 열린다(ovHandleSheets → ovModOpen). 그래서 열려 있지 않을
  // 때만 대기 카드의 [미리보기]를 누른다 — 열려 있는데 누르면 모달 뒤판에 가려 클릭이 막힌다.
  out.modalAuto = await page.evaluate(() => !!document.querySelector('.modal table.ovt'));
  if (!out.modalAuto) await page.click('.pend [data-vact="modOpenAll"]');
  await page.waitForSelector('.modal table.ovt', { timeout: 20000 });
  await page.waitForTimeout(2500);   // 이상 감지(anomaly-scan) 응답을 받아 다시 그릴 시간
  // 서버가 내려준 지적(findings)과 못 한 검사(skipped)를 카드별로 받아 둔다 — "화면에 붙었는가"를
  // 이것과 DOM title을 맞춰 보는 식으로 확인한다(서버 판정 자체는 test_anomaly.py가 본다).
  out.scan = await page.evaluate(() => (S.ov.pending || []).map((p) => ({
    pk: p.pk, sheet: p.sheet || '',
    findings: (((p.anom || {}).findings) || []).map((f) => ({
      rule: f.rule, tip: f.rule_label + ': ' + f.message, cat: f.category_key,
      month: f.month == null ? null : f.month, months: f.months || null,
      row: f.row_label || null, block: f.block || null, fields: f.fields || null,
    })),
    skipped: ((p.anom || {}).skipped) || [],
  })));
  // 탭마다 눌러서 그 탭에 그려진 설명(title)을 모은다 — 비용 · 증감사유 · 외주용역이 다른 탭이라
  // 한 탭만 보면 "증감분석 지적이 어디에도 안 보인다"를 놓친다.
  const tabN = await page.evaluate(() => document.querySelectorAll('.modal [data-vmodtab]').length);
  out.tabs = [];
  for (let i = 0; i < Math.max(1, tabN); i++) {
    if (tabN) { await page.click(`.modal [data-vmodtab="${i}"]`); await page.waitForTimeout(400); }
    out.tabs.push(await page.evaluate(() => ({
      label: ([...document.querySelectorAll('.modal [data-vmodtab]')]
        .find((b) => b.getAttribute('aria-selected') === 'true') || {}).textContent || '',
      titles: [...document.querySelectorAll('.modal [title]')].map((x) => x.getAttribute('title')),
      flagged: document.querySelectorAll('.modal .ovf-err, .modal .ovf-warn, .modal .ovf-info, .modal .ovf-rowline').length,
    })));
  }
  if (tabN) { await page.click('.modal [data-vmodtab="0"]'); await page.waitForTimeout(300); }
  out.modal = await page.evaluate(SCRAPE, '.modal table.ovt');
  out.modalText = await page.evaluate(() => document.querySelector('.modal').textContent.replace(/\s+/g, ' ').trim());
  await page.click('.modal [data-vact="modClose"]');
  await page.waitForTimeout(400);

  // ---------- ③ 반영 ----------
  // 대기 카드는 파일이 몇 개든 **한 줄 요약**이다(열어서 확인 · 전체 반영 · 전체 취소).
  // 파일 하나하나의 [반영]은 미리보기 창 안에 있다 — 거기서 행 맞추기 같은 질문에 답하므로
  // 답하고 창을 닫았다가 카드에서 다시 누르게 하지 않는다. 여기서는 창을 닫은 뒤라 [전체 반영]이다.
  blockStateGet = true;
  await page.click('.pend [data-vact="pendApplyAll"]');
  await page.waitForTimeout(2500);
  out.pendLeft = await page.evaluate(() => document.querySelectorAll('.pend').length);

  // ---------- ④ 그리드 ----------
  out.grid = await page.evaluate(SCRAPE, '.panel table.ovt');
  out.msg = await page.evaluate(() => {
    const m = document.querySelector('.umsg');
    return m ? m.textContent.replace(/\s+/g, ' ').trim() : '';
  });
} catch (e) {
  out.errors.push('진행하지 못했다: ' + String((e && e.message) || e));
}

await browser.close();
srv.close();
console.log(JSON.stringify(out));
