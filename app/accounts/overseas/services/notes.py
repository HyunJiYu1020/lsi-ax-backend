"""증감 사유 메모(notes) 저장 — 단건(PUT /overseas/notes)과 배치(PUT /overseas/state)가 같은
upsert 규칙을 쓰도록 한 곳에 둔다."""

from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.shared.models.common import Note
from app.accounts.overseas.services.labs import ACCOUNT_KEY


def upsert_note(db: Session, base_version: str, period: str, scope: str, category_key: str, text: str) -> None:
    """단일 메모 저장용(PUT /overseas/notes). 배치 저장(save_state)은 bulk_upsert_notes를 쓴다."""
    stmt = pg_insert(Note).values(
        account_key=ACCOUNT_KEY, base_version=base_version, period=period, scope=scope, category_key=category_key, note_text=text
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["account_key", "base_version", "period", "scope", "category_key"],
        set_={"note_text": stmt.excluded.note_text, "updated_at": datetime.now(timezone.utc)},
    )
    db.execute(stmt)


def bulk_upsert_notes(db: Session, rows: list[tuple[str, str, str, str, str]]) -> None:
    """rows: (base_version, period, scope, category_key, text). 메모 수만큼 따로 upsert하지 않고 한 번에 처리한다."""
    dedup = {(bv, p, s, ck): text for bv, p, s, ck, text in rows}
    if not dedup:
        return
    stmt = pg_insert(Note).values(
        [
            {"account_key": ACCOUNT_KEY, "base_version": bv, "period": p, "scope": s, "category_key": ck, "note_text": text}
            for (bv, p, s, ck), text in dedup.items()
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["account_key", "base_version", "period", "scope", "category_key"],
        set_={"note_text": stmt.excluded.note_text, "updated_at": datetime.now(timezone.utc)},
    )
    db.execute(stmt)
