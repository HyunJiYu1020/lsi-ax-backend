"""기준정보 읽기·쓰기 규칙 — "어느 버전이 어느 표를 보는가"를 한곳에서 정한다.

엔드포인트(app/shared/endpoints/reference.py)는 이 모듈을 호출만 한다. 규칙을 여기 모아 둔 이유는
챗봇·집계도 같은 질문("9월 속보가 보는 계정명이 뭐야?")을 하게 되고, 그때 엔드포인트를 통해
HTTP로 돌아가거나 규칙을 한 번 더 베껴 적으면 두 답이 갈라지기 때문이다.

버전 키 문자열은 두 가지다 — 속보·실적은 `"YYYY-MM-forecast" | "YYYY-MM-actual"`, 경영계획은
`"YYYY-plan-NN"`. 프런트의 화면용 id("2026-09-S" · "2026-P01")를 그대로 받지 않는다: 그 표기는
화면 사정으로 바뀔 수 있고, 서버에서 연·월로 범위를 잡을 수 없다.
"""

import re

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.shared.models.reference import (
    CHANGE_DETAIL_LIMIT,
    RefAssetClass,
    RefChangeLog,
    RefChangeRow,
    RefCostElement,
    RefCostElementSnapshot,
    RefDeptCode,
    RefPlmTask,
    RefProduct,
)

_RE_MONTHLY = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])-(forecast|actual)$")
_RE_PLAN = re.compile(r"^(\d{4})-plan-(\d{1,2})$")

# 버전별로 따로 두는 표(부서코드·PLM 과제)의 모델과 키 칼럼.
# 표 이름은 **한 벌만 쓴다** — 이력(ref_change_logs.table_key)과 DB 제약, 이 dict가 같은 글자를
# 쓴다 — 내부에서만 "prod"를 쓰다가 이력에 넘길 때 "product"로 바꾸면 KeyError가 난다.
VERSIONED = {
    "dept": (RefDeptCode, "code"),
    "plm": (RefPlmTask, "task_code"),
    "product": (RefProduct, "device"),
}


class VersionRef:
    """버전 하나를 가리키는 값. (연도, 종류, 월 또는 P번호)"""

    __slots__ = ("year", "kind", "seq")

    def __init__(self, year: int, kind: str, seq: int) -> None:
        self.year, self.kind, self.seq = year, kind, seq

    @property
    def key(self) -> str:
        """다시 문자열로. 응답에 그대로 실어 "어느 버전을 읽었는지"를 화면이 확인할 수 있게 한다."""
        return f"{self.year}-plan-{self.seq:02d}" if self.kind == "plan" else f"{self.year}-{self.seq:02d}-{self.kind}"

    @property
    def sort_key(self) -> int:
        """버전 순서. 프런트 verKey와 같은 식이다 — 같은 달이면 속보(짝수) < 실적(홀수)이고,
        경영계획은 그 해 안에서 P 번호 순으로만 비교된다(월 버전과 섞어 비교하지 않는다)."""
        if self.kind == "plan":
            return self.year * 1000 + 500 + self.seq
        return self.year * 1000 + self.seq * 2 + (1 if self.kind == "actual" else 0)

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"VersionRef({self.key})"


def parse_version(version: str) -> VersionRef:
    """버전 키 문자열 → VersionRef. 형식이 틀리면 400으로 막는다 — 조용히 다른 버전을 읽어
    내려주면 담당자는 자기 버전 기준정보를 보고 있다고 믿는다."""
    v = (version or "").strip()
    if m := _RE_MONTHLY.match(v):
        return VersionRef(int(m.group(1)), m.group(3), int(m.group(2)))
    if m := _RE_PLAN.match(v):
        return VersionRef(int(m.group(1)), "plan", int(m.group(2)))
    raise HTTPException(
        400, f'version 형식이 아닙니다: {version!r} — "YYYY-MM-forecast" · "YYYY-MM-actual" · "YYYY-plan-NN"'
    )


# ---------- 비용계정(연 단위 마스터 + 버전 스냅샷) ----------


def cost_elements(db: Session, year: int) -> list[RefCostElement]:
    """그 연도 계정표. 화면에서 고치는 대상이고, 아직 만들지 않은 연도는 빈 목록이다
    (빈 목록을 받으면 화면이 [전년 복사]를 권한다)."""
    return list(
        db.scalars(
            select(RefCostElement).where(RefCostElement.year == year).order_by(RefCostElement.sort_order)
        )
    )


def replace_cost_elements(
    db: Session, year: int, rows: list[dict], *, how: str = "screen", actor: str = "", note: str = ""
) -> list[RefCostElement]:
    """그 연도 계정표를 전체 교체한다. 이미 만든 버전의 스냅샷은 건드리지 않는다 —
    "저장하면 이후에 만드는 버전부터 반영"이 화면 안내문이고 그게 공통 RULE이다.

    바뀐 내용은 이력으로 남긴다(ref_change_logs) — 계정명이 언제부터 이랬는지, 엑셀 한 번에 표가
    통째로 갈렸는지를 나중에 물어보기 때문이다."""
    before = snapshot("cost_element", cost_elements(db, year))
    db.execute(delete(RefCostElement).where(RefCostElement.year == year))
    db.flush()
    saved = [
        RefCostElement(
            year=year,
            sort_order=i,
            gb=str(r.get("gb") or "").strip(),
            code=str(r.get("code") or "").strip(),
            a1=str(r.get("a1") or "").strip(),
            a2=str(r.get("a2") or "").strip(),
            a3=str(r.get("a3") or "").strip(),
            a4=str(r.get("a4") or "").strip(),
            name=str(r.get("name") or "").strip(),
        )
        for i, r in enumerate(rows)
    ]
    db.add_all(saved)
    db.flush()
    log_change(db, "cost_element", before, snapshot("cost_element", saved), scope=str(year), how=how, actor=actor, note=note)
    return saved


def _snapshot_source_year(db: Session, basis_year: int) -> int | None:
    """스냅샷을 뜰 연도 — 그 버전 연도의 표가 있으면 그것, 없으면 그 이하 가장 최근 연도.
    2027년 1월 속보를 여는데 2027년 표를 아직 안 만든 경우가 실제로 먼저 온다."""
    return db.scalar(
        select(RefCostElement.year)
        .where(RefCostElement.year <= basis_year)
        .order_by(RefCostElement.year.desc())
        .limit(1)
    )


def cost_elements_of_version(db: Session, v: VersionRef) -> list[RefCostElementSnapshot]:
    """그 버전이 보는 계정표. 처음 조회하는 버전이면 그 시점 연도표를 복사해 고정한다.

    복사를 '버전 생성 시점'이 아니라 '처음 조회 시점'에 하는 이유: 버전을 만드는 경로가 화면 ·
    챗봇 · 스크립트로 여러 개라 생성 훅을 다 붙이면 한 군데가 빠져 조용히 스냅샷 없는 버전이
    생긴다. 읽기 한 곳에서 보장하면 어느 경로로 만들어도 첫 조회에서 고정된다."""
    rows = list(
        db.scalars(
            select(RefCostElementSnapshot)
            .where(
                RefCostElementSnapshot.basis_year == v.year,
                RefCostElementSnapshot.version_kind == v.kind,
                RefCostElementSnapshot.version_seq == v.seq,
            )
            .order_by(RefCostElementSnapshot.sort_order)
        )
    )
    if rows:
        return rows

    src_year = _snapshot_source_year(db, v.year)
    if src_year is None:
        return []  # 연도표가 아직 하나도 없다 — 복사할 게 없으니 스냅샷도 만들지 않는다
    rows = [
        RefCostElementSnapshot(
            basis_year=v.year,
            version_kind=v.kind,
            version_seq=v.seq,
            source_year=src_year,
            sort_order=r.sort_order,
            gb=r.gb,
            code=r.code,
            a1=r.a1,
            a2=r.a2,
            a3=r.a3,
            a4=r.a4,
            name=r.name,
        )
        for r in cost_elements(db, src_year)
    ]
    db.add_all(rows)
    db.flush()
    return rows


# ---------- 자산 Class 매핑(시점 없는 단일 마스터) ----------


def asset_classes(db: Session) -> list[RefAssetClass]:
    return list(db.scalars(select(RefAssetClass).order_by(RefAssetClass.asset_class)))


def replace_asset_classes(
    db: Session, rows: list[dict], *, how: str = "screen", actor: str = "", note: str = ""
) -> list[RefAssetClass]:
    before = snapshot("asset_class", asset_classes(db))
    db.execute(delete(RefAssetClass))
    db.flush()
    saved: list[RefAssetClass] = []
    seen: set[str] = set()
    for r in rows:
        cls = str(r.get("cls") or "").strip()
        if not cls or cls in seen:
            continue  # 자산 Class가 키라 중복은 뒤 행을 버린다(화면도 첫 행이 이긴다)
        seen.add(cls)
        saved.append(
            RefAssetClass(
                asset_class=cls,
                class_text=str(r.get("text") or "").strip(),
                code=str(r.get("code") or "").strip(),
                name=str(r.get("name") or "").strip(),
            )
        )
    db.add_all(saved)
    db.flush()
    log_change(db, "asset_class", before, snapshot("asset_class", saved), how=how, actor=actor, note=note)
    return saved


# ---------- 부서코드 · PLM 과제(버전 단위, 새 버전은 직전 버전 복사) ----------


def _prev_rowset(db: Session, table: str, v: VersionRef) -> VersionRef | None:
    """직전 버전 — 행이 실제로 있는 버전 중 이 버전보다 순서가 앞선 가장 최근 것.

    프런트 verPrevOf(실적 ← 같은 달 속보, 속보 ← 전월 실적, 경영계획 Pn ← P(n-1))와 같은 순서지만
    "바로 앞 버전에 행이 없으면 그 앞"까지 거슬러 본다. 중간 버전을 안 열고 건너뛰는 일이 흔해서,
    바로 앞만 보면 새 버전이 빈 표로 시작한다(그러면 담당자가 18개 부서를 다시 입력한다).
    경영계획은 경영계획끼리만 비교한다 — 월 버전과 섞으면 P01이 전월 속보를 복사해 온다."""
    model, _key = VERSIONED[table]
    rows = db.execute(
        select(model.basis_year, model.version_kind, model.version_seq)
        .distinct()
        .where(model.version_kind == "plan" if v.kind == "plan" else model.version_kind != "plan")
    ).all()
    cands = [VersionRef(y, k, s) for y, k, s in rows]
    if v.kind == "plan":
        cands = [c for c in cands if c.year == v.year]
    earlier = [c for c in cands if c.sort_key < v.sort_key]
    return max(earlier, key=lambda c: c.sort_key) if earlier else None


# 화면 칼럼 키 → DB 칼럼. 프런트 COLS[t]의 k와 같은 이름을 받는다 — 업로드 엑셀 머리글을 화면이
# 이미 이 키로 정규화해 두므로, 서버가 또 다른 이름을 요구하면 변환이 한 겹 더 생긴다.
DEPT_FIELDS = {
    "code": "code",
    "job": "job_category",
    "name": "name",
    "bu": "budget_unit",
    "team": "team_s",
    "t2": "team_m",
    "t3": "team_l",
    "fa": "functional_area",
    "fam3": "fam3",
    "upd": "updated_on",
}
PLM_FIELDS = {
    "plm": "task_code",
    "prod": "product",
    "name": "name",
    "nick": "nickname",
    "st": "status",
    "ps": "plan_start",
    "pe": "plan_end",
    "stg": "stage",
    "ty": "target_type",
    "ty2": "target_subtype",
    "ss": "stage_start",
    "se": "stage_end",
    "pm": "project_manager",
    "pl": "project_leader",
    "own": "owner",
    "cr": "created_on",
}


def _dept_row(v: VersionRef, r: dict) -> RefDeptCode:
    vals = {db_col: str(r.get(k) or "").strip() for k, db_col in DEPT_FIELDS.items()}
    rep = str(r.get("rep") or "").strip().upper()
    state = str(r.get("state") or "").strip()
    return RefDeptCode(
        basis_year=v.year,
        version_kind=v.kind,
        version_seq=v.seq,
        # 대표코드는 화면에서 "Y"/"N"이지만 업로드 엑셀에는 "Yes"·"대표"·1도 온다 — 첫 글자만 본다
        # (프런트 refNorm과 같은 규칙).
        is_rep=rep[:1] == "Y" or rep in ("1", "TRUE", "O", "대표"),
        state="폐기" if state == "폐기" else "사용",
        **vals,
    )


# 제품정보는 화면 칼럼 이름이 곧 DB 칼럼 이름이다(SC_FMMS 양식 머리글을 그대로 쓴다).
PROD_FIELDS = {
    k: k
    for k in (
        "device", "yyyy", "sel_code", "section", "sale_device", "gr_device",
        "fam1_code", "fam2_code", "fam3_code", "fam4_code",
    )
}


def _prod_row(v: VersionRef, r: dict) -> RefProduct:
    vals = {db_col: str(r.get(k) or "").strip() for k, db_col in PROD_FIELDS.items()}
    return RefProduct(basis_year=v.year, version_kind=v.kind, version_seq=v.seq, **vals)


def _plm_row(v: VersionRef, r: dict) -> RefPlmTask:
    vals = {db_col: str(r.get(k) or "").strip() for k, db_col in PLM_FIELDS.items()}
    return RefPlmTask(basis_year=v.year, version_kind=v.kind, version_seq=v.seq, **vals)


def versioned_rows(db: Session, table: str, v: VersionRef) -> tuple[list, str]:
    """그 버전의 부서코드·PLM 과제. 행이 없으면 직전 버전을 복사해 넣고 시작한다.

    돌려주는 두 번째 값은 "어느 버전에서 복사했는지"다(복사 안 했으면 빈 문자열) — 화면이
    [전월 복사] 안내 배지를 그 값으로 띄운다. 복사한 사실을 안 보여주면 담당자는 직전 달 표를
    이번 달 확정값으로 착각한다."""
    model, key = VERSIONED[table]
    where = (model.basis_year == v.year, model.version_kind == v.kind, model.version_seq == v.seq)
    rows = list(db.scalars(select(model).where(*where).order_by(getattr(model, key))))
    if rows:
        return rows, ""

    prev = _prev_rowset(db, table, v)
    if prev is None:
        return [], ""
    src = db.scalars(
        select(model)
        .where(model.basis_year == prev.year, model.version_kind == prev.kind, model.version_seq == prev.seq)
        .order_by(getattr(model, key))
    )
    skip = {"id", "basis_year", "version_kind", "version_seq", "updated_at"}
    copied = [
        model(
            basis_year=v.year,
            version_kind=v.kind,
            version_seq=v.seq,
            **{c.name: getattr(r, c.name) for c in model.__table__.columns if c.name not in skip},
        )
        for r in src
    ]
    db.add_all(copied)
    db.flush()
    return copied, prev.key


def replace_versioned_rows(
    db: Session, table: str, v: VersionRef, rows: list[dict], *, how: str = "screen", actor: str = "", note: str = ""
) -> list:
    """그 버전의 표를 전체 교체한다. 키(부서코드 · 과제 코드)가 중복이면 400으로 막는다 —
    조용히 한 줄을 버리면 담당자는 저장됐다고 믿고 그 부서 금액이 사라진다."""
    model, key = VERSIONED[table]
    build = {"dept": _dept_row, "plm": _plm_row, "product": _prod_row}[table]
    made, seen = [], {}
    for i, r in enumerate(rows):
        row = build(v, r)
        k = getattr(row, key)
        if not k:
            label = {"dept": "부서코드", "plm": "과제 코드", "product": "제품 코드(device)"}[table]
            raise HTTPException(400, f"{i + 1}행: {label}가 비어 있습니다.")
        if k in seen:
            raise HTTPException(400, f"{i + 1}행: {k}가 {seen[k] + 1}행과 중복됩니다.")
        seen[k] = i
        made.append(row)

    existing, _copied = versioned_rows(db, table, v)
    before = snapshot(table, existing)
    db.execute(
        delete(model).where(
            model.basis_year == v.year, model.version_kind == v.kind, model.version_seq == v.seq
        )
    )
    db.flush()
    db.add_all(made)
    db.flush()
    out = sorted(made, key=lambda r: getattr(r, key))
    log_change(db, table, before, snapshot(table, out), scope=v.key, how=how, actor=actor, note=note)
    return out


# ---------- 변경 이력 ----------
# 표마다 "행을 가리키는 키"와 "비교할 칸"이 다르다. 키는 담당자가 표에서 행을 찾는 방법과 같아야
# 한다(부서코드 · 과제코드 · device) — 서버 id로 남기면 이력을 읽는 사람이 어느 행인지 모른다.
CHANGE_SPECS = {
    "cost_element": ("code", ("gb", "code", "a1", "a2", "a3", "a4", "name")),
    "asset_class": ("asset_class", ("asset_class", "class_text", "code", "name")),
    "dept": ("code", ("code", "job_category", "name", "budget_unit", "team_s", "team_m", "team_l",
                      "functional_area", "fam3", "is_rep", "state", "updated_on")),
    "plm": ("task_code", ("task_code", "product", "name", "nickname", "status", "plan_start", "plan_end",
                          "stage", "target_type", "target_subtype", "stage_start", "stage_end",
                          "project_manager", "project_leader", "owner", "created_on")),
    "product": ("device", ("device", "yyyy", "sel_code", "section", "sale_device", "gr_device",
                           "fam1_code", "fam2_code", "fam3_code", "fam4_code")),
    "close_dept_rule": ("_cc_acct", ("cost_center", "account_code", "account_key", "is_shared", "note")),
}
# 표 이름 → 화면에서 부르는 이름. 요약 문장에 그대로 들어간다.
CHANGE_LABELS = {
    "cost_element": "비용계정", "asset_class": "자산 Class 매핑", "dept": "부서코드",
    "plm": "PLM 과제", "product": "제품정보", "close_dept_rule": "전표 분류 기준",
}


def snapshot(table: str, rows: list) -> list[dict]:
    """모델 행 목록 → 비교용 dict 목록. 저장 **전에** 불러 두고 저장 후 결과와 맞댄다."""
    _key, fields = CHANGE_SPECS[table]
    out = []
    for r in rows:
        d = {f: getattr(r, f) for f in fields}
        if table == "close_dept_rule":
            d["_cc_acct"] = f"{d['cost_center']}|{d['account_code']}"
        out.append({k: ("" if v is None else v) for k, v in d.items()})
    return out


def _keyed(table: str, rows: list[dict]) -> dict[str, dict]:
    """키 → 행. 같은 키가 여러 번 나오면 "#2"를 붙인다 — 비용계정은 같은 Cost Elem.이 두 줄 있을
    수 있어서(감가상각비 53450300), 키를 겹치게 두면 둘 중 한 줄의 변경이 사라진다."""
    key, _fields = CHANGE_SPECS[table]
    out: dict[str, dict] = {}
    seen: dict[str, int] = {}
    for r in rows:
        base = str(r.get(key) or "")
        if table == "cost_element":
            base = f"{base}|{r.get('gb') or ''}"
        seen[base] = seen.get(base, 0) + 1
        out[base if seen[base] == 1 else f"{base}#{seen[base]}"] = r
    return out


def log_change(
    db: Session,
    table: str,
    before: list[dict],
    after: list[dict],
    *,
    scope: str = "",
    how: str = "screen",
    actor: str = "",
    note: str = "",
) -> RefChangeLog | None:
    """기준정보 저장 하나를 이력으로 남긴다. **바뀐 게 없으면 아무것도 쓰지 않고 None을 돌려준다.**

    화면은 [저장]을 누를 때마다 PUT을 보낸다 — 그걸 그대로 쌓으면 이력이 "아무 일도 없었음"으로
    가득 차서 진짜 변경을 못 찾는다. 그래서 변경 유무는 서버가 판단한다(화면이 판단하면 화면마다
    기준이 달라진다).
    """
    if table not in CHANGE_SPECS:
        return None
    _key, fields = CHANGE_SPECS[table]
    b, a = _keyed(table, before), _keyed(table, after)
    added = [k for k in a if k not in b]
    removed = [k for k in b if k not in a]
    changed: list[tuple[str, dict, dict]] = []
    for k in a:
        if k not in b:
            continue
        diff_before = {f: b[k][f] for f in fields if f != "_cc_acct" and b[k].get(f) != a[k].get(f)}
        if diff_before:
            changed.append((k, diff_before, {f: a[k][f] for f in diff_before}))
    if not (added or removed or changed):
        return None

    label = CHANGE_LABELS[table]
    bits = []
    if added:
        bits.append(f"추가 {len(added)}")
    if removed:
        bits.append(f"삭제 {len(removed)}")
    if changed:
        bits.append(f"변경 {len(changed)}")
    summary = f"{label} {len(before)}행 → {len(after)}행 · " + " · ".join(bits)

    detail = len(added) + len(removed) + len(changed)
    omit = detail > CHANGE_DETAIL_LIMIT
    if omit:
        summary += f" · 상세 생략({detail}행)"

    log = RefChangeLog(
        table_key=table, scope=scope, how=how, actor=(actor or "").strip()[:60], note=(note or "")[:200],
        rows_before=len(before), rows_after=len(after), added=len(added), removed=len(removed),
        changed=len(changed), detail_omitted=omit, summary=summary,
    )
    db.add(log)
    db.flush()
    if not omit:
        strip = lambda d: {k: v for k, v in d.items() if k != "_cc_acct"}
        db.add_all(
            [RefChangeRow(log_id=log.id, row_key=k, change="add", before={}, after=strip(a[k])) for k in added]
            + [RefChangeRow(log_id=log.id, row_key=k, change="del", before=strip(b[k]), after={}) for k in removed]
            + [RefChangeRow(log_id=log.id, row_key=k, change="chg", before=bf, after=af) for k, bf, af in changed]
        )
        db.flush()
    return log


def changes(db: Session, table: str | None = None, scope: str | None = None, limit: int = 50) -> list[RefChangeLog]:
    stmt = select(RefChangeLog).order_by(RefChangeLog.changed_at.desc(), RefChangeLog.id.desc()).limit(max(1, min(limit, 200)))
    if table:
        stmt = stmt.where(RefChangeLog.table_key == table)
    if scope is not None:
        stmt = stmt.where(RefChangeLog.scope == scope)
    return list(db.scalars(stmt))


def change_rows(db: Session, log_id: int) -> list[RefChangeRow]:
    return list(
        db.scalars(
            select(RefChangeRow).where(RefChangeRow.log_id == log_id).order_by(RefChangeRow.change, RefChangeRow.row_key)
        )
    )
