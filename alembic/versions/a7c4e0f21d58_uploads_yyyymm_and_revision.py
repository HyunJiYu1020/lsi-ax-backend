"""업로드 이력에 기준월(yyyymm)을 붙이고 version을 **차수**로 바꾼다

왜: uploads는 "어느 달 파일인지"를 담을 자리가 없어서 version에 **자리 이름**(cur|prev|prior|plan)을
적고 있었다. 자리는 보는 달에 따라 달라지는 값이라 — 9월에 올린 파일이 11월 화면에서도 cur이다 —
같은 행이 아무 달에나 "이번에 받은 파일"로 보였다. 그걸 가리려고 읽는 쪽은 "그 달에 비용 값이
실제로 있는 연구소에만 파일명을 붙이는" 우회를 하고 있었다(services/state.py).

비용(cost_entries.yyyymm) · 증감사유 · 외주용역이 이미 기준월을 들고 있다. 이력만 다른 축이었다.

version은 같은 달에 **몇 번째로 받은 파일인지**(v1 · v2 …)가 된다 — 경영계획이 차수를
P00 · P01 · P02로 관리하는 것과 같은 관례다. 자리(cur·prev)는 저장하지 않고 읽을 때 달로 정한다.

기존 행의 달 복원: 파일명에 달이 박혀 있다(OV_SIRC_2608.xlsx · M2609A · 26.09속보). 그걸로 먼저
찾고, 못 찾으면 그 행이 지금까지 해석되던 자리 규칙으로 떨어뜨린다(prev=최신 속보월의 직전 달 등).
경영계획 파일(OV_*_2026BP.xlsx)은 달이 없는 자료라 yyyymm을 비우고, version에 그 해의 적용 차수를
적는다.

Revision ID: a7c4e0f21d58
Revises: f5a2c3d7e9b1
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c4e0f21d58"
down_revision: Union[str, None] = "f5a2c3d7e9b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "uploads",
        sa.Column(
            "yyyymm", sa.String(length=6), nullable=False, server_default="",
            comment='이 파일이 속한 기준연월 "YYYYMM". 경영계획처럼 달이 없는 자료는 빈 문자열이다',
        ),
    )

    # ① 파일명에서 달을 찾는다. M2609A → 202609 · _2608.(_2609A.) → 202608 · 26.09 → 202609
    # UPDATE ... FROM LATERAL은 갱신 대상 테이블의 컬럼을 참조할 수 없어서 regexp_replace로 바로 만든다.
    # 패턴은 ^…$로 묶고 수량자를 전부 greedy로 둔다 — Postgres는 **첫 수량자**가 non-greedy면 식 전체가
    # non-greedy가 되어 매치가 파일명 끝까지 닿지 않고, 바뀐 값 뒤에 원본 꼬리가 그대로 남는다
    # ("202609속보.xlsx" → varchar(6) 초과로 실패했다).
    for pattern in (
        r"^.*M(\d{2})(0[1-9]|1[0-2])A.*$",
        r"^.*_(\d{2})(0[1-9]|1[0-2])A?\..*$",
        r"^.*(\d{2})\.(0[1-9]|1[0-2]).*$",
    ):
        op.execute(
            sa.text(
                "UPDATE uploads SET yyyymm = regexp_replace(filename, :pat, '20\\1\\2') "
                "WHERE account_key = 'overseas' AND yyyymm = '' AND filename ~ :pat"
            ).bindparams(pat=pattern)
        )

    # ② 파일명에 달이 없는 행은 지금까지 해석되던 자리 규칙으로 떨어뜨린다.
    #    기준은 비용에 들어 있는 최신 속보월 — 읽는 쪽(current_basis_month)이 쓰던 값과 같다.
    op.execute(
        """
        UPDATE uploads u SET yyyymm = CASE v.version
            WHEN 'cur'   THEN b.ym
            WHEN 'prev'  THEN to_char((to_date(b.ym,'YYYYMM') - interval '1 month'), 'YYYYMM')
            WHEN 'prior' THEN substr(b.ym,1,4)::int - 1 || '12'
            ELSE ''
        END
        FROM uploads v, (
            SELECT coalesce(max(yyyymm), to_char(now(),'YYYYMM')) AS ym FROM cost_entries
            WHERE account_code='overseas' AND version='forecast'
        ) b
        WHERE u.id = v.id AND u.account_key='overseas' AND u.yyyymm = '' AND v.version <> 'plan'
        """
    )

    # ③ version을 차수로. 같은 (계정, 행, 기준월) 안에서 올린 순서대로 v1 · v2 …
    #    경영계획 파일은 달이 없으므로 그 해의 적용 차수(management_plan_entries의 최신 차수)를 쓴다.
    op.execute(
        """
        UPDATE uploads u SET version = 'v' || r.n
        FROM (
            SELECT id, row_number() OVER (
                PARTITION BY account_key, scope_key, yyyymm ORDER BY uploaded_at, id
            ) AS n
            FROM uploads WHERE yyyymm <> ''
        ) r
        WHERE u.id = r.id
        """
    )
    op.execute(
        """
        UPDATE uploads SET version = coalesce(
            (SELECT max(plan_no) FROM management_plan_entries WHERE account_code='overseas'), 'P00')
        WHERE account_key='overseas' AND yyyymm = '' AND version = 'plan'
        """
    )

    op.drop_index("ix_uploads_account_version_scope_recent", table_name="uploads")
    op.create_index(
        "ix_uploads_account_scope_month_recent", "uploads",
        ["account_key", "scope_key", "yyyymm", "uploaded_at"],
    )
    op.alter_column(
        "uploads", "version",
        comment="그 기준월에 몇 번째로 받은 파일인지(v1 · v2 …). 경영계획처럼 달이 없는 자료는 차수(P02)",
    )


def downgrade() -> None:
    op.drop_index("ix_uploads_account_scope_month_recent", table_name="uploads")
    op.create_index(
        "ix_uploads_account_version_scope_recent", "uploads",
        ["account_key", "version", "scope_key", "uploaded_at"],
    )
    # 자리 이름으로 되돌린다 — 최신 속보월이 cur, 그 직전 달이 prev, 전년 12월이 prior.
    op.execute(
        """
        UPDATE uploads u SET version = CASE
            WHEN u.yyyymm = '' THEN 'plan'
            WHEN u.yyyymm = b.ym THEN 'cur'
            WHEN u.yyyymm = to_char((to_date(b.ym,'YYYYMM') - interval '1 month'), 'YYYYMM') THEN 'prev'
            ELSE 'prior'
        END
        FROM (
            SELECT coalesce(max(yyyymm), to_char(now(),'YYYYMM')) AS ym FROM cost_entries
            WHERE account_code='overseas' AND version='forecast'
        ) b
        WHERE u.account_key = 'overseas'
        """
    )
    op.drop_column("uploads", "yyyymm")
