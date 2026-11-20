"""Alembic이 Base.metadata를 채우기 위해 모든 모델 모듈을 한 번 import하는 자리.

모델이 공통(app/shared/models/)과 계정별(app/accounts/<계정>/models.py)로 나뉘어 있어서,
autogenerate가 테이블을 다 인식하려면 양쪽을 모두 불러와야 한다. 계정을 추가하면 그 계정의
models를 여기 한 줄 더한다 — 빠뜨리면 alembic이 그 테이블을 "삭제됨"으로 보고 drop을 만든다.
"""

from app.accounts.overseas import models as _overseas  # noqa: F401
from app.shared.models import account as _account  # noqa: F401
from app.shared.models import close as _close  # noqa: F401
from app.shared.models import common as _common  # noqa: F401
from app.shared.models import fx as _fx  # noqa: F401
from app.shared.models import generic as _generic  # noqa: F401
from app.shared.models import plan as _plan  # noqa: F401
from app.shared.models import system as _system  # noqa: F401
from app.shared.models import user as _user  # noqa: F401
from app.shared.models import version as _version  # noqa: F401
