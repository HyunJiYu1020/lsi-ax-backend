"""업로드 원본 파일(엑셀) 보존용 S3 연동. settings.s3_upload_bucket이 비어 있으면(버킷
준비 전 기본 상태) 아무 것도 하지 않고 None을 돌려준다 — 호출부는 이 None을 "원본 미보존"으로
받아들이고 파일명·파싱 결과 저장은 그대로 계속한다. 자격증명이 있는데 업로드가 실패하는 경우도
예외를 올리지 않고 None을 돌려준다 — 원본 보존은 부가기능이라 이것 때문에 메인 저장이 막히면 안 된다."""

import logging

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


def upload_bytes(data: bytes, key: str, content_type: str | None = None) -> str | None:
    """성공하면 저장한 S3 키를 돌려주고, 미설정이거나 실패하면 None을 돌려준다(호출부는 이 값을
    uploads.s3_key에 그대로 넣으면 됨 — None이면 원본 없이 파일명만 남는다)."""
    if not s3_enabled():
        return None
    extra = {"ContentType": content_type} if content_type else {}
    try:
        _get_client().put_object(Bucket=settings.s3_upload_bucket, Key=key, Body=data, **extra)
        return key
    except (BotoCoreError, ClientError) as e:
        logger.warning("S3 업로드 실패 (key=%s): %s", key, e)
        return None
