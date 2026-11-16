"""전 계정 공용 테이블 모델.

하위 모듈을 여기서 모두 import한다 — SQLAlchemy는 ForeignKey를 문자열("users.id")로 받고
매퍼를 설정할 때 그 테이블이 Base.metadata에 등록돼 있어야 해결할 수 있다. 한 모듈만 import하면
그 모듈이 가리키는 다른 테이블(users 등)이 등록되지 않아 NoReferencedTableError가 난다
(실제로 폴더를 가르고 처음 테스트를 돌렸을 때 그렇게 났다).
"""

from app.shared.models import account as _account  # noqa: F401
from app.shared.models import close as _close  # noqa: F401
from app.shared.models import common as _common  # noqa: F401
from app.shared.models import fx as _fx  # noqa: F401
from app.shared.models import generic as _generic  # noqa: F401
from app.shared.models import plan as _plan  # noqa: F401
from app.shared.models import system as _system  # noqa: F401
from app.shared.models import user as _user  # noqa: F401
from app.shared.models import version as _version  # noqa: F401
