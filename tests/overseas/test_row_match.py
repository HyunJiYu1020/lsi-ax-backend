"""업로드 행 이름 맞추기('Travel Expense'는 출장비와 같은 행인가?).

이 API의 계약은 두 가지다: ① 비슷한 이름은 후보로 올리되 **자동으로 합치지 않는다**(후보만 준다),
② 사람이 한 번 답하면 다음부터 묻지 않는다(별칭 재사용 — "별개 행"이라는 답도 포함).
그래서 테스트도 "맞췄나"가 아니라 "묻는가 / 다시 묻지 않는가"를 본다.
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints import collect as ov
from app.shared.core import row_match
from app.accounts.overseas.schemas import RowAliasIn, RowMatchIn

YYYYMM = "202609"


def _match(db, labels):
    r = ov.match_row_labels(RowMatchIn(labels=labels, yyyymm=YYYYMM, version="forecast"), db=db)
    return {i.label: i for i in r.items}


def test_suggests_fixed_account_for_renamed_row(db):
    """'Travel Expense'는 고정 계정 출장비(영문명 'Travel Exp')를 후보로 올린다 — 합치지는 않는다."""
    it = _match(db, ["Travel Expense"])["Travel Expense"]

    assert it.resolved is None  # 답해 둔 게 없으니 아직 아무것도 확정하지 않는다
    assert [(s.kind, s.key) for s in it.suggestions] == [("category", "travel")]
    s = it.suggestions[0]
    assert s.label == "출장비" and s.matched_on == "Travel Exp"  # 어느 이름에 걸렸는지 같이 준다
    assert s.score >= row_match.MIN_SCORE


def test_different_rows_are_not_suggested(db):
    """끝이 같거나(Travel Exp / Test Exp) 앞이 같은(Cloud Usage / Cloud Service) 다른 행은 안 묻는다 —
    아닐 가능성이 큰 후보를 매번 물으면 담당자가 질문을 읽지 않고 넘기게 된다."""
    got = _match(db, ["Test Expense", "Cloud Usage Fee", "Zzz Unknown Row"])

    assert [(s.kind, s.key) for s in got["Test Expense"].suggestions] == [("category", "test")]
    assert got["Cloud Usage Fee"].suggestions == []  # 전산비·IT 기타 어디에도 안 걸린다
    assert got["Zzz Unknown Row"].suggestions == []


def test_other_qualifier_is_not_stripped(db):
    """'Other IT Expenses'(IT 기타)와 'IT Expense'(전산비)는 서로 다른 고정 계정이다 — other를
    꼬리말로 떼면 둘이 같은 이름이 되어 엉뚱하게 합쳐진다."""
    assert row_match.score("Other IT Expenses", "IT Expense") == 0
    assert row_match.score("IT Expenses", "IT Expense") > 0


def test_saved_answer_is_reused_and_not_asked_again(db):
    """한 번 "같은 행"이라고 답하면 다음 업로드에서는 묻지 않고 그대로 합쳐 넣는다.
    공백·대소문자만 다른 표기도 같은 답으로 걸린다(정규화한 이름이 키라서)."""
    ov.upsert_row_alias(RowAliasIn(alias_label="Travel Expense", target_kind="category", target_key="travel"), db=db)

    it = _match(db, ["travel  EXPENSE"])["travel  EXPENSE"]
    assert it.resolved is not None and (it.resolved.kind, it.resolved.key) == ("category", "travel")
    assert it.resolved.label == "출장비"
    assert it.suggestions == []  # 확정된 답이 있으면 후보를 또 보여주지 않는다


def test_distinct_answer_stops_the_question(db):
    """"별개 행"이라고 답한 것도 저장한다 — 안 그러면 매달 "이거 같은 행 아니에요?"를 또 묻는다."""
    ov.upsert_row_alias(RowAliasIn(alias_label="Travel Expense", target_kind="distinct"), db=db)

    it = _match(db, ["Travel Expense"])["Travel Expense"]
    assert it.resolved is not None and it.resolved.kind == "distinct"
    assert it.resolved.label == ""  # 합쳐 넣을 행이 없다 = 새 행으로 둔다
    assert it.suggestions == []


def test_answer_can_be_changed_and_deleted(db):
    """잘못 합쳤으면 다시 답해서 덮어쓰거나, 지워서 다음 업로드에 다시 묻게 할 수 있다."""
    ov.upsert_row_alias(RowAliasIn(alias_label="Travel Expense", target_kind="category", target_key="travel"), db=db)
    ov.upsert_row_alias(RowAliasIn(alias_label="Travel Expense", target_kind="distinct"), db=db)
    assert _match(db, ["Travel Expense"])["Travel Expense"].resolved.kind == "distinct"

    assert ov.delete_row_alias(alias_label="Travel  Expense", db=db)["deleted"] == 1  # 정규화해서 찾는다
    again = _match(db, ["Travel Expense"])["Travel Expense"]
    assert again.resolved is None and again.suggestions  # 답이 없어졌으니 다시 묻는다

    with pytest.raises(HTTPException) as e:
        ov.delete_row_alias(alias_label="Travel Expense", db=db)
    assert e.value.status_code == 404


def test_bad_alias_requests_are_rejected(db):
    """저장할 수 없는 답은 막는다 — 없는 계정, 빈 이름, 자기 자신을 가리키는 연결."""
    for body, why in [
        (RowAliasIn(alias_label="X", target_kind="category", target_key="nope"), "없는 비용항목"),
        (RowAliasIn(alias_label="", target_kind="distinct"), "빈 이름"),
        (RowAliasIn(alias_label="X", target_kind="made_up"), "없는 target_kind"),
        (RowAliasIn(alias_label="Cloud Fee", target_kind="extra_row", target_label="Cloud Fee"), "자기 자신"),
        (RowAliasIn(alias_label="X", target_kind="extra_row"), "정식 이름 없음"),
    ]:
        with pytest.raises(HTTPException) as e:
            ov.upsert_row_alias(body, db=db)
        assert e.value.status_code == 400, why


def test_duplicate_and_blank_labels_collapse(db):
    """같은 이름을 여러 번 보내도 한 건으로 묶고, 빈 이름은 버린다(파일에 빈 행이 섞여 온다)."""
    r = ov.match_row_labels(
        RowMatchIn(labels=["Travel Expense", "travel expense", "", "   "], yyyymm=YYYYMM, version="forecast"), db=db
    )
    assert len(r.items) == 1

    assert ov.match_row_labels(RowMatchIn(labels=[], yyyymm=YYYYMM), db=db).items == []


def test_existing_extra_rows_become_candidates(db):
    """다른 연구소가 이미 쓰는 추가 행도 후보가 되고, 어느 연구소가 쓰는지 같이 준다
    (그걸 알면 같은 행인지 판단하기 쉽다)."""
    labs = [c["labs"] for c in ov._match_candidates(db, YYYYMM, "forecast") if c["kind"] == "extra_row"]
    assert all(isinstance(x, list) for x in labs)
    # 고정 계정 후보에는 집계로 계산되는 그룹 행(개발비 A, 인건비 등)이 들어가면 안 된다 —
    # 거기에는 업로드 값을 꽂을 수 없다.
    keys = {c["key"] for c in ov._match_candidates(db, YYYYMM, "forecast") if c["kind"] == "category"}
    assert "travel" in keys and not ({"A", "tot", "labor", "hr"} & keys)
