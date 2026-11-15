"""Phase 3: 범용 개념 7개 테이블에서 overseas_ 접두사를 떼고 account_key를 붙인다

Revision ID: e1a4c7b93d28
Revises: d2b7f04e9a16
Create Date: 2026-11-16 00:40:00.000000

왜 (PLAN.md P2-5): 아래 7개는 이름만 overseas일 뿐 "파일을 받고 증감을 분석하는 계정"이면 똑같이
필요한 기능이다. 지금 구조로 계정 10개를 만들면 계정당 7~8개씩 테이블이 복제된다
(dep_row_aliases, outsource_anomaly_rules...). 이미 cost_entries·notes·uploads가 account_key
컬럼 패턴으로 전 계정을 받고 있으므로 같은 패턴에 맞춘다. 데이터가 적은 지금이 이관 비용 최저점이다.

| 기존                           | 변경 후             | account_key |
|--------------------------------|---------------------|-------------|
| overseas_row_aliases           | row_aliases         | 추가        |
| overseas_reason_translations   | text_translations   | 없음(전역)  |
| overseas_carry_defs            | carry_defs          | 추가        |
| overseas_anomaly_rules         | anomaly_rules       | 추가        |
| overseas_anomaly_rule_logs     | anomaly_rule_logs   | 추가        |
| overseas_variance_reasons      | variance_reasons    | 추가        |
| overseas_variance_drivers      | variance_drivers    | 추가        |

text_translations만 account_key가 없다: 원문 해시로 찾는 전역 번역 캐시라 계정을 구분할 이유가
없다(같은 영어 문장은 어느 계정에서 올라와도 같은 한국어다). 이름도 reason_translations →
text_translations로 바꿔 "증감사유 전용"이라는 오해를 없앤다.

백필을 하지 않는다: 개발용 데이터라 재시드 가능하다는 결정(PLAN.md 열린 질문 1, 2026-11-16).
그래서 account_key를 'DEFAULT overseas 지정 → 백필 → default 제거'의 3단 춤 없이 처음부터
기본값 없는 NOT NULL로 둔다 — 계정을 명시하지 않은 삽입을 DB가 바로 막아준다. 기존 행은
이 마이그레이션에서 지우고 app.db.seed로 다시 만든다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1a4c7b93d28"
down_revision: Union[str, Sequence[str], None] = "d2b7f04e9a16"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ACCOUNT_FK = "accounts.key"

# (기존 이름, 새 이름, account_key를 넣을지, 기존 uq 이름, 새 uq 이름, 새 uq 컬럼)
TABLES = [
    ("overseas_row_aliases", "row_aliases", True,
     "uq_overseas_row_aliases_norm", "uq_row_aliases_norm", ["account_key", "alias_norm"]),
    ("overseas_reason_translations", "text_translations", False,
     "uq_overseas_reason_translations_hash", "uq_text_translations_hash", ["source_hash"]),
    ("overseas_carry_defs", "carry_defs", True,
     "uq_overseas_carry_defs_key", "uq_carry_defs_key", ["account_key", "kind", "def_key"]),
    ("overseas_anomaly_rules", "anomaly_rules", True,
     "uq_overseas_anomaly_rules_key", "uq_anomaly_rules_key", ["account_key", "rule_key"]),
    ("overseas_anomaly_rule_logs", "anomaly_rule_logs", True, None, None, None),
    ("overseas_variance_reasons", "variance_reasons", True,
     "uq_overseas_variance_reasons_key", "uq_variance_reasons_key",
     ["account_key", "basis_year", "basis_month", "kind", "stage", "base_version", "period",
      "lab_scope", "category_key"]),
    ("overseas_variance_drivers", "variance_drivers", True,
     "uq_overseas_variance_drivers_key", "uq_variance_drivers_key",
     ["account_key", "basis_year", "basis_month", "kind", "lab_scope", "driver_key"]),
]


def upgrade() -> None:
    for old, new, add_key, old_uq, new_uq, uq_cols in TABLES:
        # 기존 행은 버린다(재시드 전제) — account_key를 NOT NULL로 바로 세우기 위함이다.
        op.execute(sa.text(f"DELETE FROM {old}"))
        op.rename_table(old, new)
        if old_uq:
            op.drop_constraint(old_uq, new, type_="unique")
        if add_key:
            op.add_column(
                new,
                sa.Column(
                    "account_key", sa.String(length=30), nullable=False,
                    comment="비용계정 키(accounts.key). 이 테이블은 전 계정 공용이라 계정을 명시해야 한다",
                ),
            )
            op.create_foreign_key(f"fk_{new}_account_key", new, "accounts", ["account_key"], ["key"])
            # 계정별 조회가 기본 패턴이라 인덱스를 같이 둔다(uq가 없는 로그 테이블에 특히 필요).
            op.create_index(f"ix_{new}_account_key", new, ["account_key"])
        if new_uq:
            op.create_unique_constraint(new_uq, new, uq_cols)

    # 시퀀스 이름도 테이블을 따라가게 한다 — 이름이 어긋나면 나중에 스키마를 읽는 사람이 헷갈린다.
    for old, new, *_ in TABLES:
        op.execute(sa.text(f"ALTER SEQUENCE IF EXISTS {old}_id_seq RENAME TO {new}_id_seq"))
        op.execute(sa.text(f"ALTER INDEX IF EXISTS {old}_pkey RENAME TO {new}_pkey"))


def downgrade() -> None:
    for old, new, add_key, old_uq, new_uq, uq_cols in reversed(TABLES):
        op.execute(sa.text(f"DELETE FROM {new}"))
        if new_uq:
            op.drop_constraint(new_uq, new, type_="unique")
        if add_key:
            op.drop_index(f"ix_{new}_account_key", table_name=new)
            op.drop_constraint(f"fk_{new}_account_key", new, type_="foreignkey")
            op.drop_column(new, "account_key")
        op.rename_table(new, old)
        if old_uq:
            # 원래 uq는 account_key가 없던 컬럼 구성으로 되돌린다.
            orig = {
                "uq_overseas_row_aliases_norm": ["alias_norm"],
                "uq_overseas_reason_translations_hash": ["source_hash"],
                "uq_overseas_carry_defs_key": ["kind", "def_key"],
                "uq_overseas_anomaly_rules_key": ["rule_key"],
                "uq_overseas_variance_reasons_key": [
                    "basis_year", "basis_month", "kind", "stage", "base_version", "period",
                    "lab_scope", "category_key",
                ],
                "uq_overseas_variance_drivers_key": [
                    "basis_year", "basis_month", "kind", "lab_scope", "driver_key",
                ],
            }[old_uq]
            op.create_unique_constraint(old_uq, old, orig)

    for old, new, *_ in TABLES:
        op.execute(sa.text(f"ALTER SEQUENCE IF EXISTS {new}_id_seq RENAME TO {old}_id_seq"))
        op.execute(sa.text(f"ALTER INDEX IF EXISTS {new}_pkey RENAME TO {old}_pkey"))
