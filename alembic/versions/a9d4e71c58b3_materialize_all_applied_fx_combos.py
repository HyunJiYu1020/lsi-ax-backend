"""fx_applied_rates 전 조합 백필 — 적용 환율 순수 읽기 전환의 데이터 준비

집계·증감·결산의 환산(rate_arrays → applied_rows)이 이제 fx_applied_rates만 읽는다(없으면 404).
표는 환율 저장(PUT /fx/state)이 전 조합을 만들어 두는데, 이미 돌고 있는 DB(덤프 복원 포함)는
그 저장을 다시 거치기 전까지 지금 기준월 조합밖에 없다 — 그대로 두면 비교 버전(전실행·전년 실적)
화면이 전부 404가 된다. 그래서 여기서 한 번 전 조합을 만들어 둔다.

env.py가 이미 app 모델을 import하므로(autogenerate용) 서비스 import도 같은 결이다. 이 마이그레이션은
그 시점의 계산 규칙으로 표를 채우는 일회성 백필이고, 이후 갱신은 PUT /fx/state가 맡는다.

Revision ID: a9d4e71c58b3
Revises: f3c7a1d9b204
"""

from alembic import op
from sqlalchemy.orm import Session

revision = "a9d4e71c58b3"
down_revision = "f3c7a1d9b204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.shared.services.fx_rates import materialize_applied_rates

    db = Session(bind=op.get_bind())
    materialize_applied_rates(db)
    db.flush()


def downgrade() -> None:
    # 백필을 되돌릴 필요는 없다 — 표 내용은 원본에서 언제든 다시 만들 수 있는 파생값이고,
    # 이전 코드(read-through 이전)도 이 표에 행이 많다고 깨지지 않는다.
    pass
