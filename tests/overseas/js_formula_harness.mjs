// 프런트(frontend-html/shared/core.js)의 계산 함수를 Node에서 그대로 실행해 결과를 JSON으로
// 돌려준다. test_formula_crosscheck.py가 stdin으로 {case, input, labs}를 넣고 stdout에서 {core}를 받는다.
//
// 왜 이런 harness가 필요한가: 같은 수식이 프런트와 백엔드 두 곳에 복제돼 있고(PLAN.md P1-4),
// 한 곳만 고치면 "화면 숫자 ≠ 챗봇 숫자"가 된다. 수식을 합치는 건 Phase 6이고, 그때까지는
// 어긋남을 즉시 감지하는 것만으로 사고를 막는다 — 그래서 수식을 파이썬으로 베껴 적지 않고
// 프런트를 실제로 실행해서 비교한다(베껴 적으면 그게 세 번째 복제본이 된다).
//
// 2026-11-16: React(frontend/src/logic/calc.js) 대조를 걷어냈다 — 쓰는 프런트는 frontend-html
// 하나이고 React는 지금 돌리지 않는다. 되살릴 때는 git 이력에서 꺼내면 된다.
//
// core.js는 모듈이 아니라 브라우저 전역 스크립트(8천 줄)라 import할 수 없다. 그래서:
//   ① DOM·fetch·localStorage를 전부 받아넘기는 stub 전역에 vm으로 통째로 실행하고
//   ② 스크립트 맨 끝에 "필요한 이름만 담은 객체 리터럴"을 붙여 완료값으로 꺼낸다
//      (최상위 const/let은 globalThis의 속성이 아니라 스크립트 스코프라 이 방법밖에 없다).

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../../..");  // tests/overseas/ → tests → backend → S.LSI
const CORE_JS = path.join(REPO, "frontend-html/shared/core.js");

// core.js에서 꺼내 쓸 이름. core.js가 이 중 하나를 지우거나 이름을 바꾸면 추출이 ReferenceError로
// 즉시 실패한다 — 의도된 동작이다(프런트의 수식 함수가 사라졌다는 건 대조 대상이 바뀌었다는 뜻이라
// 조용히 넘어가면 안 된다).
const CORE_EXPORTS = [
  "num", "ovL", "ovLcOf", "ovPks", "OV_LABS", "OV_EL", "OV_ELK", "OVQ",
  "ovPartEl", "ovAgg", "ovLaborSplit", "ov17", "ovPLRows", "ovPct17",
];

// core.js를 실행시키기 위한 가짜 브라우저. 무슨 속성을 읽어도 "호출해도 되고 또 아무 속성이나
// 읽어도 되는" 물건을 돌려준다 — core.js가 최상위에서 addEventListener·fetch·localStorage를
// 건드리는데 거기서 터지면 그 뒤에 있는 const 선언(OV_LABS 등)에 도달하지 못한다.
function browserStub() {
  const make = () =>
    new Proxy(function () {}, {
      get: (_t, k) => (k === Symbol.toPrimitive ? () => "" : k === "length" ? 0 : make()),
      set: () => true,
      apply: () => make(),
      construct: () => make(),
      has: () => true,
    });
  const real = {
    console, Math, JSON, Date, Array, Object, String, Number, Boolean, RegExp, Error,
    Map, Set, WeakMap, Promise, Symbol, Intl, isFinite, isNaN, parseFloat, parseInt,
    encodeURIComponent, decodeURIComponent,
  };
  return new Proxy({ ...real }, {
    get: (t, k) => (k in t ? t[k] : make()),
    set: (t, k, v) => ((t[k] = v), true),
    has: () => true,
    getOwnPropertyDescriptor: (t, k) =>
      k in t
        ? Object.getOwnPropertyDescriptor(t, k)
        : { value: make(), writable: true, enumerable: true, configurable: true },
  });
}

// labs를 주면 core.js의 OV_LABS를 그걸로 바꿔치운다. 실제 화면도 ovLabsFromApi로 서버 목록을 받아
// 덮어쓰므로(core.js의 하드코딩 OV_LABS0는 오프라인 초기값일 뿐), 대조할 때도 같은 목록을 넣어야
// "수식이 다른 것"과 "초기값 상수가 낡은 것"을 구분할 수 있다.
//
// setCache는 core.js의 ovAgg에 캐시를 꽂기 위한 것이다 — core.js의 ovAgg는 캐시를
// 인자로 받지 않고 전역 상태에서 ovCache(ver)로 꺼내 쓴다(그게 P1-4가 말하는 "전역 의존" 그 자체다).
// OV_LABS는 let, ovCache는 function 선언이라 둘 다 epilogue에서 재대입할 수 있고, ovL·ovAgg가 그
// 바인딩을 클로저로 잡고 있어서 재대입이 그대로 먹는다.
function loadCore(labs) {
  const src = fs.readFileSync(CORE_JS, "utf8");
  // OV_TBL_ORDER도 같이 바꿔야 한다 — ovTables()의 연구소 행 순서와 `ex()`(특정 연구소 제외)가
  // 이 배열을 훑으므로, OV_LABS만 바꾸면 없는 연구소를 찾다가 터진다(ovSyncLabs가 실제로 셋을
  // 한꺼번에 맞춘다).
  const inject = labs
    ? `\n;OV_LABS = ${JSON.stringify(labs)};OV_TBL_ORDER = OV_LABS.map((L) => L.c);` +
      `OV_ALLPK = [].concat(...OV_LABS.map(ovPks));`
    : "";
  const epilogue = `${inject}
;({ ${CORE_EXPORTS.join(", ")}, setCache: (c) => { ovCache = () => c; },
   // ovTables()는 ovAgg보다 전역 의존이 넓다(환율 · CP 계약 · 기준월). 수식은 그대로 돌리고
   // **입력만** 바꿔 꽂는다 — 여기서 ovCPc나 ovTables 자체를 흉내내면 그게 복제본이 된다.
   setTableInputs: (c, rate, cpPocs) => {
     ovCache = () => c;
     ovRateOV = () => rate;
     ovCpEnsure = () => ({ pocs: cpPocs });
     fxEnsure = () => {};
   },
   tables: () => ovTables(),
   // 증감분석은 버전이 둘이라 ovCache(ver)가 버전마다 다른 캐시를 줘야 한다.
   setVersionCaches: (cc, cb) => { S.ov.base = "prev"; ovCache = (v) => (v === "cur" ? cc : cb); },
   analysis: (per) => ovAnalysis(per, "ALL") })`;
  return vm.runInContext(src + epilogue, vm.createContext(browserStub()), { filename: CORE_JS });
}

const mon17 = (a) => Array.from({ length: 17 }, (_x, i) => (a && a[i] !== undefined ? a[i] : null));
const mon12 = (a) => Array.from({ length: 12 }, (_x, j) => (a && a[j] !== undefined ? a[j] : null));

// ovPartEl 결과를 묶어 "캐시"(el + rate) 모양으로 만든다. core.js의 ovPartEl은
// (pd, extra) 2인자지만 extra가 없으면 무시하므로 1인자로 부를 수 있다.
const buildCache = (impl, d, rate) => ({
  el: Object.fromEntries(Object.entries(d).map(([pk, pd]) => [pk, impl.ovPartEl(pd)])),
  rate,
});

// ---------- 대조 케이스 ----------
// 각 케이스는 고정 입력을 받아 {core}를 돌려준다. 파이썬 쪽에서 백엔드 결과와 비교한다.
const CASES = {
  // ovPartEl: 연구소가 올린 리프 값 → 손익계산서 계층 소계(labor = sal+bonus+welf 등)를 채운 맵.
  // 백엔드는 같은 일을 cost_groups.GROUP_CALC / resolve_leaves로 한다.
  part_el({ pd, keys }, core) {
    const pick = (el) => Object.fromEntries(keys.map((k) => [k, mon12(el[k])]));
    return { core: pick(core.ovPartEl(pd)) };
  },

  // ovAgg(krw): 연구소 묶음의 월별 원화 금액 = Σ리프 × (원/$) × 1000. 백엔드는 _krw_months가 같은 일을 한다.
  agg_krw({ d, pks, keys, rate }, core) {
    core.setCache(buildCache(core, d, { std: rate, cp: rate }));
    return {
      core: Object.fromEntries(keys.map((k) => [k, mon12(core.ovAgg(null, pks, k, "krw"))])),
    };
  },

  // ovLaborSplit: 인건비 증감을 인원 효과 · 단가 효과로 나눈다. 캐시(Cc/Cb)를 인자로 받는
  // 순수 함수라 고정 입력으로 바로 비교된다.
  labor_split({ pks, ix, cur, base }, core) {
    return {
      core: core.ovLaborSplit(
        pks, ix, buildCache(core, cur.d, cur.rate), buildCache(core, base.d, base.rate)
      ),
    };
  },

  // ovPLRows: 집계 ① 전체 계정 표(손익계산서형) 45행 × 17칸. 백엔드 pl_rows와 짝이다.
  pl_rows({ d, pks, mode, rate }, core) {
    const pick = (rows) =>
      rows.map((r) => ({ key: r.key ?? r.rk ?? null, f: r.f, v: mon17(r.v) }));
    core.setCache(buildCache(core, d, { std: rate, cp: rate }));
    return { core: pick(core.ovPLRows("cur", pks, mode)) };
  },

  // ov17: 12개월 → 17칸(월 12 + 분기 4 + 연간). 백엔드 to_17과 짝이다.
  // 미등록(null)과 0을 가르는 규칙이 셋(sum·avg·avgnz) 다 달라서 모드별로 비교한다.
  to17({ months, how }, core) {
    return { core: core.ov17(months, how) };
  },

  // 집계 그룹 정의 자체(어느 행이 어느 하위를 더하는가). 백엔드 cost_groups.GROUP_CALC와 짝이다.
  group_defs(_input, core) {
    return { core: Object.fromEntries(core.OV_EL.filter((e) => e.calc).map((e) => [e.k, e.calc])) };
  },

  // 증감분석 화면 표(17기간 × 계정). 백엔드 compute_variance_table과 짝이다.
  // 두 버전(당월 · 비교)의 캐시를 각각 꽂아야 한다 — 환차는 "두 기준의 환율 차이"에서 나오므로
  // 한 캐시만 주면 환차가 통째로 0이 되어 대조가 통과해 버린다.
  // ratesCur · ratesBase는 **완성된 환율 맵**이다({std, cp, ils, ...}) — 현지통화 연구소가 있으면
  // 소문자 통화 키(core.js ovLcOf)가 있어야 하고, 없으면 ovAnaEl이 그 자리에서 터진다.
  variance({ dCur, dBase, ratesCur, ratesBase, periods }, core) {
    const Cc = buildCache(core, dCur, ratesCur), Cb = buildCache(core, dBase, ratesBase);
    core.setVersionCaches(Cc, Cb);
    return {
      core: Object.fromEntries(
        periods.map((per) => [per, core.analysis(per)]),
      ),
    };
  },

  // 집계 ②③④ 탭(연구소별 표). 백엔드 lab_tables와 짝이다. ①(pl_rows)과 달리 행이 연구소별이고
  // CP는 분기 계약 금액으로 치환되므로(usdq · krwq), 행 순서·라벨까지 대조해야 같은 표가 된다.
  lab_tables({ d, rate, cpPocs }, core) {
    core.setTableInputs(buildCache(core, d, { std: rate, cp: rate }), rate, cpPocs || []);
    const T = core.tables();
    const pick = (t) => ({
      caption: t.cap,
      rows: (t.rows || []).map((r) => ({
        label: r.label, level: r.lv || 0, format: r.f,
        total: !!r.t, bold: !!r.b, muted: !!r.rt, v: mon17(r.v),
      })),
    });
    return { core: Object.fromEntries(["t2u", "t2k", "t3", "t4", "t5", "t6"].map((k) => [k, pick(T[k])])) };
  },

  // 연구소 코드 → 현지통화 표. 수식이 아니라 수식이 쓰는 기준정보지만, 여기가 어긋나면 환차 분해가
  // 통째로 달라지므로(현지통화 연구소를 USD로 보면 현지→$ 단계가 사라진다) 같이 대조한다.
  // labs를 주지 않고 호출해서 "프런트에 하드코딩된 초기값"을 그대로 비교한다.
  lab_currencies(_input, core) {
    return {
      core: Object.fromEntries(core.OV_LABS.map((L) => [L.c, String(L.cur || "USD").toUpperCase()])),
    };
  },
};

function main() {
  const job = JSON.parse(fs.readFileSync(0, "utf8"));
  const fn = CASES[job.case];
  if (!fn) throw new Error(`알 수 없는 케이스: ${job.case}`);
  const core = loadCore(job.labs);

  process.stdout.write(JSON.stringify(fn(job.input || {}, core)));
}

try {
  main();
} catch (e) {
  process.stderr.write(String((e && e.stack) || e));
  process.exit(1);
}
