"""업로드 이력(uploads)의 차수 규칙 — 같은 (연구소, 기준월)에 몇 번째 파일인지는 서버가 센다."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.models.common import Upload
from app.accounts.overseas.services.labs import ACCOUNT_KEY


def next_revision(db: Session, scope_key: str, yyyymm: str) -> str:
    """그 (연구소, 기준월)의 다음 차수. 경영계획처럼 달이 없는 자료는 차수를 세지 않는다.

    세는 일을 서버가 하는 이유: 올리는 쪽은 그 달에 이미 몇 번 올라왔는지 모른다. 두 사람이
    동시에 올려도 각자 "v1"로 적으면 이력이 덮인 것처럼 보인다."""
    if not yyyymm:
        return ""
    last = db.scalars(
        select(Upload.version)
        .where(Upload.account_key == ACCOUNT_KEY, Upload.scope_key == scope_key, Upload.yyyymm == yyyymm)
        .order_by(Upload.uploaded_at.desc(), Upload.id.desc())
    ).first()
    n = int(last[1:]) if last and last[:1] == "v" and last[1:].isdigit() else 0
    return f"v{n + 1}"
