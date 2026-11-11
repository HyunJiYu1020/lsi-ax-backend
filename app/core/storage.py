"""업로드 원본 파일(엑셀) 보존.

보관 위치는 설정에 따라 둘 중 하나이고, 호출부는 어느 쪽인지 몰라도 된다 — 둘 다 "키"를 돌려주고
그 키를 uploads.s3_key에 그대로 넣으면 된다(나중에 버킷이 생겨도 호출부는 손댈 필요가 없다):
- settings.s3_upload_bucket이 있으면  → S3
- 없으면                              → settings.upload_local_dir 아래 로컬 파일

원본은 "틀어진 값을 되돌릴 유일한 근거"라서 버킷이 준비되기 전에도 반드시 남긴다. 다만 보관이
실패해도 예외를 올리지 않고 None을 돌려준다 — 파싱 결과 저장(메인 흐름)이 이것 때문에 막히면
안 되기 때문이다. 호출부는 None을 "원본 미보존"으로 받아들이고 파일명만 남긴다.
"""

import logging
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings

logger = logging.getLogger(__name__)

_client = None


def s3_enabled() -> bool:
    return bool(settings.s3_upload_bucket and settings.aws_access_key_id and settings.aws_secret_access_key)


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
    return _client


def _local_root() -> Path:
    return Path(settings.upload_local_dir)


def _local_path(key: str) -> Path:
    """키를 로컬 경로로 바꾼다. 키는 서버가 만들지만, 혹시라도 ".."나 절대경로가 섞여 보관 폴더
    밖으로 쓰는 일이 없도록 경로를 정규화한 뒤 반드시 폴더 안쪽인지 확인한다."""
    root = _local_root().resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"보관 폴더 밖을 가리키는 키입니다: {key!r}")
    return path


def upload_bytes(data: bytes, key: str, content_type: str | None = None) -> str | None:
    """성공하면 보관 키를 돌려주고, 실패하면 None을 돌려준다."""
    if s3_enabled():
        extra = {"ContentType": content_type} if content_type else {}
        try:
            _get_client().put_object(Bucket=settings.s3_upload_bucket, Key=key, Body=data, **extra)
            return key
        except (BotoCoreError, ClientError) as e:
            logger.warning("S3 업로드 실패 (key=%s): %s", key, e)
            return None
    try:
        path = _local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key
    except (OSError, ValueError) as e:
        logger.warning("원본 로컬 저장 실패 (key=%s): %s", key, e)
        return None


def download_bytes(key: str) -> bytes | None:
    """보관해 둔 원본을 돌려준다(없으면 None). 업로드 당시 파일을 다시 읽어야 할 때 쓴다 —
    값이 틀어졌을 때 되돌리거나, 양식이 바뀐 뒤 과거 파일을 그때 양식으로 다시 해석할 때."""
    if not key:
        return None
    if s3_enabled():
        try:
            return _get_client().get_object(Bucket=settings.s3_upload_bucket, Key=key)["Body"].read()
        except (BotoCoreError, ClientError) as e:
            logger.warning("S3 다운로드 실패 (key=%s): %s", key, e)
            return None
    try:
        path = _local_path(key)
        return path.read_bytes() if path.exists() else None
    except (OSError, ValueError) as e:
        logger.warning("원본 로컬 읽기 실패 (key=%s): %s", key, e)
        return None
