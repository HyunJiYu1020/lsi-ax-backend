// 업로드 현황 ✓ 판정 검증 — core.js를 Node에서 그대로 실행한다(js_formula_harness.mjs와 같은 방식).
//
// 무엇을 막는가: DB에 그 달 행이 하나도 없는데(select * from cost_entries where yyyymm='202611'
// → 0행) 업로드 현황 격자가 전부 ✓로 보이던 사고다. 원인이 두 겹이었다 —
//   ① 프런트가 /overseas/state에 **연도만** 보내서, 서버가 "DB의 최신 속보월"로 짐작한 달의
//      데이터를 내려줬다(서버 쪽은 test_state_roundtrip.py가 본다).
//   ② 버전을 만들 때 직전 버전을 통째로 복사해 넣어서(ovVerCopy), 서버가 빈 달을 줘도
//      브라우저 버킷에 남은 복사본 때문에 ✓가 켜졌다. ovHas가 보는 건 DB가 아니라 이 버킷이다.
// 여기서는 ②와 ①의 프런트 쪽(달을 보내는지)을 고정한다. 통과하면 조용히 끝나고, 깨지면
// 어느 항목이 왜 깨졌는지 찍고 exit 1 한다(test_upload_status_grid.py가 그 코드를 본다).
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../../..");  // tests/overseas/ → tests → backend → S.LSI
const CORE_JS = path.join(REPO, "frontend-html/shared/core.js");

function browserStub() {
  const make = () => new Proxy(function () {}, {
    get: (_t, k) => (k === Symbol.toPrimitive ? () => "" : k === "length" ? 0 : make()),
    set: () => true, apply: () => make(), construct: () => make(), has: () => true,
  });
  const real = {
    console, Math, JSON, Date, Array, Object, String, Number, Boolean, RegExp, Error,
    Map, Set, WeakMap, Promise, Symbol, Intl, isFinite, isNaN, parseFloat, parseInt,
    encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout,
  };
  return new Proxy({ ...real }, {
    get: (t, k) => (k in t ? t[k] : make()),
    set: (t, k, v) => ((t[k] = v), true),
    has: () => true,
    getOwnPropertyDescriptor: (t, k) => k in t
      ? Object.getOwnPropertyDescriptor(t, k)
      : { value: make(), writable: true, enumerable: true, configurable: true },
  });
}

// 없는 이름을 적으면 VM 스텁 프록시가 잡혀서 **무조건 truthy**가 된다 — 테스트가 통과한 것처럼
// 보이므로, 이름을 바꿀 때는 여기도 같이 고쳐야 한다(ovHasS를 지웠을 때 실제로 그랬다).
const EXPORTS = ["S", "ovEnsure", "ovVD", "ovBind", "ovSrvHasS", "ovHas", "ovStateQ",
  "ovDropLocalData", "verSelect", "verCreate", "verGet", "fxYM", "OV_LABS", "OV_SHEETS", "ovPks", "ovL"];
const src = fs.readFileSync(CORE_JS, "utf8");
const C = vm.runInContext(src + `\n;({ ${EXPORTS.join(", ")} })`,
  vm.createContext(browserStub()), { filename: CORE_JS });

let fails = 0;
const ok = (cond, msg) => { console.log((cond ? "  PASS  " : "  FAIL  ") + msg); if (!cond) fails++; };

C.ovEnsure();

// ---------- 1) 월 버전을 고르면 /overseas/state에 그 달을 같이 보낸다 ----------
console.log("\n[1] ovStateQ — 보고 있는 달을 서버에 알려준다");
if (!C.verGet("2026-11-S")) C.verCreate({ y: 2026, m: 11, kind: "forecast" });
C.verSelect("2026-11-S");
const q11 = C.ovStateQ();
ok(/basis_year=2026/.test(q11), `연도를 보낸다: ${q11}`);
ok(/basis_month=11/.test(q11), `11월을 보낸다: ${q11}`);

C.verSelect("2026-09-S");
ok(/basis_month=9(&|$)/.test(C.ovStateQ()), `9월로 바꾸면 9를 보낸다: ${C.ovStateQ()}`);

const planId = (C.S.vers.list.find(v => v.kind === "plan") || {}).id;
C.verSelect(planId);
ok(!/basis_month/.test(C.ovStateQ()), `경영계획은 달을 안 보낸다: ${C.ovStateQ()}`);

// ---------- 2) 새 버전은 빈 상태로 열린다 (verCreate가 복사하지 않는다) ----------
console.log("\n[2] verCreate — 새 버전은 직전 버전을 복사하지 않는다");
const newId = "2026-12-S";
// 복사 원본이 될 직전 버전에 값을 넣어 둔다
const src11 = C.ovVD("2026-11-A");
src11.d = { SARC_CPU: { sal: Array(12).fill(5) } };
src11.files = { SARC_CPU: { file: "OV_SARC_2611.xlsx", at: "11.18 10:00" } };
if (C.verGet(newId)) delete C.S.ov.VD[newId];
C.S.vers.list = C.S.vers.list.filter(v => v.id !== newId);
C.verCreate({ y: 2026, m: 12, kind: "forecast" });
const made = C.ovVD(newId);
ok(Object.keys(made.d).length === 0, "새 버전의 비용 버킷이 비어 있다");
ok(Object.keys(made.files).length === 0, "새 버전에 '복사' 파일 기록이 안 생긴다");

// ---------- 3) 브라우저에 남은 해외연구소 값은 한 번 싹 비운다 ----------
// 정책이 바뀌었다: 전에는 "'… 복사' 버킷만" 비우고 업로드 · 예시 버킷은 지켰는데, 지켜 둔 그
// 예시 버킷이 바로 "DB엔 없는데 화면엔 숫자가 있다"의 출처였다(예시 시드 ovSeedX · 자동 생성
// 예시 버전 ovVerExamples). 지금은 가림 없이 전부 비우고 /overseas/state 로드가 DB 값으로 다시
// 채운다(ovApplyApiState가 비용 · 증감사유 · 외주용역 · CP를 자리별로 꽂는다).
console.log("\n[3] ovDropLocalData — 버킷을 가림 없이 비운다");
const copied = C.ovVD("2026-07-S"), real = C.ovVD("2026-06-S"), ex = C.ovVD("2026-05-S");
copied.d = { SARC_CPU: { sal: Array(12).fill(9) } };
copied.files = { SARC_CPU: { file: "OV_SARC_2606.xlsx", at: "26.06 실적 복사" } };
const sarcPks = C.ovPks(C.ovL("SARC"));
real.d = Object.fromEntries(sarcPks.map(pk => [pk, { sal: Array(12).fill(7) }]));
real.files = { SARC_CPU: { file: "OV_SARC_2606.xlsx", at: "06.15 09:30" } };
ex.d = { SARC_CPU: { sal: Array(12).fill(3) } };
ex.U = { SARC: { items: [{ comp: "Vendor SARC-01", m: Array(12).fill(1) }], at: "예시" } };
ex.R = { SARC: { rows: {}, at: "예시" } };
ex.files = { SARC_CPU: { file: "OV_SARC_2605.xlsx", at: "예시" } };
// 반영 전 대기 파일은 서버에 아직 없다 — 여기서 지우면 다시 올릴 길이 없으므로 남겨야 한다.
C.S.ov.pending = [{ id: "p1", ym: "202611", errs: [], lost: [] }];
C.S.ov.nolocal = 0;
C.ovDropLocalData();
ok(Object.keys(copied.d).length === 0, "'… 복사' 버킷은 비워진다");
ok(Object.keys(copied.files).length === 0, "'… 복사' 파일 기록도 지워진다");
ok(Object.keys(real.d).length === 0, "업로드로 들어온 버킷도 비운다 — 서버가 다시 채운다");
ok(Object.keys(ex.d).length === 0 && Object.keys(ex.U).length === 0 && Object.keys(ex.R).length === 0,
  "예시 버킷도 비운다 (비용 · 외주용역 · 증감사유)");
ok(C.S.ov.pending.length === 1, "반영 전 대기 파일은 남긴다");
// 플래그가 섰으면 두 번째 호출은 아무것도 안 건드린다
C.ovVD("2026-07-S").d = { SARC_CPU: { sal: Array(12).fill(1) } };
C.ovDropLocalData();
ok(C.S.ov.nolocal === 1 && Object.keys(C.ovVD("2026-07-S").d).length === 1,
  "두 번 돌려도 한 번만 동작한다(플래그)");

// ---------- 4) 업로드 현황 ✓는 **서버에 저장된 것**만 센다 ----------
// 화면 버킷(d)은 [반영]만 눌러도 채워진다 — 그걸로 ✓를 켜면 cost_entries에 한 행도 없는 달이
// 전 연구소 ✓로 보인다. 그래서 판정은 ovApplyApiState가 서버 응답으로 적어 두는 srv만 본다.
console.log("\n[4] ovSrvHasS — ✓는 서버가 준 자리에만 켜진다");
C.verSelect("2026-07-S");
C.ovBind();
const anyChecked = C.OV_LABS.some(L => C.ovSrvHasS("cost", "cur", L.c));
ok(!anyChecked, "복사본을 비운 달은 전 연구소 비용이 '미'다");

// 화면에는 값이 있는데 서버에는 없는 달(= 반영만 하고 저장하지 않은 상태)
const localOnly = C.ovVD("2026-07-S");
localOnly.d = Object.fromEntries(sarcPks.map(pk => [pk, { sal: Array(12).fill(4) }]));
ok(!C.ovSrvHasS("cost", "cur", "SARC"), "반영만 하고 저장하지 않은 달은 '미'다");

// 서버가 내려준 자리(srv)에만 ✓가 켜진다. SARC는 파트가 둘이라 둘 다 있어야 한다.
localOnly.srv = { cost: { [sarcPks[0]]: 1 }, rsn: {}, outs: {} };
ok(!C.ovSrvHasS("cost", "cur", "SARC"), "파트 한쪽만 저장돼 있으면 아직 '미'다");
localOnly.srv.cost[sarcPks[1]] = 1;
ok(C.ovSrvHasS("cost", "cur", "SARC"), "서버에 저장된 달은 ✓가 켜진다");
ok(!C.ovSrvHasS("rsn", "cur", "SARC"), "증감사유는 따로 센다 — 비용만 저장됐으면 '미'다");
localOnly.srv.rsn.SARC = 1;
ok(C.ovSrvHasS("rsn", "cur", "SARC"), "증감사유가 서버에 있으면 ✓가 켜진다");

console.log(fails ? `\n${fails}건 실패` : "\n전부 통과");
process.exit(fails ? 1 : 0);
