"""도메인 로직 — DB는 알지만 HTTP는 모른다.

의존 방향: endpoints → services → core/models (한 방향). services가 endpoints를 import하면
안 된다 — 그게 PLAN.md P1-1이 말하는 그물을 다시 만드는 일이다.
"""
