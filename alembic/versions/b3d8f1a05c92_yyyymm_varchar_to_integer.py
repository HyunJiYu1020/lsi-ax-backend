"""yyyymm을 varchar(6)에서 INTEGER로 — 편성 기준연월을 숫자로 담는다

왜: `yyyymm`은 "202609"처럼 늘 6자리 숫자인데 `varchar(6)`로 담고 있었다. 고정폭이라 문자열
크기 비교가 날짜순 비교가 되긴 하지만(`max(yyyymm)`이 최신 달), 그 성질은 INTEGER도 똑같이
가진다. 숫자로 두면 추가로:

* **SQL에서 연·월을 분해할 수 있다** — `yyyymm / 100`이 연도, `yyyymm % 100`이 월이다. 여태
  파이썬에서 `int(ym[:4])` · `int(ym[4:6])`로 쪼개고 있었고(`periods.ym_year` · `ym_month`와
  호출부 15곳), 그래서 DB 쪽에서 연 단위로 묶는 쿼리를 쓸 수 없었다.
* **형식 CHECK가 정규식에서 범위 비교로 바뀐다** — `yyyymm ~ '^\\d{4}(0[1-9]|1[0-2])$'` 대신
  `yyyymm % 100 BETWEEN 1 AND 12`다. 뜻이 그대로 드러나고, 값이 애초에 숫자라 "숫자인지"를
  따로 검사할 필요가 없다.
* 인덱스가 조금 작아진다(4바이트 vs 7바이트). `ix_cost_entries_account_version_yyyymm`처럼
  읽기 핫패스에 들어간 인덱스라 의미가 아주 없지는 않다.

**`uploads`만 nullable로 간다.** 경영계획 업로드는 달이 없어서 `yyyymm`에 빈 문자열을 넣어
왔다(이 마이그레이션 시점 13행, 전부 `account_key='overseas'` · `version='P02'`). 빈 문자열은
정수로 갈 수 없으니 NULL로 옮긴다 — "달이 없는 자료"를 빈 문자열로 적던 것을 NULL로 적는 것
뿐이고, 그때 version이 그 해 차수(P02)를 가리킨다는 규칙은 그대로다. `uploads.yyyymm`은
UNIQUE 제약에 들어 있지 않고(`ix_uploads_account_scope_month_recent` 인덱스에만 쓴다)
`ON CONFLICT` 대상도 아니라서, NULL을 서로 다른 값으로 취급하는 Postgres 성질이 중복 삽입
사고를 낼 자리가 없다.

`overseas_reason_rows` · `overseas_vendor_rows`의 DB 기본값 `''`도 같이 뗀다. 두 테이블은
`yyyymm ~ '^\\d{6}$'` CHECK를 걸고 있어서 **빈 문자열이 애초에 저장될 수 없었다** — 도달 못 하는
기본값이었다(실데이터 위반 0행이 그 증거). 호출부(`services/reason_rows.py` · `services/vendor.py`)는
값이 없으면 400으로 멈추므로 기본값 없이도 동작이 같다.

그 두 테이블의 CHECK는 `^\\d{6}$`로 느슨해서 13월·00월도 통과했다. 옮기면서 나머지 네 테이블과
같은 규칙(월 1~12)으로 조인다 — 옮기기 전 확인했고 위반 행은 없었다.

Revision ID: b3d8f1a05c92
Revises: a1c7e9f35b20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3d8f1a05c92"
down_revision: Union[str, None] = "a1c7e9f35b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (테이블, 기존 CHECK 이름) — 전부 정규식 기반이라 타입을 바꾸기 전에 떼야 한다.
_TABLES: tuple[tuple[str, str], ...] = (
    ("cost_entries", "ck_cost_entries_yyyymm_format"),
    ("extra_cols", "ck_extra_cols_yyyymm_format"),
    ("overseas_reason_rows", "ck_overseas_reason_rows_yyyymm_format"),
    ("overseas_vendor_rows", "ck_overseas_vendor_rows_yyyymm_format"),
    ("overseas_cost_headers", "ck_overseas_cost_headers_yyyymm_format"),
)

# 숫자가 된 뒤의 형식 규칙. 연도 하한을 2000으로 둔 이유는 6자리를 벗어난 값(예: 9999 같은
# 자리수 사고)을 걸러내려는 것이고, 상한 2999는 자리수만 지키게 하는 느슨한 울타리다.
_INT_CHECK = "yyyymm / 100 BETWEEN 2000 AND 2999 AND yyyymm % 100 BETWEEN 1 AND 12"

# 되돌릴 때 쓸 원래 정규식. 두 테이블은 원래 느슨한 쪽이었으니 그대로 복원한다.
_OLD_REGEX: dict[str, str] = {
    "cost_entries": r"yyyymm ~ '^\d{4}(0[1-9]|1[0-2])$'",
    "extra_cols": r"yyyymm ~ '^\d{4}(0[1-9]|1[0-2])$'",
    "overseas_reason_rows": r"yyyymm ~ '^\d{6}$'",
    "overseas_vendor_rows": r"yyyymm ~ '^\d{6}$'",
    "overseas_cost_headers": r"yyyymm ~ '^\d{4}(0[1-9]|1[0-2])$'",
}

# DB 기본값 ''가 실제로 걸려 있는 테이블. 타입을 바꾸기 전에 떼야 한다 — 안 떼면 Postgres가
# 기본값 식 ''를 integer로 캐스팅하려다 실패한다.
_HAS_EMPTY_DEFAULT: tuple[str, ...] = ("overseas_reason_rows", "overseas_vendor_rows", "uploads")


def upgrade() -> None:
    for table, check_name in _TABLES:
        op.drop_constraint(check_name, table, type_="check")

    for table in _HAS_EMPTY_DEFAULT:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN yyyymm DROP DEFAULT")

    # uploads는 "달이 없는 자료"를 빈 문자열로 적어 왔다 — 정수로 못 가니 NULL로 먼저 옮긴다.
    op.alter_column("uploads", "yyyymm", existing_type=sa.String(6), nullable=True)
    op.execute("UPDATE uploads SET yyyymm = NULL WHERE yyyymm = ''")
    op.alter_column(
        "uploads",
        "yyyymm",
        existing_type=sa.String(6),
        type_=sa.Integer(),
        existing_nullable=True,
        nullable=True,
        postgresql_using="yyyymm::integer",
        comment='이 파일이 속한 기준연월 YYYYMM(예: 202609). 경영계획처럼 달이 없는 자료는 NULL',
    )

    for table, _ in _TABLES:
        op.alter_column(
            table,
            "yyyymm",
            existing_type=sa.String(6),
            type_=sa.Integer(),
            existing_nullable=False,
            nullable=False,
            postgresql_using="yyyymm::integer",
        )
        op.create_check_constraint(f"ck_{table}_yyyymm_format", table, _INT_CHECK)


def downgrade() -> None:
    for table, _ in _TABLES:
        op.drop_constraint(f"ck_{table}_yyyymm_format", table, type_="check")
        op.alter_column(
            table,
            "yyyymm",
            existing_type=sa.Integer(),
            type_=sa.String(6),
            existing_nullable=False,
            nullable=False,
            postgresql_using="lpad(yyyymm::text, 6, '0')",
        )
        op.create_check_constraint(f"ck_{table}_yyyymm_format", table, _OLD_REGEX[table])

    op.alter_column(
        "uploads",
        "yyyymm",
        existing_type=sa.Integer(),
        type_=sa.String(6),
        existing_nullable=True,
        nullable=True,
        postgresql_using="lpad(yyyymm::text, 6, '0')",
        comment='이 파일이 속한 기준연월 "YYYYMM". 경영계획처럼 달이 없는 자료는 빈 문자열',
    )
    # NULL을 다시 빈 문자열로 되돌린 뒤에야 NOT NULL을 걸 수 있다.
    op.execute("UPDATE uploads SET yyyymm = '' WHERE yyyymm IS NULL")
    op.alter_column("uploads", "yyyymm", existing_type=sa.String(6), nullable=False)

    for table in _HAS_EMPTY_DEFAULT:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN yyyymm SET DEFAULT ''")
