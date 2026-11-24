"""해외연구소 저장 단위를 K$에서 $로 바꾼다 (기존 금액 ×1000)

Revision ID: a1c7d4f08b32
Revises: 5b91ba457db6
Create Date: 2026-11-18

연구소가 보내는 양식은 그대로 K$지만, 업로드가 그 셀을 ×1000해서 **$로 저장**하게 바뀌었다
(core.js ovParse). 계산도 `값 × 환율`로 ×1000이 빠졌고, 화면만 ÷1000해서 K$로 보여준다.
그래서 이미 들어있는 값(= K$로 저장된 값)을 ×1000해 줘야 전환 전후 금액이 같다.

무엇을 곱하고 무엇을 안 곱하는가:
- 곱한다: 해외연구소 비용(cost_entries) · 경영계획(management_plan_entries) · 외주용역 월 금액 ·
  증감사유 시트 금액 · 실적 1차의 $ 환산액. 전부 "달러로 적힌 값"이다.
- 안 곱한다:
  · 인력(category_code='hc') — 단위가 명(名)이다.
  · 원화 기타(scope_code='_ETC') — 애초에 달러가 아니라 원화(억원)로 입력받는 줄이다.
  · 실적 1차의 local_amount — 현지통화 1단위(ILS 1, INR 1)로 저장하고 환산은 읽을 때 한다.
  · CP 계약(overseas_cp_rows) — 계약서 그대로 $로 저장해 왔다. 이번 전환으로 비용 데이터가
    같은 단위가 된 것이고, CP 값 자체는 바뀌지 않는다.

이상 감지 기준(anomaly_rules.amount)도 금액이라 같이 올린다 — 기준만 K$로 남으면 "급변 10 이상"이
$10 이상이 되어 전 칸이 걸린다. 단위 의심(unit_suspect) 기준은 이번에 함께 조였는데(코드 기본값
20,000,000 = 20,000K$), 담당자가 직접 고쳐 둔 값은 그 뜻을 보존해야 하므로 여기서는 단위 환산
(×1000)만 한다.

되돌리기(downgrade)는 ÷1000이다. 두 방향 모두 **같은 WHERE**를 써야 한다 — 범위가 어긋나면
한 번 되돌렸다 다시 올릴 때 일부 행만 1000배가 된다.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1c7d4f08b32'
down_revision: Union[str, Sequence[str], None] = '5b91ba457db6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (설명, SQL) — upgrade는 {op}에 '*', downgrade는 '/'를 넣어 같은 범위에 반대 연산을 한다.
SCALED = (
    (
        "해외연구소 비용(월·분기·연간 칸 전부, 인력·원화기타 제외)",
        """
        UPDATE cost_entry_details d
           SET amount = d.amount {op} 1000
          FROM cost_entries e
         WHERE e.id = d.cost_entry_id
           AND e.account_code = 'overseas'
           AND e.category_code <> 'hc'
           AND e.scope_code <> '_ETC'
        """,
    ),
    (
        "해외연구소 경영계획",
        """
        UPDATE management_plan_entry_details d
           SET amount = d.amount {op} 1000
          FROM management_plan_entries e
         WHERE e.id = d.plan_entry_id
           AND e.account_code = 'overseas'
           AND e.category_code <> 'hc'
           AND e.scope_code <> '_ETC'
        """,
    ),
    (
        "외주용역 월 금액",
        "UPDATE overseas_vendor_row_details SET amount = amount {op} 1000",
    ),
    (
        "증감사유 시트 금액 5열(인력 행 제외)",
        """
        UPDATE overseas_reason_rows
           SET plan_value = plan_value {op} 1000,
               prev_value = prev_value {op} 1000,
               cur_value  = cur_value  {op} 1000,
               diff_plan  = diff_plan  {op} 1000,
               diff_prev  = diff_prev  {op} 1000
         WHERE category_key <> 'hc'
        """,
    ),
    (
        "실적 1차의 $ 환산액(현지통화 총액은 그대로)",
        "UPDATE overseas_actual_first_rows SET usd_amount = usd_amount {op} 1000 WHERE usd_amount IS NOT NULL",
    ),
    (
        "해외연구소 이상 감지 기준의 금액",
        "UPDATE anomaly_rules SET amount = amount {op} 1000 WHERE account_key = 'overseas' AND amount IS NOT NULL",
    ),
)


def _run(op_sign: str) -> None:
    for _label, sql in SCALED:
        op.execute(sql.format(op=op_sign))


def upgrade() -> None:
    """저장 단위 K$ → $ (×1000)."""
    _run("*")


def downgrade() -> None:
    """저장 단위 $ → K$ (÷1000). 코드도 같이 되돌려야 금액이 맞는다."""
    _run("/")
