"""memos·memo_cols 삭제 + error_logs를 사내 표준(sait error_log) 컬럼으로 재편

Revision ID: c3e18a7d5b94
Revises: a1c7d4f08b32
Create Date: 2026-11-19

### memos · memo_cols 삭제

둘 다 "화면에 보이지 않는 메모 전용 열"을 담던 테이블이다. 챗봇(MCP 도구)으로는 만들 수 있었지만
그리드에 렌더되지 않아 사용자가 확인할 방법이 없었다 — 챗봇 프롬프트가 직접 "이걸 쓰면 사용자
눈에는 아무것도 안 생긴 것처럼 보인다"고 경고하고 있었다. 같은 자리를 `extra_cols`가 대신한다
(모델 주석에 "MemoCol과 같은 모양의 레지스트리"라고 적혀 있던 그 후속 테이블이고, 이쪽은 화면이
실제로 쓴다). 삭제 시점에 두 테이블 모두 행 0개였다.

### error_logs 재편

컬럼 이름·의미를 사내 표준 DDL(sait `error_log`)에 맞춘다. 나중에 운영 로그를 한 곳으로 모을 때
조회 쿼리·대시보드를 그대로 쓸 수 있어야 하기 때문이다. 테이블명만 이 프로젝트 관례(복수형)를
따라 `error_logs`로 남긴다.

이름이 바뀐 컬럼(데이터는 보존한다 — ALTER ... RENAME):
    method → http_method · path → api_url · status_code → http_status
    error_type → error_code · message → error_message · traceback → stack_trace
    id → log_id (BigInteger로 확대)

새로 생기는 컬럼: trace_id · user_id · session_id · created_at · client_ip · user_agent ·
params · log_level · server_name · response_time · process_status · handler

`log_level`은 NOT NULL인데 기존 행에는 값이 없다 — 지금까지 이 테이블에 들어간 건 전부 미처리
500이었으므로 'ERROR'로 채운다. 이게 맞는 이유: 이번 변경으로 **의도적으로 거절한 4xx도
'WARNING'으로 쌓이기 시작**하는데, 기존 행을 WARNING으로 두면 과거 500들이 전부 "거절"로
분류돼 버린다.

`user_id`는 `users.id`(int) FK가 아니라 String(50)이다 — 표준이 문자열 사번이기 때문이다.
SSO 연동 전까지는 NULL로 남는다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3e18a7d5b94"
down_revision: Union[str, None] = "a1c7d4f08b32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------- memos · memo_cols 삭제 ----------
    # memos가 memo_cols.col_key를 논리적으로 참조하므로(FK는 없다) 셀부터 지운다.
    op.drop_table("memos")
    op.drop_table("memo_cols")

    # ---------- error_logs: 기존 컬럼 이름을 표준으로 ----------
    # 표준은 error_message가 String(1000)·error_code가 String(50)이다. 지금 테이블은 message가
    # Text라 그보다 긴 행이 실제로 들어 있어서(스택트레이스가 섞인 긴 메시지), 좁히기 전에
    # 잘라 둬야 ALTER가 StringDataRightTruncation으로 실패하지 않는다. 자른 전문은 traceback
    # (→ stack_trace)에 그대로 남으므로 정보가 사라지지는 않는다.
    op.execute("UPDATE error_logs SET message = left(message, 1000) WHERE length(message) > 1000")
    op.execute("UPDATE error_logs SET error_type = left(error_type, 50) WHERE length(error_type) > 50")

    op.alter_column("error_logs", "id", new_column_name="log_id", type_=sa.BigInteger())
    op.alter_column("error_logs", "method", new_column_name="http_method")
    op.alter_column("error_logs", "path", new_column_name="api_url", type_=sa.String(500))
    op.alter_column("error_logs", "status_code", new_column_name="http_status", type_=sa.Integer())
    op.alter_column("error_logs", "error_type", new_column_name="error_code", type_=sa.String(50))
    op.alter_column("error_logs", "message", new_column_name="error_message", type_=sa.String(1000))
    op.alter_column("error_logs", "traceback", new_column_name="stack_trace")

    # 표준에서는 이 컬럼들이 모두 nullable이다 — 4xx 로그는 스택트레이스가 없고, 요청 밖에서
    # 찍힌 로그(스크립트·스타트업)는 메서드·경로가 아예 없다.
    op.alter_column("error_logs", "http_method", existing_type=sa.String(10), nullable=True)
    op.alter_column("error_logs", "api_url", existing_type=sa.String(500), nullable=True)
    op.alter_column("error_logs", "http_status", existing_type=sa.Integer(), nullable=True)
    op.alter_column("error_logs", "error_code", existing_type=sa.String(50), nullable=True)
    op.alter_column("error_logs", "error_message", existing_type=sa.String(1000), nullable=True)
    op.alter_column("error_logs", "stack_trace", existing_type=sa.Text(), nullable=True)

    # ---------- error_logs: 표준에 있는 컬럼 추가 ----------
    op.add_column("error_logs", sa.Column("trace_id", sa.String(50), nullable=True))
    op.add_column("error_logs", sa.Column("user_id", sa.String(50), nullable=True))
    op.add_column("error_logs", sa.Column("session_id", sa.String(100), nullable=True))
    op.add_column(
        "error_logs",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column("error_logs", sa.Column("client_ip", sa.String(45), nullable=True))
    op.add_column("error_logs", sa.Column("user_agent", sa.String(500), nullable=True))
    op.add_column("error_logs", sa.Column("params", sa.Text(), nullable=True))
    op.add_column("error_logs", sa.Column("server_name", sa.String(100), nullable=True))
    op.add_column("error_logs", sa.Column("response_time", sa.Integer(), nullable=True))
    op.add_column(
        "error_logs",
        sa.Column("process_status", sa.String(20), server_default=sa.text("'UNPROCESSED'"), nullable=True),
    )
    op.add_column("error_logs", sa.Column("handler", sa.String(50), nullable=True))

    # log_level: 기존 행은 전부 미처리 500이었으므로 ERROR. 채운 뒤 NOT NULL로 조인다.
    op.add_column("error_logs", sa.Column("log_level", sa.String(20), nullable=True))
    op.execute("UPDATE error_logs SET log_level = 'ERROR' WHERE log_level IS NULL")
    op.alter_column("error_logs", "log_level", existing_type=sa.String(20), nullable=False)

    # ---------- 주석 ----------
    op.execute(
        "COMMENT ON TABLE error_logs IS "
        "'API 서버 로그(WARNING 이상). 컬럼은 사내 표준 error_log DDL과 같은 이름·의미.'"
    )
    for col, comment in (
        ("log_id", "로그 id"),
        ("trace_id", "요청 1건을 잇는 추적 id (요청마다 생성)"),
        ("user_id", "요청한 사용자 식별자(사번). SSO 연동 전까지는 NULL — 표준이 문자열 사번이라 users.id FK가 아니다"),
        ("session_id", "세션 id. SSO 연동 전까지는 NULL"),
        ("occurred_at", "발생 시각"),
        ("created_at", "로그 적재 시각"),
        ("client_ip", "클라이언트 IP (IPv6 길이까지 수용)"),
        ("user_agent", "User-Agent 헤더"),
        ("api_url", "요청 경로 (쿼리스트링 제외)"),
        ("http_method", "HTTP 메서드"),
        ("params", "쿼리스트링 + 요청 본문(재현에 필요). 본문을 읽을 수 없으면 쿼리스트링만 남는다"),
        ("log_level", "WARNING(거절된 4xx) | ERROR(미처리 500) | CRITICAL"),
        ("http_status", "응답 상태 코드"),
        ("error_code", "예외 클래스명 (표준의 error_code 자리 — 사내 코드 체계가 생기면 그 값으로 바꾼다)"),
        ("error_message", "예외 메시지 (1000자에서 자른다)"),
        ("stack_trace", "스택트레이스. 4xx는 비어 있을 수 있다"),
        ("server_name", "처리한 서버 호스트명"),
        ("response_time", "응답 소요 시간(ms)"),
        ("process_status", "운영 처리 상태 (UNPROCESSED | ...)"),
        ("handler", "이 로그를 남긴 로거 이름"),
    ):
        op.execute(f"COMMENT ON COLUMN error_logs.{col} IS '{comment}'")


def downgrade() -> None:
    # ---------- error_logs 되돌리기 ----------
    for col in (
        "trace_id", "user_id", "session_id", "created_at", "client_ip", "user_agent",
        "params", "log_level", "server_name", "response_time", "process_status", "handler",
    ):
        op.drop_column("error_logs", col)

    # 되돌린 스키마는 NOT NULL이었다 — 그 사이 쌓인 4xx 로그에는 스택트레이스가 없어서
    # 그대로는 NOT NULL을 걸 수 없다. 빈 문자열로 채우고 조인다(값을 지우는 게 아니라
    # "없음"을 전 스키마가 표현할 수 있는 형태로 바꾸는 것).
    op.execute(
        "UPDATE error_logs SET stack_trace = COALESCE(stack_trace, ''), "
        "error_message = COALESCE(error_message, ''), error_code = COALESCE(error_code, ''), "
        "api_url = COALESCE(api_url, ''), http_method = COALESCE(http_method, ''), "
        "http_status = COALESCE(http_status, 500)"
    )
    op.alter_column("error_logs", "stack_trace", existing_type=sa.Text(), nullable=False)
    op.alter_column("error_logs", "error_message", existing_type=sa.String(1000), nullable=False)
    op.alter_column("error_logs", "error_code", existing_type=sa.String(50), nullable=False)
    op.alter_column("error_logs", "api_url", existing_type=sa.String(500), nullable=False)
    op.alter_column("error_logs", "http_method", existing_type=sa.String(10), nullable=False)
    op.alter_column("error_logs", "http_status", existing_type=sa.Integer(), nullable=False)

    op.alter_column("error_logs", "stack_trace", new_column_name="traceback")
    op.alter_column("error_logs", "error_message", new_column_name="message", type_=sa.Text())
    op.alter_column("error_logs", "error_code", new_column_name="error_type", type_=sa.String(100))
    op.alter_column("error_logs", "http_status", new_column_name="status_code", type_=sa.SmallInteger())
    op.alter_column("error_logs", "api_url", new_column_name="path", type_=sa.String(255))
    op.alter_column("error_logs", "http_method", new_column_name="method")
    op.alter_column("error_logs", "log_id", new_column_name="id", type_=sa.Integer())

    # ---------- memo_cols · memos 복원 (구조만 — 삭제 시점에 데이터가 없었다) ----------
    op.create_table(
        "memo_cols",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_key", sa.String(32), sa.ForeignKey("accounts.key"), nullable=False),
        sa.Column("scope_key", sa.String(40), nullable=False),
        sa.Column("version", sa.String(10), nullable=False),
        sa.Column("col_key", sa.String(40), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.UniqueConstraint("account_key", "scope_key", "version", "col_key"),
        comment="메모 전용 열(존재·이름만). 셀 내용은 memos 참조.",
    )
    op.create_table(
        "memos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_key", sa.String(32), sa.ForeignKey("accounts.key"), nullable=False),
        sa.Column("scope_key", sa.String(40), nullable=False),
        sa.Column("version", sa.String(10), nullable=False),
        sa.Column("row_key", sa.String(40), nullable=False),
        sa.Column("col_key", sa.String(40), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.UniqueConstraint("account_key", "scope_key", "version", "row_key", "col_key"),
        comment="메모 셀. 집계·증감분석 계산에는 들어가지 않는 참고용 텍스트.",
    )
