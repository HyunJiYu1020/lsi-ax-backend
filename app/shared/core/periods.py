"""기간(연·월·분기) 표기와 변환 — 전 계정 공용 순수 유틸. DB도 HTTP도 모른다.

yyyymm은 "YYYYMM" 6자리 고정폭이다 — 고정폭이라 문자열 크기 비교가 곧 날짜순 비교가 되고
(`max(yyyymm)`이 최신 달), 그래서 DB 쿼리에서 따로 날짜 타입으로 바꾸지 않고 그대로 정렬·비교한다.
"""

# 기간 코드 → 그 기간에 드는 월 인덱스(0=1월). 조회 파라미터(period=y|q1~q4|m1~m12)를 그대로 받는다.
PERIOD_IX: dict[str, tuple[int, ...]] = {
    "y": tuple(range(12)),
    "q1": (0, 1, 2), "q2": (3, 4, 5), "q3": (6, 7, 8), "q4": (9, 10, 11),
    **{f"m{m}": (m - 1,) for m in range(1, 13)},
}

# 분기말 월 인덱스(3·6·9·12월). CP 계약처럼 분기 정산인 금액을 분기말에 몰아넣을 때 쓴다.
QUARTER_END_IX: tuple[int, ...] = (2, 5, 8, 11)


def yyyymm(year: int, month: int) -> str:
    """(2026, 9) -> "202609"."""
    return f"{year:04d}{month:02d}"


def ym_year(ym: str) -> int:
    """"202609" -> 2026."""
    return int(ym[:4])


def ym_month(ym: str) -> int:
    """"202609" -> 9."""
    return int(ym[4:6])


def month_value(cats: dict[str, list], key: str, j: int):
    """그 계정의 j월(0-based) 값. 행 자체가 없거나 배열이 짧으면 미등록(None)이다.

    공통 RULE(빈칸 처리): 없는 칸은 0이 아니라 None이다 — 0으로 돌려주면 "0원으로 올라온 달"과
    "아직 안 올라온 달"이 구분되지 않는다. 계산에 쓸 때만 호출부가 0으로 바꿔 더한다.
    """
    arr = cats.get(key)
    return arr[j] if arr and j < len(arr) else None
