# 테스트 구조

`app/`과 같은 기준으로 가른다 — 경로가 "공통인가 한 계정 전용인가"를 말해 준다.

```
tests/
├── conftest.py      db 픽스처(실제 로컬 Postgres에 SAVEPOINT로 붙어 끝나면 롤백).
│                    루트에 두면 하위 폴더 전부에 자동 적용된다.
├── shared/          전 계정 공용 코드의 테스트
└── overseas/        해외연구소 계정 테스트 (+ js_formula_harness.mjs)
```

## 어느 폴더에 둘지

**무엇을 import하는가가 아니라 무엇을 고정하는가**로 가른다.
예: `overseas/test_close_voucher_match.py`는 공용 모듈(`app/shared/core/voucher_match.py`)을
import하지만, 고정하는 건 "전표를 어느 **연구소**에 붙이나"라는 해외연구소 규칙이라 overseas에 둔다.
다른 계정이 같은 모듈을 쓰게 되면 그 계정 폴더에 자기 테스트를 추가한다.

계정 폴더 안에서는 `overseas_` 접두사를 붙이지 않는다 — 폴더가 이미 계정을 말한다.

## 실행

```bash
pytest                  # 전체
pytest tests/overseas   # 해외연구소만
pytest tests/shared     # 공통만
```

로컬 Postgres가 안 떠 있으면 db 픽스처를 쓰는 테스트는 스킵된다(conftest.py).
`overseas/test_formula_crosscheck.py`는 node와 프런트 소스(`frontend-html/`, `frontend/`)가
없으면 스킵된다 — 세 구현(core.js · calc.js · 백엔드)을 실제로 실행해서 대조하기 때문이다.
