"""구조 미정 시트(열 구성이 확정되기 전인 표)의 행 입출력. 19개 계정 공용.

위탁용역비·생소비 편성값이 이 모양이다 — 한 행이 계약 하나이고, 열이 40개가 넘는데 그 구성이
아직 달마다 바뀐다(2026-11에도 기준정보 양식이 바뀌었다). 열이 바뀔 때마다 마이그레이션을 하면
화면 한 번 고칠 때마다 DB 배포가 따라붙어서, 결국 "그냥 브라우저에 두자"로 돌아간다. 그래서
`sheet_rows.data`(JSONB)에 화면 키 그대로 담고, 구성이 확정되면 전용 테이블로 옮긴다
(해외연구소 외주용역이 그렇게 `overseas_vendor_rows`로 옮겨갔다).

쓰기가 행 단위가 아니라 **표 단위 교체**인 이유: 화면은 엑셀을 올려 표 전체를 바꾸거나 표를 고친 뒤
[저장]을 누른다. 행 단위 API로 쪼개면 저장 한 번이 수백 개 요청이 되고, 중간에 실패하면 표가
반만 바뀐 상태로 남는다(그게 제일 나쁜 결과다 — 합계는 맞는데 행을 세면 안 맞는 표).
"""

from pydantic import BaseModel, Field


class SheetRowIn(BaseModel):
    """시트 한 행. `row_key`를 비우면 서버가 순서 번호로 채운다 — 업로드한 엑셀에는 행을
    식별할 키가 없는 경우가 많고(계약명이 같은 행이 둘 있기도 하다), 그때 키를 요구하면
    화면이 가짜 키를 만들어 보내게 된다."""

    row_key: str = ""
    sort_order: int | None = None
    data: dict


class SheetRowOut(SheetRowIn):
    row_key: str
    sort_order: int


class SheetRowsIn(BaseModel):
    """표 하나(계정 × 범위 × 버전 × 시트)를 통째로 교체한다."""

    account_key: str
    scope_key: str = "ALL"  # 계정 안의 행 묶음. 범위 구분이 없는 계정은 "ALL"
    version: str  # cur(업데이트) | prev(전월) | prior(전년 실적) | plan(경영계획)
    sheet_key: str = Field(..., description='시트 종류("budget" · "appr" · "rev" …)')
    rows: list[SheetRowIn] = []


class SheetRowsOut(BaseModel):
    account_key: str
    scope_key: str
    version: str
    sheet_key: str
    rows: list[SheetRowOut] = []


class SheetBundleOut(BaseModel):
    """한 계정의 모든 표를 한 응답에 담는다 — 키는 `"<version>|<sheet_key>"`.

    왜 묶음이 필요한가: 화면 하나가 표 열 개 넘게 쓴다(위탁용역비 4버전 × 2시트, 감가상각비 원천
    7표 + 상태). 표마다 한 번씩 부르면 페이지 로드가 요청 수십 개를 동시에 던져서 서버 커넥션
    풀을 말린다(실제로 그렇게 서버가 멈췄다). 화면 입장에서도 표를 따로 받으면 "다 받은 뒤 한 번
    그리기"를 하려고 로딩 상태를 직접 관리해야 한다.
    """

    account_key: str
    scope_key: str
    sheets: dict[str, list[SheetRowOut]] = {}
