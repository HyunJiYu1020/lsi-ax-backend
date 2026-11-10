"""19개 비용계정 + (실제 단계가 정해진 4개 계정의) 진행 프로세스를 심는 시드 스크립트.

스키마(테이블 구조)를 바꾸는 게 아니라 데이터(행)만 채워 넣는 거라 Alembic 마이그레이션과는
분리해서 관리한다. 실행: `uv run python -m app.db.seed`
재실행해도 안전하도록(이미 있으면 건너뜀) 작성했다.
"""

from sqlalchemy.orm import Session

from app.core.enums import AccountStatus, CodeGroup, OverseasSheetType
from app.db.session import SessionLocal
from app.models import Account, AccountProgress, AccountStep, CommonCode, CommonCodeGroup, OverseasLab, OverseasLabSheet

# 13개 해외연구소 파트 (pk, lab_code, part_code, name, country, sort_order) — 프론트 OV_LABS와 동일.
# sort_order는 lab_code 단위 표시 순서(같은 lab_code의 파트들은 같은 값) — 탭·집계표가 이 순서로 나온다.
OVERSEAS_LABS: list[tuple[str, str, str | None, str, str, int]] = [
    ("SARC:CPU", "SARC", "CPU", "SARC+ACL · CPU", "미국", 10),
    ("SARC:GPU", "SARC", "GPU", "SARC+ACL · GPU (ACL)", "미국", 10),
    ("DSRA", "DSRA", None, "DSRA", "미국", 20),
    ("SIRC", "SIRC", None, "SIRC", "이스라엘", 30),
    ("SSIR", "SSIR", None, "SSIR", "인도", 40),
    ("SCSC", "SCSC", None, "SCSC", "영국", 50),
    ("SSCR", "SSCR", None, "SSCR", "중국", 60),
    ("TDC", "TDC", None, "TDC", "대만", 70),
    ("DSRJ", "DSRJ", None, "DSRJ", "일본", 80),
    ("SRPOL", "SRPOL", None, "SRPOL", "폴란드", 90),
    ("SRR", "SRR", None, "SRR", "러시아", 100),
    ("CP", "CP", None, "CP", "이스라엘", 110),
    ("SRCX", "SRCX", None, "SRCX", "중국", 120),
    # 실제 연구소가 아니라 "원화 기타"처럼 특정 연구소에 속하지 않는 계정 전체 단위 값을
    # 담기 위한 가상 행. overseas_cost_entries 스키마를 그대로 재사용하기 위함.
    ("_ETC", "_ETC", None, "(계정 공통) 원화 기타", "-", 999),
]

# 연구소(lab_code)별 워크북 시트 구성. SARC+ACL만 비용 시트 하나고, 나머지는 비용·증감분석·
# 외주용역업체 3개 — 연구소가 늘어나거나 시트 구성이 바뀌면 이 목록만 고치면 된다(코드 변경 없음).
_STD_SHEETS = [(OverseasSheetType.COST, 10), (OverseasSheetType.VARIANCE, 20), (OverseasSheetType.VENDOR, 30)]
OVERSEAS_LAB_SHEETS: list[tuple[str, OverseasSheetType, int]] = [
    (lab_code, sheet_type, order)
    for lab_code in ("DSRA", "SIRC", "SSIR", "SCSC", "SSCR", "TDC", "DSRJ", "SRPOL", "SRR", "CP", "SRCX")
    for sheet_type, order in _STD_SHEETS
] + [("SARC", OverseasSheetType.COST, 10)]

# (key, name, color, data_type) — 프론트 accSeed()의 19개 계정과 동일
# data_type은 "지금 당장 테이블이 있다"는 뜻이 아니라 "이 계정이 어떤 모양의 데이터를 다룰지"에
# 대한 설계 메모다. 실제 전용 화면이 생길 때 이 타입에 맞는 전용 테이블을 만든다.
ACCOUNTS: list[tuple[str, str, str, str]] = [
    ("dep", "감가상각비", "#4C6FFF", "line_items"),
    ("labor", "인건비", "#2DA8FF", "monthly_total"),
    ("outsource", "위탁용역비", "#19B5A2", "line_items"),
    ("consum", "생소비", "#5FCFBE", "line_items"),
    ("patent", "특허료", "#8EA3FF", "monthly_total"),
    ("fixtech", "정액기술료", "#6E7FD8", "monthly_total"),
    ("ratetech", "정률기술료", "#A98BF0", "monthly_total"),
    ("overseas", "해외연구소", "#E9A53A", "matrix"),
    ("sample", "견본비", "#F08A7A", "monthly_total"),
    ("cae", "CAE", "#E8C25C", "monthly_total"),
    ("ermask", "ER/MASK", "#C084E8", "monthly_total"),
    ("fixfee", "고정성 수수료", "#5C9DE0", "monthly_total"),
    ("market", "시장조사비", "#3FB37F", "monthly_total"),
    ("cert", "인증비", "#D9844A", "monthly_total"),
    ("edu", "교육훈련비", "#7DB8F2", "monthly_total"),
    ("claim", "Claim비", "#E06C8C", "monthly_total"),
    ("headcount", "인원성경비", "#62A87C", "monthly_total"),
    ("supplies", "소모품비", "#B9A04A", "monthly_total"),
    ("repair", "수선유지비", "#8C98AF", "monthly_total"),
]

# common_codes.group_code가 참조하는 그룹 레지스트리. (group_code, name, description)
# VERSION · FX_RATE_TYPE은 이 스크립트가 아니라 다른 경로(/common-codes API 등)로
# 이미 데이터가 들어가 있던 그룹이라, 그 FK가 깨지지 않도록 그룹 레지스트리에도 같이 등록해둔다.
CODE_GROUPS: list[tuple[str, str, str]] = [
    (CodeGroup.ACCOUNT_STATUS.value, "계정 진행 상태", "accounts.status에 저장되는 편성 진행 상태 코드"),
    (CodeGroup.OVERSEAS_SHEET_TYPE.value, "해외연구소 시트 종류", "overseas_lab_sheets.sheet_key에 저장되는 워크북 시트 종류"),
    ("VERSION", "버전", "cost_entries 등의 version 컬럼: cur(당월 속보)·prev(전실행)·prior(전년 실적)·plan(경영계획)"),
    ("FX_RATE_TYPE", "환율 종류", "fx_rates.rate_type: end(월말환율)·act(실적환율)·cp(CP환율)"),
]

# common_codes(group_code=OVERSEAS_SHEET_TYPE)의 code 값. (code, name, sort_order)
OVERSEAS_SHEET_TYPE_CODES: list[tuple[OverseasSheetType, str, int]] = [
    (OverseasSheetType.COST, "비용", 10),
    (OverseasSheetType.VARIANCE, "증감분석", 20),
    (OverseasSheetType.VENDOR, "외주용역업체", 30),
]

# accounts.status가 가리키는 공통코드. (code, name, sort_order) — 프론트 ALL_ST와 동일 순서.
ACCOUNT_STATUS_CODES: list[tuple[AccountStatus, str, int]] = [
    (AccountStatus.DRAFTING, "작성중", 1),
    (AccountStatus.DRAFTED, "작성완료", 2),
    (AccountStatus.NOT_SUBMITTED, "미제출", 3),
    (AccountStatus.SUBMITTED, "제출완료", 4),
    (AccountStatus.REVIEWED, "검토완료", 5),
    (AccountStatus.REJECTED, "반려", 6),
]

# 실제 전용 편성 화면(다단계 프로세스)이 이미 구현된 4개 계정만 단계를 심는다.
# 나머지 15개는 아직 "준비 중" 스텁이라 정의된 프로세스가 없고, 그래서 account_progress도 없다
# (account_progress는 account_steps에 실제로 있는 단계만 가리킬 수 있도록 FK로 강제돼 있음).
STEPS: dict[str, list[tuple[str, str]]] = {
    "overseas": [
        ("취합", "연구소 데이터 업로드"),
        ("집계", "집계표"),
        ("증감분석", "증감분석"),
    ],
    "dep": [
        ("취합", "기초자료 업로드"),
        ("가공", "기준정보 맵핑 · 상각 스케줄"),
        ("검증", "데이터 검증 · 수정"),
        ("확정", "최종 편성안 확정"),
        ("분석", "증감분석"),
    ],
    "outsource": [
        ("업로드", "편성안 업로드"),
        ("사유", "변경 사유 확인"),
        ("가공", "기준정보 맵핑"),
        ("검토", "검증 · 편성안 확정"),
        ("편성", "최종 편성값"),
        ("분석", "분석 · 취합 · 업로드"),
    ],
    "consum": [
        ("업로드", "편성안 업로드"),
        ("가공", "기준정보 맵핑"),
        ("사유", "부서별 증감 사유"),
        ("검토", "검증 · 편성안 확정"),
        ("편성", "최종 편성값"),
        ("분석", "분석 · 취합 · 업로드"),
    ],
}


def seed(db: Session) -> None:
    existing = {a.key: a for a in db.query(Account).all()}

    for key, name, color, data_type in ACCOUNTS:
        if key in existing:
            continue
        acct = Account(key=key, name=name, color=color, data_type=data_type, status=AccountStatus.DRAFTING.value)
        db.add(acct)
        db.flush()  # acct.id 채우기
        existing[key] = acct

    for key, steps in STEPS.items():
        acct = existing[key]
        has_steps = db.query(AccountStep).filter_by(account_id=acct.id).first() is not None
        if has_steps:
            continue
        for order, (tag, title) in enumerate(steps, start=1):
            db.add(AccountStep(account_id=acct.id, step_order=order, tag=tag, title=title))
        db.flush()
        has_progress = db.query(AccountProgress).filter_by(account_id=acct.id).first() is not None
        if not has_progress:
            db.add(AccountProgress(account_id=acct.id, current_step_order=1))

    existing_labs = {lab.pk for lab in db.query(OverseasLab).all()}
    for pk, lab_code, part_code, name, country, sort_order in OVERSEAS_LABS:
        if pk in existing_labs:
            continue
        db.add(
            OverseasLab(
                pk=pk, lab_code=lab_code, part_code=part_code, name=name, country=country, sort_order=sort_order
            )
        )

    existing_lab_sheets = {(s.lab_code, s.sheet_key) for s in db.query(OverseasLabSheet).all()}
    for lab_code, sheet_type, sheet_order in OVERSEAS_LAB_SHEETS:
        if (lab_code, sheet_type.value) in existing_lab_sheets:
            continue
        db.add(OverseasLabSheet(lab_code=lab_code, sheet_key=sheet_type.value, sheet_order=sheet_order))

    existing_groups = {g.group_code for g in db.query(CommonCodeGroup).all()}
    for group_code, name, description in CODE_GROUPS:
        if group_code in existing_groups:
            continue
        db.add(CommonCodeGroup(group_code=group_code, name=name, description=description))
    db.flush()  # common_codes가 FK로 참조하기 전에 그룹부터 반영

    existing_codes = {
        (c.group_code, c.code)
        for c in db.query(CommonCode).filter(
            CommonCode.group_code.in_([CodeGroup.ACCOUNT_STATUS.value, CodeGroup.OVERSEAS_SHEET_TYPE.value])
        ).all()
    }
    for code, name, sort_order in ACCOUNT_STATUS_CODES:
        if (CodeGroup.ACCOUNT_STATUS.value, code.value) in existing_codes:
            continue
        db.add(
            CommonCode(group_code=CodeGroup.ACCOUNT_STATUS.value, code=code.value, name=name, sort_order=sort_order)
        )
    for code, name, sort_order in OVERSEAS_SHEET_TYPE_CODES:
        if (CodeGroup.OVERSEAS_SHEET_TYPE.value, code.value) in existing_codes:
            continue
        db.add(
            CommonCode(group_code=CodeGroup.OVERSEAS_SHEET_TYPE.value, code=code.value, name=name, sort_order=sort_order)
        )

    db.commit()


def main() -> None:
    db = SessionLocal()
    try:
        seed(db)
        print(
            f"done — accounts: {db.query(Account).count()}, steps: {db.query(AccountStep).count()}, "
            f"labs: {db.query(OverseasLab).count()}, lab_sheets: {db.query(OverseasLabSheet).count()}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
