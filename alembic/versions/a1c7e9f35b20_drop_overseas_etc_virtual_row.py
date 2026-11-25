"""원화 기타(_ETC) 가상 행을 걷어낸다 — 입구가 없는 채로 특수 처리만 남아 있던 줄

왜: `_ETC`는 연구소가 아니라 "본사에서 원화(억원)로 얹는 기타 금액"을 담으려고 만든 가상 행이다.
연구소 값은 전부 $로 저장되는데 이 줄만 원화라, 읽는 쪽마다 "_ETC는 빼라"는 특수 처리가 붙어
있었다(is_summable · table_inputs · months_by_category · summary).

그런데 **값을 넣을 입구가 없다.** 화면에 입력칸을 그리는 조건(ovRowHtml의 r.edit)을 세우는 코드가
한 군데도 없어서, 핸들러·저장·서버 처리는 다 살아 있는데 사용자가 값을 넣을 방법이 없었다.
실제로 저장된 _ETC 행은 전부 0이었다(월·버전별 6행 × 17칸, 절대값 합계 0).

쓰지도 않으면서 사고를 한 번 냈다: save_state가 선택한 달에 _ETC 행을 항상 썼고
current_basis_month()는 max(yyyymm)이라, **값이 전부 0인 줄 하나가 편성 기준월을 한 달 밀었다**
(2026-11-20, 그 달 데이터가 없어 집계·증감분석이 404). 그래서 지웠다.

금액은 하나도 바뀌지 않는다 — 지우는 값이 전부 0이다.

Revision ID: a1c7e9f35b20
Revises: f2a9c1d4b7e6
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a1c7e9f35b20"
down_revision: Union[str, None] = "f2a9c1d4b7e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 상세(월·분기·연간 칸) → 헤더 → 연구소 기준정보 순으로 지운다(참조 순서).
    op.execute(
        """
        DELETE FROM cost_entry_details
         WHERE cost_entry_id IN (
               SELECT id FROM cost_entries
                WHERE account_code = 'overseas' AND scope_code = '_ETC')
        """
    )
    op.execute("DELETE FROM cost_entries WHERE account_code = 'overseas' AND scope_code = '_ETC'")
    # 경영계획 쪽에도 같은 가상 행이 들어갈 수 있었다(save_state의 plan_rows).
    op.execute(
        """
        DELETE FROM management_plan_entry_details
         WHERE plan_entry_id IN (
               SELECT id FROM management_plan_entries
                WHERE account_code = 'overseas' AND scope_code = '_ETC')
        """
    )
    op.execute(
        "DELETE FROM management_plan_entries WHERE account_code = 'overseas' AND scope_code = '_ETC'"
    )
    op.execute("DELETE FROM overseas_lab_revisions WHERE lab_code = '_ETC'")
    op.execute("DELETE FROM overseas_labs WHERE pk = '_ETC'")
    # 테이블 설명도 같이 고친다 — 모델(models.OverseasLab)과 DB 주석이 갈리면 DB만 보는 쪽
    # (DBeaver · 덤프)은 없어진 가상 행을 계속 있는 것으로 읽는다.
    op.execute("COMMENT ON TABLE overseas_labs IS '해외연구소·파트 기준정보(SARC는 CPU·GPU로 분리). 프론트 탭은 이 테이블을 lab_code로 묶어서 그린다.'")


def downgrade() -> None:
    # 기준정보 줄만 되돌린다. 금액은 전부 0이었으므로 복원할 값이 없다.
    op.execute(
        """
        INSERT INTO overseas_labs (pk, lab_code, part_code, name, country, sort_order, currency, is_active)
        VALUES ('_ETC', '_ETC', NULL, '(계정 공통) 원화 기타', '-', 999, 'USD', true)
        ON CONFLICT (pk) DO NOTHING
        """
    )
    op.execute("COMMENT ON TABLE overseas_labs IS '해외연구소·파트 기준정보(SARC는 CPU·GPU로 분리) + 연구소에 속하지 않는 계정 전체 단위 값을 담는 가상 행(_ETC). 프론트 탭은 이 테이블을 lab_code로 묶어서 그린다.'")
