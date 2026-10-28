"""common_codes를 코드에서 안전하게 참조하기 위한 Enum.
DB(common_code_groups/common_codes)가 실제 데이터이고, 이 Enum은 애플리케이션 코드(쿼리 필터,
검증 로직 등)에서 문자열 오타 없이 그 값을 쓰기 위한 것 — 둘이 어긋나면 안 되니 같이 수정한다."""

from enum import Enum


class CodeGroup(str, Enum):
    """common_code_groups.group_code와 1:1로 맞아야 하는 그룹 목록."""

    ACCOUNT_STATUS = "ACCOUNT_STATUS"


class AccountStatus(str, Enum):
    """common_codes(group_code=ACCOUNT_STATUS)의 code 값. accounts.status에 저장된다."""

    DRAFTING = "drafting"
    DRAFTED = "drafted"
    NOT_SUBMITTED = "not_submitted"
    SUBMITTED = "submitted"
    REVIEWED = "reviewed"
    REJECTED = "rejected"
