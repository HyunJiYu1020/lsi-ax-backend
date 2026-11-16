// 프런트(core.js · calc.js)의 계산 함수를 Node에서 그대로 실행해 결과를 JSON으로 돌려준다.
// test_formula_crosscheck.py가 stdin으로 {case, input, labs}를 넣고 stdout에서 {core, calc}를 받는다.
//
// 왜 이런 harness가 필요한가: 같은 수식이 core.js(목업) · calc.js(React) · 백엔드 세 곳에 복제돼 있고
// (PLAN.md P1-4), 한 곳만 고치면 "화면 숫자 ≠ 챗봇 숫자"가 된다. 수식을 합치는 건 Phase 6이고,
// 그때까지는 어긋남을 즉시 감지하는 것만으로 사고를 막는다 — 그래서 수식을 파이썬으로 베껴 적지 않고
// 두 구현을 실제로 실행해서 비교한다(베껴 적으면 그게 네 번째 복제본이 된다).
//
// core.js는 모듈이 아니라 브라우저 전역 스크립트(8천 줄)라 import할 수 없다. 그래서:
//   ① DOM·fetch·localStorage를 전부 받아넘기는 stub 전역에 vm으로 통째로 실행하고
//   ② 스크립트 맨 끝에 "필요한 이름만 담은 객체 리터럴"을 붙여 완료값으로 꺼낸다
//      (최상위 const/let은 globalThis의 속성이 아니라 스크립트 스코프라 이 방법밖에 없다).
// calc.js는 순수 ESM이라 그냥 import한다.

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { register } from "node:module";
import { fileURLToPath } from "node:url";

// calc.js는 확장자 없이 import한다(`from "../utils/format"`) — Vite가 해결해 주는 방식이라
// Node의 ESM 로더는 그대로는 못 찾는다. 번들러를 끌어들이는 대신 "못 찾으면 .js를 붙여 다시"
// 해보는 resolve 훅만 끼운다(프런트 코드는 전혀 건드리지 않는다).
register(
  "data:text/javascript," +
    encodeURIComponent(`
      export async function resolve(spec, ctx, next) {
        try { return await next(spec, ctx); }
        catch (e) {
          if (spec.startsWith(".") && !/\\.(js|mjs|cjs|json)$/.test(spec)) return next(spec + ".js", ctx);
          throw e;
        }
      }
    `),
  import.meta.url,
);

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../../..");  // tests/overseas/ → tests → backend → S.LSI
const CORE_JS = path.join(REPO, "frontend-html/shared/core.js");
const CALC_JS = path.join(REPO, "frontend/src/logic/calc.js");
const CONST_JS = path.join(REPO, "frontend/src/data/overseasConstants.js");

// core.js에서 꺼내 쓸 이름. core.js가 이 중 하나를 지우거나 이름을 바꾸면 추출이 ReferenceError로
// 즉시 실패한다 — 의도된 동작이다(목업의 수식 함수가 사라졌다는 건 대조 대상이 바뀌었다는 뜻이라
// 조용히 넘어가면 안 된다).
const CORE_EXPORTS = [
  "num", "ovL", "ovLcOf", "ovPks", "OV_LABS", "OV_EL", "OV_ELK", "OVQ",
  "ovPartEl", "ovAgg", "ovLaborSplit", "ov17",
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
// setCache는 core.js의 ovAgg에 캐시를 꽂기 위한 것이다 — core.js의 ovAgg는 calc.js와 달리 캐시를
// 인자로 받지 않고 전역 상태에서 ovCache(ver)로 꺼내 쓴다(그게 P1-4가 말하는 "전역 의존" 그 자체다).
// OV_LABS는 let, ovCache는 function 선언이라 둘 다 epilogue에서 재대입할 수 있고, ovL·ovAgg가 그
// 바인딩을 클로저로 잡고 있어서 재대입이 그대로 먹는다.
function loadCore(labs) {
  const src = fs.readFileSync(CORE_JS, "utf8");
  const inject = labs
    ? `\n;OV_LABS = ${JSON.stringify(labs)};OV_ALLPK = [].concat(...OV_LABS.map(ovPks));`
    : "";
  const epilogue = `${inject}
;({ ${CORE_EXPORTS.join(", ")}, setCache: (c) => { ovCache = () => c; } })`;
  return vm.runInContext(src + epilogue, vm.createContext(browserStub()), { filename: CORE_JS });
}

const mon12 = (a) => Array.from({ length: 12 }, (_x, j) => (a && a[j] !== undefined ? a[j] : null));

// 두 구현의 ovPartEl 결과를 묶어 "캐시"(el + rate) 모양으로 만든다. core.js의 ovPartEl은
// (pd, extra) 2인자지만 extra가 없으면 무시하므로 calc.js와 같이 1인자로 부를 수 있다.
const buildCache = (impl, d, rate) => ({
  el: Object.fromEntries(Object.entries(d).map(([pk, pd]) => [pk, impl.ovPartEl(pd)])),
  rate,
});

// ---------- 대조 케이스 ----------
// 각 케이스는 고정 입력을 받아 {core, calc} 한 쌍을 돌려준다. 파이썬 쪽에서 백엔드 결과까지 셋을 비교한다.
const CASES = {
  // ovPartEl: 연구소가 올린 리프 값 → 손익계산서 계층 소계(labor = sal+bonus+welf 등)를 채운 맵.
  // 백엔드는 같은 일을 cost_groups.GROUP_CALC / resolve_leaves로 한다.
  part_el({ pd, keys }, core, calc) {
    const pick = (el) => Object.fromEntries(keys.map((k) => [k, mon12(el[k])]));
    return { core: pick(core.ovPartEl(pd)), calc: pick(calc.ovPartEl(pd)) };
  },

  // ovAgg(krw): 연구소 묶음의 월별 원화 금액 = Σ리프 × (원/$) × 1000. 백엔드는 _krw_months가 같은 일을 한다.
  agg_krw({ d, pks, keys, rate }, core, calc) {
    const rates = { std: rate, cp: rate };
    core.setCache(buildCache(core, d, rates));
    const calcCache = buildCache(calc, d, rates);
    return {
      core: Object.fromEntries(keys.map((k) => [k, mon12(core.ovAgg(null, pks, k, "krw"))])),
      calc: Object.fromEntries(keys.map((k) => [k, mon12(calc.ovAgg(calcCache, pks, k, "krw"))])),
    };
  },

  // ovLaborSplit: 인건비 증감을 인원 효과 · 단가 효과로 나눈다. 두 구현 모두 캐시(Cc/Cb)를 인자로
  // 받는 순수 함수라 고정 입력으로 바로 비교된다.
  labor_split({ pks, ix, cur, base }, core, calc) {
    const run = (impl) =>
      impl.ovLaborSplit(pks, ix, buildCache(impl, cur.d, cur.rate), buildCache(impl, base.d, base.rate));
    return { core: run(core), calc: run(calc) };
  },

  // 집계 그룹 정의 자체(어느 행이 어느 하위를 더하는가). 백엔드 cost_groups.GROUP_CALC와 짝이다.
  group_defs(_input, core, calc) {
    const defs = (impl) => Object.fromEntries(impl.OV_EL.filter((e) => e.calc).map((e) => [e.k, e.calc]));
    return { core: defs(core), calc: defs(calc) };
  },

  // 연구소 코드 → 현지통화 표. 수식이 아니라 수식이 쓰는 기준정보지만, 여기가 어긋나면 환차 분해가
  // 통째로 달라지므로(현지통화 연구소를 USD로 보면 현지→$ 단계가 사라진다) 같이 대조한다.
  // labs를 주지 않고 호출해서 "두 프런트에 하드코딩된 초기값"을 그대로 비교한다.
  lab_currencies(_input, core, calc) {
    const table = (impl) =>
      Object.fromEntries(impl.OV_LABS.map((L) => [L.c, String(L.cur || "USD").toUpperCase()]));
    return { core: table(core), calc: table(calc) };
  },
};

async function main() {
  const job = JSON.parse(fs.readFileSync(0, "utf8"));
  const fn = CASES[job.case];
  if (!fn) throw new Error(`알 수 없는 케이스: ${job.case}`);
  const core = loadCore(job.labs);
  const calc = { ...(await import(CALC_JS)), ...(await import(CONST_JS)) };
  process.stdout.write(JSON.stringify(fn(job.input || {}, core, calc)));
}

main().catch((e) => {
  process.stderr.write(String((e && e.stack) || e));
  process.exit(1);
});
