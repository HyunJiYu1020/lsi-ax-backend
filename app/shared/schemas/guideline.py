from datetime import datetime

from pydantic import BaseModel


class GuidelineIn(BaseModel):
    topic: str
    text: str
    account_key: str = ""
    applies_to: str = "all"
    source: str = "chat"
    # 뜻이 겹치는 지침이 이미 있어도 "둘 다 필요하다"는 답을 받았다는 표시. 기본값이 False인 게
    # 핵심이다 — 모델이 아무것도 안 넣으면 막히는 쪽이 기본이어야, 묻지 않고 쌓는 일이 안 생긴다.
    confirm_new_topic: bool = False


class GuidelineOut(BaseModel):
    topic: str
    text: str
    account_key: str
    applies_to: str
    source: str
    updated_at: datetime | None = None
    # 같은 주제의 지침을 덮어쓴 경우 그 전 문장. 화면·챗봇이 "'억원'에서 '백만원'으로 바꿨습니다"를
    # 보여줄 수 있어야 한다 — 바뀐 걸 모르고 지나가면 보고서 단위가 조용히 달라진다.
    replaced_text: str = ""
