"""기준정보 초기 데이터 — 비용계정 · 자산 Class 매핑 · 부서코드 · PLM 과제.

화면(frontend-html/shared/core.js의 seed())이 쓰는 예시 데이터와 **같은 내용**이다. 서버에 붙지
않은 브라우저와 붙은 브라우저가 다른 표를 보면, 담당자는 "서버 연결했더니 부서코드가 바뀌었다"로
읽는다. 실제 운영 데이터는 화면에서 엑셀로 올려 덮어쓴다 — 이 모듈은 빈 DB에서 화면이 제대로
뜨게 하는 출발점이다.

부서코드·PLM 과제는 버전별 표라서 "어느 버전에 심을지"를 정해야 한다. 기준 버전(BASIS)에만
심고, 이후 버전은 서버가 직전 버전을 복사해 만든다(app/shared/core/reference.py).

실행: seed.py가 같이 호출한다(`uv run python -m app.db.seed`). 이미 행이 있으면 건너뛴다.
"""

from sqlalchemy.orm import Session

from app.shared.models.close import CloseDeptRule
from app.shared.models.reference import (
    RefAssetClass,
    RefCostElement,
    RefCostElementSnapshot,
    RefDeptCode,
    RefPlmTask,
    RefProduct,
)

# 초기 데이터를 심을 기준 — 연도표는 이 연도, 버전별 표는 이 버전(2026년 9월 속보)에 들어간다.
# 화면 기본 기준월(core.js S.basis = "2026-09")과 같은 값이다.
BASIS_YEAR = 2026
BASIS_KIND = "forecast"
BASIS_SEQ = 9

COST_ELEMENTS: list[dict[str, str]] = [
    {"gb": "감가상각비", "code": "53450100", "a1": "03 경비", "a2": "05 자산성경비", "a3": "21 감가상각비", "a4": "36 감가상각비", "name": "감가상각비-건물"},
    {"gb": "감가상각비", "code": "53450300", "a1": "03 경비", "a2": "05 자산성경비", "a3": "21 감가상각비", "a4": "36 감가상각비", "name": "감가상각비-기계장치"},
    {"gb": "감가상각비", "code": "53450400", "a1": "03 경비", "a2": "05 자산성경비", "a3": "21 감가상각비", "a4": "36 감가상각비", "name": "감가상각비-공구와기구"},
    {"gb": "감가상각비", "code": "53450500", "a1": "03 경비", "a2": "05 자산성경비", "a3": "21 감가상각비", "a4": "36 감가상각비", "name": "감가상각비-비품"},
    {"gb": "감가상각비", "code": "53450600", "a1": "03 경비", "a2": "05 자산성경비", "a3": "21 감가상각비", "a4": "36 감가상각비", "name": "감가상각비-전산기기"},
    {"gb": "감가상각비", "code": "53450200", "a1": "03 경비", "a2": "05 자산성경비", "a3": "21 감가상각비", "a4": "36 감가상각비", "name": "감가상각비-구축물"},
    {"gb": "감가상각비", "code": "53460100", "a1": "03 경비", "a2": "05 자산성경비", "a3": "21 감가상각비", "a4": "37 무형자산상각비", "name": "무형자산상각비"},
    {"gb": "감가상각비", "code": "53450300", "a1": "03 경비", "a2": "05 자산성경비", "a3": "21 감가상각비", "a4": "36 감가상각비", "name": "감가상각비-기계장치"},
    {"gb": "감가상각비", "code": "53450600", "a1": "03 경비", "a2": "05 자산성경비", "a3": "21 감가상각비", "a4": "36 감가상각비", "name": "감가상각비-전산기기"},
    {"gb": "위탁용역비", "code": "54370700", "a1": "03 경비", "a2": "07 직접개발비", "a3": "28 경상연구개발비", "a4": "45 위탁용역비", "name": "경상연구개발비-위탁용역비-국내"},
    {"gb": "위탁용역비", "code": "54370800", "a1": "03 경비", "a2": "07 직접개발비", "a3": "28 경상연구개발비", "a4": "45 위탁용역비", "name": "경상연구개발비-위탁용역비-해외"},
    {"gb": "위탁용역비", "code": "54370900", "a1": "03 경비", "a2": "07 직접개발비", "a3": "28 경상연구개발비", "a4": "45 위탁용역비", "name": "경상연구개발비-위탁용역비-운영"},
    {"gb": "지급수수료", "code": "53732501", "a1": "03 경비", "a2": "08 일반운영 경비", "a3": "31 지급수수료", "a4": "50 지급수수료", "name": "지급수수료-일반"},
    {"gb": "생소비", "code": "54380100", "a1": "03 경비", "a2": "07 직접개발비", "a3": "28 경상연구개발비", "a4": "46 생소비", "name": "경상연구개발비-생소비-재료"},
    {"gb": "생소비", "code": "54380200", "a1": "03 경비", "a2": "07 직접개발비", "a3": "28 경상연구개발비", "a4": "46 생소비", "name": "경상연구개발비-생소비-시작품"},
    {"gb": "생소비", "code": "54380300", "a1": "03 경비", "a2": "07 직접개발비", "a3": "28 경상연구개발비", "a4": "46 생소비", "name": "경상연구개발비-생소비-분석"},
    {"gb": "기술료", "code": "54351101", "a1": "04 기술료", "a2": "06 기술료", "a3": "24 기술료", "a4": "40 기술료", "name": "기술사용료-기술료-정액"},
    {"gb": "생소비", "code": "53530300", "a1": "03 경비", "a2": "03 생산성비용", "a3": "09 생산성소모품비", "a4": "18 기타", "name": "생산소모품비-청정용품"},
    {"gb": "조사비", "code": "53790100", "a1": "03 경비", "a2": "08 일반운영 경비", "a3": "32 조사비", "a4": "51 조사비", "name": "조사비-시장조사"},
]

ASSET_CLASSES: list[dict[str, str]] = [
    {"cls": "16010100", "text": "토지", "code": "53450100", "name": "감가상각비-건물"},
    {"cls": "16110100", "text": "기계장치", "code": "53450300", "name": "감가상각비-기계장치"},
    {"cls": "16210100", "text": "공구와기구", "code": "53450400", "name": "감가상각비-공구와기구"},
    {"cls": "16310100", "text": "비품", "code": "53450500", "name": "감가상각비-비품"},
    {"cls": "16412503", "text": "전산기기(OA)", "code": "53450600", "name": "감가상각비-전산기기"},
    {"cls": "16510100", "text": "구축물", "code": "53450200", "name": "감가상각비-구축물"},
    {"cls": "17110100", "text": "무형자산(SW)", "code": "53460100", "name": "무형자산상각비"},
    {"cls": "신규투자", "text": "신규 투자(기본)", "code": "53450300", "name": "감가상각비-기계장치"},
    {"cls": "전산기기(HPC)", "text": "전산기기(HPC)", "code": "53450600", "name": "감가상각비-전산기기"},
]

DEPT_CODES: list[dict[str, str]] = [
    {"code": "C10I3000", "job": "AAA", "name": "SoC설계그룹", "bu": "C100GEAA", "team": "SOC개발실", "t2": "SOC개발실", "t3": "System LSI사업부", "fa": "6000", "fam3": "R&D-SOC", "upd": "2026.01.26", "rep": "Y", "state": "사용"},
    {"code": "C10I3010", "job": "AAA", "name": "SoC설계혁신그룹", "bu": "C100GEAA", "team": "SOC개발실", "t2": "SOC개발실", "t3": "System LSI사업부", "fa": "6000", "fam3": "R&D-SOC", "upd": "2026.01.26", "rep": "N", "state": "폐기"},
    {"code": "C10I3110", "job": "AAA", "name": "SoC검증그룹", "bu": "C100GEAA", "team": "SOC개발실", "t2": "SOC개발실", "t3": "System LSI사업부", "fa": "6000", "fam3": "R&D-SOC", "upd": "2026.01.26", "rep": "N", "state": "사용"},
    {"code": "C10I3120", "job": "AAA", "name": "SoC펌웨어그룹", "bu": "C100GEAA", "team": "SOC개발실", "t2": "SOC개발실", "t3": "System LSI사업부", "fa": "6000", "fam3": "R&D-SOC", "upd": "2026.01.26", "rep": "N", "state": "사용"},
    {"code": "C10I3200", "job": "AAA", "name": "센서개발그룹", "bu": "C100GEAA", "team": "센서사업팀", "t2": "센서사업팀", "t3": "System LSI사업부", "fa": "6000", "fam3": "R&D-SENSOR", "upd": "2026.01.26", "rep": "Y", "state": "사용"},
    {"code": "C10I3210", "job": "AAA", "name": "센서평가그룹", "bu": "C100GEAA", "team": "센서사업팀", "t2": "센서사업팀", "t3": "System LSI사업부", "fa": "4000", "fam3": "R&D-SENSOR", "upd": "2026.01.26", "rep": "N", "state": "사용"},
    {"code": "C10I3215", "job": "AAA", "name": "센서화질그룹", "bu": "C100GEAA", "team": "센서사업팀", "t2": "센서사업팀", "t3": "System LSI사업부", "fa": "6000", "fam3": "R&D-SENSOR", "upd": "2026.01.26", "rep": "N", "state": "폐기"},
    {"code": "C10I3218", "job": "AAA", "name": "SOC마케팅그룹", "bu": "C100GEAA", "team": "SOC개발실", "t2": "SOC개발실", "t3": "System LSI사업부", "fa": "6000", "fam3": "MKT-SOC", "upd": "2026.01.26", "rep": "N", "state": "사용"},
    {"code": "C10I3300", "job": "AAA", "name": "DDI개발그룹", "bu": "C100GEAA", "team": "LSI개발실", "t2": "LSI개발실", "t3": "System LSI사업부", "fa": "4000", "fam3": "R&D-DDI", "upd": "2026.01.26", "rep": "Y", "state": "사용"},
    {"code": "C10I3310", "job": "AAA", "name": "DDI설계그룹", "bu": "C100GEAA", "team": "LSI개발실", "t2": "LSI개발실", "t3": "System LSI사업부", "fa": "4000", "fam3": "R&D-DDI", "upd": "2026.01.26", "rep": "N", "state": "사용"},
    {"code": "C10I3400", "job": "AAA", "name": "테스트엔지니어링그룹", "bu": "C100GEAA", "team": "Test&Package센터", "t2": "Test&Package센터", "t3": "System LSI사업부", "fa": "2000", "fam3": "T&P", "upd": "2026.01.26", "rep": "Y", "state": "사용"},
    {"code": "C10I3405", "job": "AAA", "name": "테스트기술그룹", "bu": "C100GEAA", "team": "Test&Package센터", "t2": "Test&Package센터", "t3": "System LSI사업부", "fa": "2000", "fam3": "T&P", "upd": "2026.01.26", "rep": "N", "state": "폐기"},
    {"code": "C10I3410", "job": "AAA", "name": "품질보증그룹", "bu": "C100GEAA", "team": "Test&Package센터", "t2": "Test&Package센터", "t3": "System LSI사업부", "fa": "2000", "fam3": "T&P", "upd": "2026.01.26", "rep": "N", "state": "사용"},
    {"code": "C10I3500", "job": "AAA", "name": "CP개발그룹", "bu": "C100GEAA", "team": "CP개발실", "t2": "CP개발실", "t3": "System LSI사업부", "fa": "4000", "fam3": "R&D-CP", "upd": "2026.01.26", "rep": "Y", "state": "사용"},
    {"code": "C10I3600", "job": "AAA", "name": "설계인프라그룹", "bu": "C100GEAA", "team": "설계인프라실", "t2": "설계인프라실", "t3": "System LSI사업부", "fa": "1000", "fam3": "INFRA", "upd": "2026.01.26", "rep": "Y", "state": "사용"},
    {"code": "C10I3610", "job": "AAA", "name": "IT인프라그룹", "bu": "C100GEAA", "team": "설계인프라실", "t2": "설계인프라실", "t3": "System LSI사업부", "fa": "2000", "fam3": "INFRA", "upd": "2026.01.26", "rep": "N", "state": "사용"},
    {"code": "C10I3700", "job": "AAA", "name": "AI센터", "bu": "C100GEAA", "team": "AI센터", "t2": "AI센터", "t3": "System LSI사업부", "fa": "4000", "fam3": "AI", "upd": "2026.01.26", "rep": "Y", "state": "사용"},
    {"code": "C10I3952", "job": "AAA", "name": "Talent Experience그룹", "bu": "C100GEAA", "team": "인사팀", "t2": "경영지원실", "t3": "System LSI사업부", "fa": "1000", "fam3": "HR", "upd": "2026.01.26", "rep": "Y", "state": "사용"},
]

PLM_TASKS: list[dict[str, str]] = [
    {"plm": "RGE011712-19CS", "prod": "APPLICATION PROCESSOR", "name": "S5EHR80-D19", "nick": "KITT1 LP5", "st": "Drop", "ps": "2026-08-24", "pe": "2027-09-30", "stg": "PI", "ty": "SOC", "ty2": "Auto SOC", "ss": "2018-01-19", "se": "2026-09-30", "pm": "문주형 (joohyung.mun)", "pl": "권철희 (cheul2.kweon)", "own": "권철희(cheul2.kweon)", "cr": "2026-08-24"},
    {"plm": "S5E9965-A01", "prod": "APPLICATION PROCESSOR", "name": "S5E9965-A01", "nick": "Vanguard", "st": "정상진행", "ps": "2025-09-01", "pe": "2027-06-30", "stg": "ES", "ty": "SOC", "ty2": "Mobile AP", "ss": "2025-09-01", "se": "2026-12-31", "pm": "이정훈 (jhoon.lee)", "pl": "박서연 (sy0412.park)", "own": "박서연(sy0412.park)", "cr": "2025-08-18"},
    {"plm": "S5E9965-P03", "prod": "APPLICATION PROCESSOR", "name": "S5E9965-P03", "nick": "Vanguard", "st": "정상진행", "ps": "2025-11-03", "pe": "2027-06-30", "stg": "ES", "ty": "SOC", "ty2": "Mobile AP", "ss": "2025-11-03", "se": "2026-12-31", "pm": "이정훈 (jhoon.lee)", "pl": "최민석 (ms77.choi)", "own": "최민석(ms77.choi)", "cr": "2025-10-20"},
    {"plm": "S5E9965-T01", "prod": "APPLICATION PROCESSOR", "name": "S5E9965-T01", "nick": "Vanguard", "st": "지연진행", "ps": "2025-10-06", "pe": "2027-03-31", "stg": "PI", "ty": "SOC", "ty2": "Mobile AP", "ss": "2025-10-06", "se": "2026-11-30", "pm": "이정훈 (jhoon.lee)", "pl": "정우진 (woojin.jung)", "own": "정우진(woojin.jung)", "cr": "2025-09-22"},
    {"plm": "S5E9965-F02", "prod": "APPLICATION PROCESSOR", "name": "S5E9965-F02", "nick": "Vanguard", "st": "정상완료", "ps": "2025-03-03", "pe": "2026-06-30", "stg": "CS", "ty": "SOC", "ty2": "Mobile AP", "ss": "2025-03-03", "se": "2026-06-30", "pm": "이정훈 (jhoon.lee)", "pl": "한지수 (jisu.han)", "own": "한지수(jisu.han)", "cr": "2025-02-17"},
    {"plm": "S5E8365-B01", "prod": "APPLICATION PROCESSOR", "name": "S5E8365-B01", "nick": "Telos", "st": "Hold", "ps": "2026-01-05", "pe": "2027-12-31", "stg": "PI", "ty": "SOC", "ty2": "Wearable", "ss": "2026-01-05", "se": "2026-10-31", "pm": "강태호 (taeho.kang)", "pl": "윤하늘 (sky.yoon)", "own": "윤하늘(sky.yoon)", "cr": "2025-12-15"},
    {"plm": "S5K3J1-R02", "prod": "IMAGE SENSOR", "name": "S5K3J1-R02", "nick": "Glacier", "st": "Drop", "ps": "2025-11-03", "pe": "2026-12-18", "stg": "ES", "ty": "SENSOR", "ty2": "Mobile CIS", "ss": "2025-11-03", "se": "2026-08-31", "pm": "김도윤 (doyun.kim)", "pl": "오세라 (sera.oh)", "own": "오세라(sera.oh)", "cr": "2025-10-20"},
    {"plm": "S5K3J1-C01", "prod": "IMAGE SENSOR", "name": "S5K3J1-C01", "nick": "Glacier", "st": "정상진행", "ps": "2025-07-01", "pe": "2027-03-31", "stg": "ES", "ty": "SENSOR", "ty2": "Mobile CIS", "ss": "2025-07-01", "se": "2026-12-31", "pm": "김도윤 (doyun.kim)", "pl": "임재현 (jh.lim)", "own": "임재현(jh.lim)", "cr": "2025-06-16"},
    {"plm": "S6E3HA-L00", "prod": "DISPLAY DRIVER IC", "name": "S6E3HA-L00", "nick": "Aurora", "st": "Drop", "ps": "2025-06-02", "pe": "2026-06-30", "stg": "CS", "ty": "LSI", "ty2": "DDI", "ss": "2025-06-02", "se": "2026-03-31", "pm": "서준영 (jy.seo)", "pl": "배유나 (yuna.bae)", "own": "배유나(yuna.bae)", "cr": "2025-05-15"},
    {"plm": "S6E3HA-L01", "prod": "DISPLAY DRIVER IC", "name": "S6E3HA-L01", "nick": "Aurora", "st": "지연완료", "ps": "2025-04-01", "pe": "2026-09-30", "stg": "CS", "ty": "LSI", "ty2": "DDI", "ss": "2025-04-01", "se": "2026-10-31", "pm": "서준영 (jy.seo)", "pl": "신동혁 (dh.shin)", "own": "신동혁(dh.shin)", "cr": "2025-03-17"},
]

# 전표 계정 자동 분류 기준정보(close_dept_rules) — (부서코드 × 비용계정) → 19개 계정 중 하나.
# 비어 있으면 적재한 전표가 **전부 미분류**로 떨어져서, 1단계 계정별 요약과 챗봇은 "이 달 결산은
# 아무 계정에도 안 붙었다"로 보인다(화면은 자기 코드 표로 따로 분류하니 화면만 정상으로 보인다 —
# 그 어긋남이 이 표가 비어 있다는 걸 가장 늦게 알게 만든다).
#
# cost_center를 빈 문자열로 둔 행은 "그 비용계정 전체"를 뜻한다. 해외연구소는 같은 ERP 계정이
# 국내 부서에서도 나오기 때문에 부서코드까지 봐야 하고, 그 행은 연구소 부서코드가 확정된 뒤에
# 넣는다(지금 추측으로 넣으면 국내 비용이 해외연구소로 붙는다).
# (account_code, account_key, note)
CLOSE_DEPT_RULES: list[tuple[str, str, str]] = [
    ("53790100", "market", "조사비-시장조사"),
    ("53810100", "cae", "CAE 사용료"),
    ("53720100", "outsource", "위탁용역비(화면 예시 코드)"),
    ("54370700", "outsource", "경상연구개발비-위탁용역비-국내"),
    ("54370800", "outsource", "경상연구개발비-위탁용역비-해외"),
    ("54370900", "outsource", "경상연구개발비-위탁용역비-운영"),
    ("53732501", "outsource", "지급수수료-일반(위탁용역비 화면의 지급수수료 탭)"),
    ("53730100", "consum", "생소비(화면 예시 코드)"),
    ("54380100", "consum", "경상연구개발비-생소비-재료"),
    ("54380200", "consum", "경상연구개발비-생소비-시작품"),
    ("54380300", "consum", "경상연구개발비-생소비-분석"),
    ("53530300", "consum", "생산소모품비-청정용품"),
    ("53850100", "supplies", "소모품비"),
    ("53100100", "dep", "감가상각비(화면 예시 코드)"),
    ("53450100", "dep", "감가상각비-건물"),
    ("53450200", "dep", "감가상각비-구축물"),
    ("53450300", "dep", "감가상각비-기계장치"),
    ("53450400", "dep", "감가상각비-공구와기구"),
    ("53450500", "dep", "감가상각비-비품"),
    ("53450600", "dep", "감가상각비-전산기기"),
    ("53460100", "dep", "무형자산상각비"),
    ("54351101", "fixtech", "기술사용료-기술료-정액"),
]


# 제품정보(SC_FMMS) — 화면 pmSeed()와 같은 내용. 제품 → 제품군(fam1~fam4) 매핑을 여러 계정이 쓴다.
PRODUCTS: list[dict[str, str]] = [
    {"device": "SA59955", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "SA59955AW1-L030", "gr_device": "SA59955", "fam1_code": "SOC", "fam2_code": "MOBILE AP", "fam3_code": "MAP_FLAG", "fam4_code": "MAP_FLAG_955"},
    {"device": "SC59965", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "SC59965AWB-J130", "gr_device": "SC59965", "fam1_code": "SOC", "fam2_code": "MOBILE AP", "fam3_code": "MAP_FLAG", "fam4_code": "MAP_FLAG_965"},
    {"device": "SA59975", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "SA59975AW1-L030", "gr_device": "SA59975", "fam1_code": "SOC", "fam2_code": "MOBILE AP", "fam3_code": "MAP_FLAG", "fam4_code": "MAP_FLAG_975"},
    {"device": "S5E9945", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S5E9945XA1-L010", "gr_device": "S5E9945", "fam1_code": "SOC", "fam2_code": "MOBILE AP", "fam3_code": "MAP_PRM", "fam4_code": "MAP_PRM_945"},
    {"device": "S5E8855", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S5E8855XB1-L020", "gr_device": "S5E8855", "fam1_code": "SOC", "fam2_code": "MOBILE AP", "fam3_code": "MAP_MID", "fam4_code": "MAP_MID_855"},
    {"device": "S5E8845", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S5E8845XB0-L020", "gr_device": "S5E8845", "fam1_code": "SOC", "fam2_code": "MOBILE AP", "fam3_code": "MAP_MID", "fam4_code": "MAP_MID_845"},
    {"device": "S5E8865", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S5E8865XA0-L010", "gr_device": "S5E8865", "fam1_code": "SOC", "fam2_code": "MOBILE AP", "fam3_code": "MAP_MID", "fam4_code": "MAP_MID_865"},
    {"device": "S5E8735", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S5E8735XA0-A010", "gr_device": "S5E8735", "fam1_code": "SOC", "fam2_code": "AUTO", "fam3_code": "AUTO_IVI", "fam4_code": "AUTO_IVI_735"},
    {"device": "S5A0910", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S5A0910XA0-A020", "gr_device": "S5A0910", "fam1_code": "SOC", "fam2_code": "AUTO", "fam3_code": "AUTO_ISP", "fam4_code": "AUTO_ISP_910"},
    {"device": "S5E5515", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S5E5515XA1-W010", "gr_device": "S5E5515", "fam1_code": "SOC", "fam2_code": "WEARABLE", "fam3_code": "WEAR", "fam4_code": "WEAR_515"},
    {"device": "S5KHP3S", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S5KHP3SX01-FGX1", "gr_device": "S5KHP3S", "fam1_code": "SENSOR", "fam2_code": "SENSOR", "fam3_code": "SEN_HI", "fam4_code": "SEN_HI_P3S"},
    {"device": "S5KGN5S", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S5KGN5SX02-FGX2", "gr_device": "S5KGN5S", "fam1_code": "SENSOR", "fam2_code": "SENSOR", "fam3_code": "SEN_MID", "fam4_code": "SEN_MID_N5S"},
    {"device": "S5K3J1S", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S5K3J1SX03-FGX9", "gr_device": "S5K3J1S", "fam1_code": "SENSOR", "fam2_code": "SENSOR", "fam3_code": "SEN_LOW", "fam4_code": "SEN_LOW_J1S"},
    {"device": "S5KHM2S", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S5KHM2SX01-FGX3", "gr_device": "S5KHM2S", "fam1_code": "SENSOR", "fam2_code": "SENSOR", "fam3_code": "SEN_MID", "fam4_code": "SEN_MID_M2S"},
    {"device": "S5KJN1S", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S5KJN1SX01-FGX4", "gr_device": "S5KJN1S", "fam1_code": "SENSOR", "fam2_code": "SENSOR", "fam3_code": "SEN_MID", "fam4_code": "SEN_MID_N1S"},
    {"device": "S6HD312", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S6HD312XA0-D010", "gr_device": "S6HD312", "fam1_code": "LSI", "fam2_code": "DTV SOC", "fam3_code": "DTV", "fam4_code": "DTV_312"},
    {"device": "S6HD630", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S6HD630XA0-D020", "gr_device": "S6HD630", "fam1_code": "LSI", "fam2_code": "DTV SOC", "fam3_code": "DTV", "fam4_code": "DTV_630"},
    {"device": "S6HD640", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S6HD640XA0-D030", "gr_device": "S6HD640", "fam1_code": "LSI", "fam2_code": "DTV SOC", "fam3_code": "DTV", "fam4_code": "DTV_640"},
    {"device": "S6HD820", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S6HD820XA0-D040", "gr_device": "S6HD820", "fam1_code": "LSI", "fam2_code": "DTV SOC", "fam3_code": "DTV", "fam4_code": "DTV_820"},
    {"device": "S6E3HAF", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S6E3HAFX01-N010", "gr_device": "S6E3HAF", "fam1_code": "LSI", "fam2_code": "DDI", "fam3_code": "DDI", "fam4_code": "DDI_HAF"},
    {"device": "S6E3FC5", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S6E3FC5X01-N020", "gr_device": "S6E3FC5", "fam1_code": "LSI", "fam2_code": "DDI", "fam3_code": "DDI", "fam4_code": "DDI_FC5"},
    {"device": "S3K250A", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S3K250AX01-S010", "gr_device": "S3K250A", "fam1_code": "LSI", "fam2_code": "SECURITY", "fam3_code": "SEC", "fam4_code": "SEC_50A"},
    {"device": "S3SSE2A", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S3SSE2AX00-S020", "gr_device": "S3SSE2A", "fam1_code": "LSI", "fam2_code": "SECURITY", "fam3_code": "SEC", "fam4_code": "SEC_E2A"},
    {"device": "S2MPS29", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S2MPS29XA0-P010", "gr_device": "S2MPS29", "fam1_code": "LSI", "fam2_code": "PMIC", "fam3_code": "PMIC", "fam4_code": "PMIC_S29"},
    {"device": "S2MPR01", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S2MPR01XA0-P020", "gr_device": "S2MPR01", "fam1_code": "LSI", "fam2_code": "PMIC", "fam3_code": "PMIC", "fam4_code": "PMIC_R01"},
    {"device": "S2FHD01", "yyyy": "2026", "sel_code": "M2609A", "section": "FABLSS", "sale_device": "S2FHD01XA0-P030", "gr_device": "S2FHD01", "fam1_code": "LSI", "fam2_code": "PMIC", "fam3_code": "PMIC", "fam4_code": "PMIC_D01"},
]


def seed_reference(db: Session) -> dict[str, int]:
    """네 표를 심는다. 표마다 따로 본다 — 한 표만 비어 있는 DB(마이그레이션만 돌린 상태에서
    부서코드만 업로드한 경우 등)에서도 나머지가 채워져야 화면이 뜬다."""
    made: dict[str, int] = {}

    if not db.query(RefCostElement).filter(RefCostElement.year == BASIS_YEAR).count():
        db.add_all(
            RefCostElement(year=BASIS_YEAR, sort_order=i, **r) for i, r in enumerate(COST_ELEMENTS)
        )
        made["cost_elements"] = len(COST_ELEMENTS)

    if not db.query(RefAssetClass).count():
        db.add_all(
            RefAssetClass(asset_class=r["cls"], class_text=r["text"], code=r["code"], name=r["name"])
            for r in ASSET_CLASSES
        )
        made["asset_classes"] = len(ASSET_CLASSES)

    ver = dict(basis_year=BASIS_YEAR, version_kind=BASIS_KIND, version_seq=BASIS_SEQ)
    if not db.query(RefDeptCode).filter_by(**ver).count():
        db.add_all(
            RefDeptCode(
                **ver, code=r["code"], job_category=r["job"], name=r["name"], budget_unit=r["bu"],
                team_s=r["team"], team_m=r["t2"], team_l=r["t3"], functional_area=r["fa"],
                fam3=r["fam3"], updated_on=r["upd"], is_rep=r["rep"].upper()[:1] == "Y",
                state=r["state"] or "사용",
            )
            for r in DEPT_CODES
        )
        made["dept_codes"] = len(DEPT_CODES)

    if not db.query(RefPlmTask).filter_by(**ver).count():
        db.add_all(
            RefPlmTask(
                **ver, task_code=r["plm"], product=r["prod"], name=r["name"], nickname=r["nick"],
                status=r["st"], plan_start=r["ps"], plan_end=r["pe"], stage=r["stg"],
                target_type=r["ty"], target_subtype=r["ty2"], stage_start=r["ss"], stage_end=r["se"],
                project_manager=r["pm"], project_leader=r["pl"], owner=r["own"], created_on=r["cr"],
            )
            for r in PLM_TASKS
        )
        made["plm_tasks"] = len(PLM_TASKS)

    if not db.query(RefProduct).filter_by(**ver).count():
        db.add_all(RefProduct(**ver, **r) for r in PRODUCTS)
        made["products"] = len(PRODUCTS)

    if not db.query(CloseDeptRule).count():
        db.add_all(
            CloseDeptRule(cost_center="", account_code=code, account_key=key, note=note)
            for code, key, note in CLOSE_DEPT_RULES
        )
        made["close_dept_rules"] = len(CLOSE_DEPT_RULES)

    # 기준 버전의 계정표 스냅샷도 같이 박아 둔다. 조회 시점에 자동으로 만들어지긴 하지만(첫
    # GET이 만든다), 시드 직후 DB를 들여다보는 사람에게 "이 버전은 어느 계정표를 보는가"가
    # 보여야 규칙을 코드에서 다시 읽지 않아도 된다.
    if not db.query(RefCostElementSnapshot).filter_by(**ver).count() and made.get("cost_elements"):
        db.flush()
        db.add_all(
            RefCostElementSnapshot(**ver, source_year=BASIS_YEAR, sort_order=i, **r)
            for i, r in enumerate(COST_ELEMENTS)
        )
        made["cost_element_snapshots"] = len(COST_ELEMENTS)

    db.commit()
    return made
