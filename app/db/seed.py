"""19개 비용계정 + (실제 단계가 정해진 4개 계정의) 진행 프로세스를 심는 시드 스크립트.

스키마(테이블 구조)를 바꾸는 게 아니라 데이터(행)만 채워 넣는 거라 Alembic 마이그레이션과는
분리해서 관리한다. 실행: `uv run python -m app.db.seed`
재실행해도 안전하도록(이미 있으면 건너뜀) 작성했다.
"""

from sqlalchemy.orm import Session

from app.core.enums import AccountStatus, CodeGroup, OverseasSheetType, VersionStatus
from app.db.session import SessionLocal
from app.models import Account, AccountProgress, AccountStep, CommonCode, CommonCodeGroup, OverseasLab, OverseasLabSheet

# 해외연구소 파트 14개 + 가상 행 _ETC (pk, lab_code, part_code, name, country, sort_order) — 프론트 OV_LABS와 동일.
# sort_order는 lab_code 단위 표시 순서(같은 lab_code의 파트들은 같은 값) — 탭·집계표가 이 순서로 나온다.
OVERSEAS_LABS: list[tuple[str, str, str | None, str, str, int]] = [
    ("SARC:CPU", "SARC", "CPU", "SARC+ACL · CPU", "미국", 10),
    ("SARC:GPU", "SARC", "GPU", "SARC+ACL · GPU (ACL)", "미국", 10),
    # CPU+GPU 합산과 별개로 연구소에서 받는 공식 총계 원본값. part_code="TOTAL"은 프론트
    # ovLabsFromApi가 L.parts 그룹핑에서 제외하므로 OV_ALLPK(전체 집계)에는 안 섞인다 —
    # "총계" 탭 전용 조회(ovCostView의 totPk)로만 쓰인다.
    ("SARC:TOTAL", "SARC", "TOTAL", "SARC+ACL · 총계", "미국", 10),
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
    # 담기 위한 가상 행. cost_entries 등 공용 테이블의 scope_code를 그대로 재사용하기 위함.
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
CODE_GROUPS: list[tuple[str, str, str]] = [
    (CodeGroup.ACCOUNT_STATUS.value, "계정 진행 상태", "accounts.status에 저장되는 편성 진행 상태 코드"),
    (CodeGroup.OVERSEAS_SHEET_TYPE.value, "해외연구소 시트 종류", "overseas_lab_sheets.sheet_key에 저장되는 워크북 시트 종류"),
    (CodeGroup.OVERSEAS_CATEGORY.value, "해외연구소 비용항목", "cost_entries.category_code(account_code=overseas)에 저장되는 고정 비용항목 코드. 사용자 추가(커스텀) 행은 이 목록 밖의 값을 쓸 수 있다"),
    (CodeGroup.VERSION_STATUS.value, "버전 진행 상태", "versions.status에 저장되는 속보·실적 버전 진행 상태 코드"),
    (
        CodeGroup.OVERSEAS_FX_RULE.value, "해외연구소 환산 환율 기준",
        "해외연구소 화면이 실적 구간(마감된 달) 환산에 말일(last)·월평균(avg) 중 뭘 쓸지. code='default' "
        "행의 name이 예외가 없는 모든 달에 적용되는 기본값이고, 특정 달만 다르게 쓸 때는 "
        "overseas_fx_rule_overrides에 그 연월 행을 넣는다.",
    ),
]

# common_codes(group_code=OVERSEAS_SHEET_TYPE)의 code 값. (code, name, sort_order)
OVERSEAS_SHEET_TYPE_CODES: list[tuple[OverseasSheetType, str, int]] = [
    (OverseasSheetType.COST, "비용", 10),
    (OverseasSheetType.VARIANCE, "증감분석", 20),
    (OverseasSheetType.VENDOR, "외주용역업체", 30),
]

# common_codes(group_code=OVERSEAS_CATEGORY)의 code 값. (code, name(한글), description(영문),
# parent_code, sort_order). 프론트 core.js의 OV_EL과 1:1로 맞춰야 한다 — 늘어나면 여기도 같이 고칠 것
# (app/mcp_server.py의 COST_CATEGORY_KEYS 하드코딩도 같이 맞춰둔다).
# A·hr·labor·asset·direct·gen·mgr·itx는 실제 비용이 안 꽂히는 집계 전용 "그룹" 행이다(leaf가 아니라
# 자식이 있는 행) — cost_entries.category_code로는 못 쓰게 _check_category가 따로 막는다
# (하위 행이 하나라도 있으면 그룹으로 취급). tot·B·pct는 화면 계산 전용이라(A+B 합계, 비율) 아예
# 코드를 안 둔다 — parent_code로 계층을 표현할 대상이 없다.
OVERSEAS_CATEGORY_CODES: list[tuple[str, str, str, str | None, int]] = [
    ("hc", "인력", "Headcount", None, 10),
    ("A", "개발비", "Development Expense (A)", None, 20),
    ("hr", "인원성경비", "HR related", "A", 30),
    ("labor", "인건비", "Labor Cost", "hr", 40),
    ("sal", "급여", "Salaries & Wages", "labor", 50),
    ("bonus", "상여", "Bonus (MBO etc)", "labor", 60),
    ("welf", "복리후생", "Welfare, Health Insurance", "labor", 70),
    ("edu", "교육훈련비", "Education & Training Exp", "hr", 80),
    ("travel", "출장비", "Travel Exp", "hr", 90),
    ("asset", "자산성경비", "Asset related Exp", "A", 100),
    ("dep", "감가상각비", "Depreciation Exp", "asset", 110),
    ("amort", "무형자산상각", "Amortization Exp", "asset", 120),
    ("rent", "임차료", "Rent Exp", "asset", 130),
    ("repair", "수선비", "Repair & Maintenance Exp", "asset", 140),
    ("ins", "보험료", "Insurance Exp", "asset", 150),
    ("direct", "직접개발비", "Direct R&D Exp", "A", 160),
    ("supp", "개발소모품비", "Product Supplies Exp", "direct", 170),
    ("test", "인증테스트비", "Test Exp", "direct", 180),
    ("ord", "경상연구비", "Ordinary R&D Exp", "direct", 190),
    ("gen", "일반운영비", "General Operation Exp", "A", 200),
    ("comm", "지급수수료", "Commission & Service Exp", "gen", 210),
    ("it", "전산비", "IT Expense", "gen", 220),
    ("cons", "일반소모품", "Consumable Supplies Expense", "gen", 230),
    ("util", "수도광열비", "Light&Fuel Exp", "gen", 240),
    ("car", "차량유지비", "Car R&M Exp", "gen", 250),
    ("tel", "통신비", "Telecom Exp", "gen", 260),
    ("pub", "도서인쇄비", "Publishing Exp", "gen", 270),
    ("oth", "잡비", "others", "gen", 280),
    ("mgr", "통제성경비", "Managerial Exp", "A", 290),
    ("conv", "회의비", "Convention Exp", "mgr", 300),
    ("event", "행사비", "Event Exp", "mgr", 310),
    ("social", "교제비", "Social Exp", "mgr", 320),
    ("misc", "기타경비", "Misc.", "A", 330),
    ("tax", "조세공과", "Taxes and Duties", "A", 340),
    ("nonop", "영업외수지", "Non-operational", "A", 350),
    ("itx", "IT 관련 비용", "IT Expenses", None, 360),
    ("eda", "EDA Tool", "EDA Tool", "itx", 370),
    ("emu", "Emulator", "Emulator", "itx", 380),
    ("swl", "SW/License구매", "SW/License Purchase", "itx", 390),
    ("svr", "SVR/Storage임대", "Rent for SVR/Storage", "itx", 400),
    ("itoth", "기타(IT)", "Other IT Expenses", "itx", 410),
    ("outs", "외주용역비", "Outsourcing Expense(Only for R&D)", None, 420),
]

# versions.status가 가리키는 공통코드. (code, name, sort_order) — not_started(예정)는 실제로는
# 저장되지 않는 값이지만(행이 없으면 예정), 화면 라벨·셀렉트 옵션으로 쓰려고 같이 둔다.
VERSION_STATUS_CODES: list[tuple[VersionStatus, str, int]] = [
    (VersionStatus.NOT_STARTED, "예정", 1),
    (VersionStatus.DRAFTING, "작성중", 2),
    (VersionStatus.DONE, "완료", 3),
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

    existing_code_rows = {
        (c.group_code, c.code): c
        for c in db.query(CommonCode).filter(
            CommonCode.group_code.in_(
                [
                    CodeGroup.ACCOUNT_STATUS.value, CodeGroup.OVERSEAS_SHEET_TYPE.value,
                    CodeGroup.OVERSEAS_CATEGORY.value, CodeGroup.VERSION_STATUS.value,
                    CodeGroup.OVERSEAS_FX_RULE.value,
                ]
            )
        ).all()
    }
    existing_codes = set(existing_code_rows)
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
    for code, name, description, parent_code, sort_order in OVERSEAS_CATEGORY_CODES:
        existing_row = existing_code_rows.get((CodeGroup.OVERSEAS_CATEGORY.value, code))
        if existing_row is not None:
            existing_row.parent_code = parent_code
            existing_row.sort_order = sort_order
            continue
        db.add(
            CommonCode(
                group_code=CodeGroup.OVERSEAS_CATEGORY.value, code=code, name=name,
                description=description, parent_code=parent_code, sort_order=sort_order,
            )
        )
    for code, name, sort_order in VERSION_STATUS_CODES:
        if (CodeGroup.VERSION_STATUS.value, code.value) in existing_codes:
            continue
        db.add(
            CommonCode(group_code=CodeGroup.VERSION_STATUS.value, code=code.value, name=name, sort_order=sort_order)
        )

    # 해외연구소 환산 환율 기준의 기본값 — 월 단위 예외(overseas_fx_rule_overrides)가 없는 모든 달이
    # 이 값을 쓴다. 이 행이 있어야 "지금 기준이 뭐냐"의 답이 코드가 아니라 DB에 남는다.
    if (CodeGroup.OVERSEAS_FX_RULE.value, "default") not in existing_codes:
        db.add(CommonCode(group_code=CodeGroup.OVERSEAS_FX_RULE.value, code="default", name="last", sort_order=10))

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
