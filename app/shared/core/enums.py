"""common_codes를 코드에서 안전하게 참조하기 위한 Enum.
DB(common_code_groups/common_codes)가 실제 데이터이고, 이 Enum은 애플리케이션 코드(쿼리 필터,
검증 로직 등)에서 문자열 오타 없이 그 값을 쓰기 위한 것 — 둘이 어긋나면 안 되니 같이 수정한다."""

from enum import Enum


class CodeGroup(str, Enum):
    """common_code_groups.group_code와 1:1로 맞아야 하는 그룹 목록."""

    ACCOUNT_STATUS = "ACCOUNT_STATUS"
    OVERSEAS_SHEET_TYPE = "OVERSEAS_SHEET_TYPE"
    OVERSEAS_CATEGORY = "OVERSEAS_CATEGORY"
    VERSION_STATUS = "VERSION_STATUS"
    OVERSEAS_FX_RULE = "OVERSEAS_FX_RULE"


class AccountStatus(str, Enum):
    """common_codes(group_code=ACCOUNT_STATUS)의 code 값. accounts.status에 저장된다."""

    DRAFTING = "drafting"
    DRAFTED = "drafted"
    NOT_SUBMITTED = "not_submitted"
    SUBMITTED = "submitted"
    REVIEWED = "reviewed"
    REJECTED = "rejected"


class OverseasSheetType(str, Enum):
    """common_codes(group_code=OVERSEAS_SHEET_TYPE)의 code 값. overseas_lab_sheets.sheet_key에 저장된다.
    연구소 워크북 1개 안의 시트 구성(연구소마다 다를 수 있음)을 나타낸다."""

    COST = "cost"
    VARIANCE = "variance"
    VENDOR = "vendor"


class VersionStatus(str, Enum):
    """common_codes(group_code=VERSION_STATUS)의 code 값. versions.status에 저장된다.
    not_started는 실제로는 저장되지 않는다 — 그 연월 버전이 아직 열리지 않았으면 행 자체가 없는
    것으로 "예정"을 나타내기 때문(versions 테이블 코멘트 참고). 그래도 화면에 보일 라벨이 필요해
    공통코드에는 셀렉트 옵션용으로 남겨둔다."""

    NOT_STARTED = "not_started"
    DRAFTING = "drafting"
    DONE = "done"
