"""TDC(대만) 현지통화를 TWD → USD로 바로잡음

Revision ID: d2b7f04e9a16
Revises: c9b3e7a1f052
Create Date: 2026-11-15 20:10:00.000000

왜: TDC는 $로 결산하는 연구소다. 목업(budget-platform-ax 261103.html → core.js)은 국가별 기본
통화를 덮어써서 TDC만 USD로 두는데(`ovLabsSeed`의 `r.c==="TDC"?"USD":...`와 `ovEnsure`의 tdcUsd
플래그, 둘 다 2026-11-03에 들어갔다), 백엔드는 같은 날 들어간 마이그레이션 b3f4c91a7d2e가 국가→통화
일괄 매핑("대만"→TWD)으로 백필해서 이 예외를 놓쳤다. 그래서 화면은 TDC를 $ 연구소로, 서버는 TWD
연구소로 계산하고 있었다.

영향: TWD로 보면 TDC의 환차가 "현지→$"와 "$→원" 두 단계로 쪼개지고, USD로 보면 "$→원" 한 단계만
생긴다. 금액 총합은 같지만 환차/환차外 배분과 인건비의 인원·단가 분해가 달라진다.

과거 버전을 소급해서 바꾸는 게 맞는가: 맞다. 기준정보 이력(overseas_lab_revisions)은 "통화를
바꾸기로 한 결정"을 그 버전부터 적용하기 위한 것이고, 이건 결정이 바뀐 게 아니라 처음부터 USD였어야
하는 값이 잘못 들어가 있던 것이다. 그래서 이력 행을 추가하지 않고 현재 값을 고친다 — 과거 보고서도
원래 나와야 했던 숫자로 다시 계산된다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2b7f04e9a16"
down_revision: Union[str, Sequence[str], None] = "c9b3e7a1f052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # lab_code로 거른다 — 지금은 TDC에 파트가 없어 pk도 "TDC" 하나지만, 나중에 파트가 생기면
    # 모든 파트가 같은 통화를 써야 한다(통화는 연구소 단위 기준정보다).
    op.execute(
        sa.text("UPDATE overseas_labs SET currency = 'USD' WHERE lab_code = 'TDC' AND currency = 'TWD'")
    )
    # 기준정보 이력에 TWD로 박힌 행이 있으면 같이 바로잡는다(지금 DB에는 없지만, 이 마이그레이션을
    # 나중에 돌리는 환경에는 있을 수 있다 — 남겨 두면 그 시점 조회가 다시 TWD로 계산된다).
    op.execute(
        sa.text(
            "UPDATE overseas_lab_revisions SET currency = 'USD' WHERE lab_code = 'TDC' AND currency = 'TWD'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("UPDATE overseas_labs SET currency = 'TWD' WHERE lab_code = 'TDC' AND currency = 'USD'")
    )
    op.execute(
        sa.text(
            "UPDATE overseas_lab_revisions SET currency = 'TWD' WHERE lab_code = 'TDC' AND currency = 'USD'"
        )
    )
