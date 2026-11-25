"""이미 적재된 행 합치기 (TC-008).

별칭(row_aliases)과 짝이지만 하는 일이 다르다 — 별칭은 "앞으로 올라올" 파일의 이름을 맞추고,
합치기는 "이미 들어간" 금액을 옮긴다. 그래서 테스트도 그 쪽을 지킨다:
① 총액이 보존되는가(옮긴 금액이 사라지거나 두 번 세지지 않는가)
② 분기·연간 행을 건드리지 않는가(그 칸은 연구소가 적어 보낸 값만 담는다 — 2026-11-24 규칙)
③ 되돌릴 수 없는 작업이라 dry_run이 정말 아무것도 안 바꾸는가
④ 지나간 칸·집계 그룹 행처럼 **건드리면 안 되는 곳**을 거절하는가
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.accounts.overseas.endpoints import collect as ov
from app.accounts.overseas.schemas import ExtraRowIn, RowMergeIn
from app.accounts.overseas.services.entries import current_basis_yyyymm, entry_months_bulk
from app.shared.models.common import CostEntry, CostEntryDetail

PK = "SARC:CPU"
VER = "forecast"


@pytest.fixture
def ym(db):
    y = current_basis_yyyymm(db)
    if not y:
        pytest.skip("속보 데이터가 없어 스킵")
    return y


def _add(db, ym, key, label, monthly, lab=PK):
    ov.upsert_extra_row(
        ExtraRowIn(lab_pk=lab, version=VER, row_key=key, label=label, yyyymm=ym,
                   anchor_key="misc", v=[monthly] * 12),
        db,
    )


def _entry(db, ym, category_code, lab=PK):
    return db.scalar(
        select(CostEntry).where(
            CostEntry.account_code == "overseas", CostEntry.scope_code == lab,
            CostEntry.version == VER, CostEntry.yyyymm == ym,
            CostEntry.category_code == category_code,
        )
    )


def _total(db, ym, category_code, lab=PK):
    e = _entry(db, ym, category_code, lab)
    if e is None:
        return 0.0
    return sum(x or 0 for x in (entry_months_bulk(db, [e]).get(e.id) or [])[:12])


def _body(ym, **kw):
    base = dict(yyyymm=ym, version=VER, source_labels=["Other"], target_kind="category",
                target_key="misc", lab_pk=PK, dry_run=True)
    base.update(kw)
    return RowMergeIn(**base)


def test_dry_run_changes_nothing(db, ym):
    """미리보기는 저장하지 않는다 — 합치기는 되돌릴 수 없어서 확인 단계가 반드시 필요하다."""
    _add(db, ym, "ux_other_t", "Other", 1.0)
    before = _total(db, ym, "misc")

    res = ov.merge_rows(_body(ym), db)

    assert res.dry_run is True
    assert res.removed_rows == 0
    assert res.merged[0].source_total == pytest.approx(12.0)
    assert res.merged[0].target_before == pytest.approx(before)
    assert res.merged[0].target_after == pytest.approx(before + 12.0)
    assert _total(db, ym, "misc") == pytest.approx(before), "미리보기가 값을 바꿨다"
    assert _entry(db, ym, "ux_other_t") is not None, "미리보기가 원본 행을 지웠다"


def test_merge_preserves_total_and_removes_source(db, ym):
    """합친 금액이 대상 행에 더해지고 원본 행은 사라진다 — 총액은 그대로다."""
    _add(db, ym, "ux_other_t", "Other", 1.0)
    _add(db, ym, "ux_miscf_t", "Misc Fee", 2.0)
    before = _total(db, ym, "misc")

    res = ov.merge_rows(_body(ym, source_labels=["other", "Misc Fee"], dry_run=False), db)

    assert res.removed_rows == 2
    assert _total(db, ym, "misc") == pytest.approx(before + 12.0 + 24.0)
    assert _entry(db, ym, "ux_other_t") is None
    assert _entry(db, ym, "ux_miscf_t") is None


def test_label_is_matched_after_normalizing(db, ym):
    """'other'로 말해도 'Other' 행을 찾는다 — 연구소마다 대소문자·공백이 제각각이다."""
    _add(db, ym, "ux_other_t", "Other Exp.", 1.0)
    res = ov.merge_rows(_body(ym, source_labels=["otherexp"]), db)
    assert [p.source_label for p in res.merged] == ["Other Exp."]


def test_quarter_and_year_are_left_alone(db, ym):
    """분기·연간 행은 합친 뒤에도 **건드리지 않는다** (2026-11-24에 규칙이 뒤집혔다).

    그 칸은 연구소가 파일에 적어 보낸 값만 담는 자리다 — 서버가 월에서 계산해 덮으면 보고값이
    사라진다(tests/overseas/test_quarter_year_resync.py 머리 주석). 합친 결과 월 합과 어긋나게
    되면 업로드 미리보기가 period_mismatch로 색칠해 알리고, 사람이 다시 올려서 고친다."""
    _add(db, ym, "ux_other_t", "Other", 1.0)
    target = _entry(db, ym, "misc")
    if target is None:
        # 이 테스트만 **이미 저장된** 기타경비 행의 분기·연간 상세를 읽는다 — 합친 뒤에도 그 칸이
        # 그대로인지가 확인할 거리다. 그래서 그 연구소가 지금 편성 기준월에 올려 둔 데이터가
        # 있어야 한다(_add로 만드는 추가 행은 category_code가 row_key라 기타경비 행이 아니다).
        # 기준월은 "속보 데이터가 들어 있는 가장 늦은 달"이라 다른 연구소가 다음 달을 올리는
        # 순간 넘어간다 — 없는 걸 AttributeError로 터뜨리지 않고 이유를 적고 건너뛴다.
        pytest.skip(f"{PK}의 {ym} 속보에 기타경비(misc) 행이 없어 스킵 — 분기·연간 상세를 읽을 자리가 없다")
    read = lambda: {
        (r.period_type, r.year, r.period): float(r.amount)
        for r in db.scalars(
            select(CostEntryDetail).where(
                CostEntryDetail.cost_entry_id == target.id,
                CostEntryDetail.period_type.in_(("QUARTER", "YEAR")),
            )
        ).all()
    }
    before_periods, before_months = read(), _total(db, ym, "misc")

    ov.merge_rows(_body(ym, dry_run=False), db)

    assert _total(db, ym, "misc") != pytest.approx(before_months), "합치기가 월 값을 안 바꿨다"
    assert read() == before_periods, "합치기가 분기·연간 행을 고쳤다"


def test_each_lab_is_merged_separately(db, ym):
    """연구소별로 따로 합친다 — 섞어 더하면 그 연구소 실적이 다른 연구소로 옮겨간다."""
    _add(db, ym, "ux_other_t", "Other", 1.0, lab=PK)
    _add(db, ym, "ux_other_t", "Other", 5.0, lab="DSRA")
    pk_before, dsra_before = _total(db, ym, "misc"), _total(db, ym, "misc", "DSRA")

    res = ov.merge_rows(_body(ym, lab_pk="", dry_run=False), db)

    assert {p.lab_pk for p in res.merged} == {PK, "DSRA"}
    assert _total(db, ym, "misc") == pytest.approx(pk_before + 12.0)
    assert _total(db, ym, "misc", "DSRA") == pytest.approx(dsra_before + 60.0)


def test_merge_into_extra_row_renames_target(db, ym):
    """추가 행으로 합치면 대상 행 이름을 정식 이름으로 맞춘다 — 집계표는 행 이름으로 묶기 때문에
    이름이 어긋나면 합쳤는데 두 줄로 보인다."""
    _add(db, ym, "ux_cloudfee_t", "Cloud Fee", 3.0)
    _add(db, ym, "ux_cloud_t", "Cloud Usage", 1.0)

    ov.merge_rows(
        _body(ym, source_labels=["Cloud Fee"], target_kind="extra_row", target_key="",
              target_label="Cloud Usage", dry_run=False),
        db,
    )
    target = _entry(db, ym, "cloudusage")
    assert target is not None and target.label == "Cloud Usage"
    assert _entry(db, ym, "ux_cloudfee_t") is None


def test_missing_source_is_not_an_error(db, ym):
    """합칠 행이 없으면 에러가 아니라 "없다"고 알려준다 — 'Misc'처럼 이미 고정 계정으로 들어간
    이름은 애초에 합칠 대상이 아니다(추가 행이 아니라서)."""
    res = ov.merge_rows(_body(ym, source_labels=["존재하지않는행"]), db)
    assert res.merged == [] and res.removed_rows == 0
    assert "합칠 행이 없습니다" in res.note


def test_rejects_past_month(db, ym):
    """지나간 칸은 거절한다 — 이미 보고된 숫자를 뒤에서 바꾸면 그 달 보고서를 다시 뽑을 때 달라진다."""
    past = f"{int(ym[:4]) - 1}12"
    with pytest.raises(HTTPException) as e:
        ov.merge_rows(_body(past), db)
    assert e.value.status_code == 400
    assert "다시 업로드" in e.value.detail


def test_rejects_group_row_as_target(db, ym):
    """집계 그룹 행에는 합칠 수 없다 — 하위 항목의 합으로 계산되는 줄이라 값을 꽂으면 어긋난다."""
    for key in ("gen", "A", "tot"):
        with pytest.raises(HTTPException) as e:
            ov.merge_rows(_body(ym, target_key=key), db)
        assert e.value.status_code == 400, key
        assert "계산되는 행" in e.value.detail


def test_rejects_bad_input(db, ym):
    """빈 이름·없는 계정·자기 자신으로 합치기는 저장 전에 막는다."""
    for kw in (
        dict(source_labels=[]),
        dict(source_labels=["   "]),
        dict(target_key="없는계정"),
        dict(source_labels=["misc"], target_key="misc"),
        dict(target_kind="distinct"),
        dict(target_kind="extra_row", target_key="", target_label=""),
    ):
        with pytest.raises(HTTPException) as e:
            ov.merge_rows(_body(ym, **kw), db)
        assert e.value.status_code == 400, kw


def test_derived_row_is_not_merged(db, ym):
    """파생 행은 합치지 않는다 — 월별 값을 저장하지 않고 계산식만 갖고 있어서 옮길 금액이 없다."""
    ov.upsert_extra_row(
        ExtraRowIn(lab_pk="ALL", version=VER, row_key="ux_derived_t", label="Other",
                   yyyymm=ym, anchor_key="misc", is_derived=True,
                   formula={"op": "sum_elementwise", "operands": ["travel", "edu"]}),
        db,
    )
    res = ov.merge_rows(_body(ym, lab_pk="", source_labels=["Other"]), db)
    assert res.merged == [], "파생 행이 합치기 대상으로 잡혔다"


# ---------- 값이 바뀌면 확정을 되돌린다 (D7 · TC-041) ----------
def test_merge_unconfirms_the_account(db, ym):
    """확정한 뒤 합치면 확정을 작성 중으로 되돌린다.

    안 되돌리면 "9월 속보 확정 완료"라고 적힌 채로 숫자가 달라져서, 보고된 값과 화면 값이 다른데
    아무도 모른다. 화면이 이미 하는 일(core.js ovUnconfirm)을 서버에서 값을 바꿀 때도 해야 한다.
    확정 일시와 note까지 같이 지운다(공통 RULE: 완료 후 수정 시 작성중 + 일자 삭제)."""
    from app.shared.endpoints.version import update_version_account
    from app.shared.models.version import Version
    from app.shared.schemas.version import VersionAccountStatusUpdate

    year, month = int(ym[:4]), int(ym[4:])
    # 버전 행이 없으면 만든다(바깥 트랜잭션이 롤백되니 DB에는 안 남는다). 스킵하지 않는 이유:
    # 이 테스트가 지키려는 건 "값이 바뀌면 확정이 거짓이 된다"이고, 그건 버전 행 유무와 무관하게
    # 반드시 성립해야 한다 — 로컬 DB에 버전 행이 없다고 조용히 안 돌면 회귀를 못 잡는다.
    if not db.scalar(
        select(Version).where(Version.year == year, Version.month == month, Version.kind == VER)
    ):
        db.add(Version(year=year, month=month, kind=VER, status="drafting"))
        db.flush()

    update_version_account(
        year, month, VER, "overseas",
        VersionAccountStatusUpdate(status="done", note="미업로드 2개 포함 확정"), db,
    )
    _add(db, ym, "ux_other_t", "Other", 1.0)

    res = ov.merge_rows(_body(ym, dry_run=False), db)

    assert res.unconfirmed is True
    assert "작성 중으로 되돌렸습니다" in res.note
    after = next(
        a for a in __import__(
            "app.shared.endpoints.version", fromlist=["x"]
        ).list_version_accounts(year, month, VER, db)
        if a.account_code == "overseas"
    )
    assert after.status == "drafting"
    assert after.confirmed_at is None
    assert after.note == "", "되돌렸는데 확정 당시 단서가 남으면 그게 지금 상태인 줄 읽힌다"


def test_dry_run_does_not_unconfirm(db, ym):
    """미리보기는 확정도 건드리지 않는다 — 아직 아무것도 바뀌지 않았다."""
    _add(db, ym, "ux_other_t", "Other", 1.0)
    assert ov.merge_rows(_body(ym), db).unconfirmed is False
