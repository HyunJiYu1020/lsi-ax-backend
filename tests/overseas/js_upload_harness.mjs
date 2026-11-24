// 프런트(frontend-html/shared/core.js)의 업로드 파싱·검증 payload 생성을 Node에서 그대로 실행해
// JSON으로 돌려준다. test_upload_validation_crosscheck.py가 stdin으로 {file, sheets}를 넣고
// stdout에서 {pends:[{pk, payload}]}를 받는다.
//
// 왜 필요한가: "빈칸은 null · 0은 0"이라는 구분이 프런트 파싱에서 시작해 서버 기준(missing_cell)
// 까지 이어져야 의미가 있다. 둘 중 한쪽만 고치면 "0으로 보고한 달"이 전부 미제출로 걸리거나
// (경고가 무용해진다) 미제출이 조용히 0으로 들어간다. 그래서 파싱을 파이썬으로 베껴 적지 않고
// 프런트를 실제로 실행해서 그 payload를 서버 판정 함수에 먹인다.
//
// core.js는 모듈이 아니라 브라우저 전역 스크립트라 import할 수 없다 — js_formula_harness.mjs와
// 같은 방법(DOM·fetch stub + vm + 끝에 객체 리터럴)을 쓴다. fetch는 가로채서 검증 payload를 꺼낸다.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Blob } from "node:buffer";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CORE_JS = path.join(path.resolve(HERE, "../../.."), "frontend-html/shared/core.js");

function browserStub() {
  const make = () =>
    new Proxy(function () {}, {
      get: (_t, k) => (k === Symbol.toPrimitive ? () => "" : k === "length" ? 0 : make()),
      set: () => true, apply: () => make(), construct: () => make(), has: () => true,
    });
  const real = {
    console, Math, JSON, Date, Array, Object, String, Number, Boolean, RegExp, Error,
    Map, Set, WeakMap, Promise, Symbol, Intl, isFinite, isNaN, parseFloat, parseInt,
    encodeURIComponent, decodeURIComponent,
    TextEncoder, TextDecoder, Uint8Array, Uint32Array, DataView, ArrayBuffer, Blob,
  };
  return new Proxy({ ...real }, {
    get: (t, k) => (k in t ? t[k] : make()),
    set: (t, k, v) => ((t[k] = v), true),
    has: () => true,
    getOwnPropertyDescriptor: (t, k) =>
      k in t ? Object.getOwnPropertyDescriptor(t, k)
             : { value: make(), writable: true, enumerable: true, configurable: true },
  });
}

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const ctx = vm.createContext(browserStub());
const core = vm.runInContext(
  fs.readFileSync(CORE_JS, "utf8") + "\n;({S, ovHandleSheets, ovModHtml, ovModOpen, ovPkName, parseCSV});\n",
  ctx, { filename: CORE_JS });

const posts = [];
ctx.fetch = (url, opt) => {
  posts.push({ url: String(url), body: opt && opt.body ? JSON.parse(opt.body) : null });
  return { then: () => ({ then: () => ({ catch: () => {} }), catch: () => {} }), catch: () => {} };
};
ctx.render = () => {};

core.S.basis = input.basis || "2026-11";
core.S.ov = { upVer: "cur", lab: input.lab || "SIRC", part: "", pending: [], V: { cur: { d: {} } }, files: {}, R: {}, U: {} };

// pasted: 사람이 화면 칸에 직접 넣은 입력(텍스트 붙여넣기). 안 온 시트를 묻는 검사가 꺼진다.
// pastes: 붙여넣기 **원문 텍스트**를 준 경우 — core의 parseCSV로 직접 돌린다(ovHandleTextSheets가
// 하는 그대로). 엑셀 클립보드는 탭 구분인데 숫자에 천단위 콤마가 섞여 오므로, 표(aoa)로 미리
// 만들어 넣으면 바로 그 깨짐을 못 본다.
const pasted = (input.pastes || []).filter((x) => x.text && x.text.trim())
  .map((x) => ({ name: x.name, aoa: core.parseCSV(x.text) }));
const err = core.ovHandleSheets(pasted.length ? pasted : input.sheets, input.file,
  input.pasted ? { pasted: true } : undefined);
const scans = posts.filter((p) => p.url.includes("anomaly-scan")).map((p) => p.body);
const pends = core.S.ov.pending.map((p, i) => ({
  pk: p.pk,
  sheet: p.sheet || "",
  cost_rows: p.cost ? p.cost.n : 0,
  has_rsn: !!p.rsn,
  has_outs: !!p.outs,
  errs: p.errs || [],
  // lost: 양식에 있어야 하는데 안 온 시트. 담당자가 연구소에 재요청해야 하는 줄이라, 채울 수
  // 없는 입력(붙여넣기)에서 뜨면 헛경고가 된다.
  lost: p.lost || [],
  // 계정 목록에 없는 행 — 구분(label)과 Cost Element(label_en)를 **둘 다** 꺼낸다. 영문명은
  // 추가 행에만 쓰이고(고정 계정은 OV_EL의 en), 파싱이 흘리면 표의 Cost Element 칸이 빈
  // 입력창으로 남아 열 제목이 셀에 새어든 것처럼 보인다.
  unmatched: ((p.cost && p.cost.unmatched) || []).map((u) => ({ label: u.label, label_en: u.label_en || "" })),
  payload: scans[i] || null,
  // 파일의 기간 열 머리글 원문(col_key -> 글자). 취합 비용 표가 이 글자를 그대로 쓰고,
  // 백엔드 overseas_cost_headers에 같은 키로 저장된다 — 둘이 어긋나면 머리글이 사라지거나
  // 400으로 거절되므로 test_cost_headers_crosscheck가 이 값으로 양쪽을 맞춰 본다.
  hdr: p.cost ? p.cost.hdr || {} : null,
}));

// 모달도 한 번 그려 본다 — 파싱 결과가 바뀌면 여기서 먼저 터지게 해서 "빈 창"으로 나가는 걸 막는다.
let modal = { ok: false, tabs: 0, error: "" };
if (core.S.ov.pending.length) {
  try {
    core.ovModOpen(core.S.ov.pending.map((p) => p.id), 0);
    const html = core.ovModHtml();
    modal = { ok: html.length > 0, tabs: (html.match(/data-vmodtab=/g) || []).length, error: "" };
  } catch (e) {
    modal = { ok: false, tabs: 0, error: String((e && e.message) || e) };
  }
}
process.stdout.write(JSON.stringify({ error: err || "", pends, modal }));
