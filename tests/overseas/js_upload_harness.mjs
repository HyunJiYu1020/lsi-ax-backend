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
  fs.readFileSync(CORE_JS, "utf8") + "\n;({S, ovHandleSheets, ovModHtml, ovModOpen, ovPkName, parseCSV, ovTxtSheets, ovTxtSep, ovFormDl});\n",
  ctx, { filename: CORE_JS });

const posts = [];
ctx.fetch = (url, opt) => {
  posts.push({ url: String(url), body: opt && opt.body ? JSON.parse(opt.body) : null });
  return { then: () => ({ then: () => ({ catch: () => {} }), catch: () => {} }), catch: () => {} };
};
ctx.render = () => {};

core.S.basis = input.basis || "2026-11";
core.S.ov = { upVer: "cur", lab: input.lab || "SIRC", part: "", pending: [], V: { cur: { d: {} } }, files: {}, R: {}, U: {} };

// pastes: 시트 내용을 **원문 텍스트**로 준 경우 — core의 parseCSV로 직접 돌린다. 엑셀 클립보드와
// 탭 구분 .txt는 숫자에 천단위 콤마가 섞여 오므로, 표(aoa)로 미리 만들어 넣으면 바로 그 깨짐을
// 못 본다(문자열을 표로 자르는 자리가 바로 parseCSV다).
const rawSheets = (input.pastes || []).filter((x) => x.text && x.text.trim())
  .map((x) => ({ name: x.name, aoa: core.parseCSV(x.text) }));
// text: 텍스트 파일(.txt) **원문** — 구분자 줄로 블록을 쪼개는 길을 그대로 지난다(ovTxtSheets).
// 여기도 표(aoa)로 미리 만들어 넣으면 안 된다: 쪼개지는 자리가 바로 그 원문이기 때문이다.
// form:"txt" — **텍스트 양식을 만들어 그대로 다시 올린다**(왕복). 양식이 쓰는 구분자와 업로드가
// 받는 구분자가 갈리면(한쪽 이름만 바뀌면) 연구소가 채워 보낸 파일이 안 읽히는데, 사람이 양식을
// 받아서 올려 보기 전까지는 아무도 그걸 모른다. downloadBlob을 가로채 파일 내용을 그대로 쓴다.
let form = null;
if (input.form === "txt") {
  // [양식 받기] 한 번이 파일을 **여러 개** 떨어뜨린다(공용 한 개 + 파트가 나뉜 연구소마다 한 개).
  // 마지막 것만 붙잡으면 어느 연구소를 시험해도 파트 양식만 올려 보게 된다 — 전부 받아 두고,
  // 그 연구소가 실제로 받는 파일을 고른다: 파일명에 연구소 코드가 붙은 것이 있으면 그것,
  // 없으면 공용(코드가 안 붙은 첫 파일)이다.
  const got = [];
  ctx.downloadBlob = (name, blob) => { got.push({ name, blob }); };
  core.ovFormDl("txt");
  const lab = input.lab || "SIRC";
  const pick = got.find((g) => g.name.includes("_" + lab + "_")) || got[0];
  form = pick ? { name: pick.name, text: await pick.blob.text(), files: got.map((g) => g.name) } : null;
}
const text = form ? form.text : input.text;
const split = text ? core.ovTxtSheets(text, input.lab || "SIRC") : null;
// 구분자를 못 찾은 텍스트 파일은 종전처럼 시트 한 장으로 본다 — ovHandleOneFile이 하는 그대로다
// (구분자가 없는 파일이 "읽을 시트가 없다"로 거절되면 안 된다).
const txtSheets = text
  ? split || [{ name: input.file, aoa: core.parseCSV(text, core.ovTxtSep(text)) }]
  : null;
const err = core.ovHandleSheets(txtSheets || (rawSheets.length ? rawSheets : input.sheets), input.file);
const scans = posts.filter((p) => p.url.includes("anomaly-scan")).map((p) => p.body);
const pends = core.S.ov.pending.map((p, i) => ({
  pk: p.pk,
  sheet: p.sheet || "",
  cost_rows: p.cost ? p.cost.n : 0,
  has_rsn: !!p.rsn,
  has_outs: !!p.outs,
  errs: p.errs || [],
  // lost: 양식에 있어야 하는데 안 온 시트. 담당자가 연구소에 재요청해야 하는 줄이다.
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
// split: 구분자로 쪼갠 결과(블록 이름 = 양식 시트명 · 줄 수). null이면 구분자를 못 찾아
// "파일 한 개 = 시트 한 장"으로 간 것이다 — 그 둘을 테스트가 구분해야 한다.
process.stdout.write(JSON.stringify({ error: err || "", pends, modal,
  split: split && split.map((x) => ({ name: x.name, rows: x.aoa.length })),
  // files: [양식 받기] 한 번에 떨어진 **파일 이름 전부**. 올려 보는 건 그중 한 개지만, 몇 개가
  // 나오고 이름에 연구소 코드가 박혀 있는지도 고정해야 한다 — 연구소 이름이 들어간 양식이
  // 다시 생기면 담당자가 그 파일을 다른 연구소에 돌려 쓸 수 없다.
  form: form && { name: form.name, text: form.text, files: form.files } }));
