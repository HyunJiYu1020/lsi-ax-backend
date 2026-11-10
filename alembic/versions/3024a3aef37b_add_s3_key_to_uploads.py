"""add s3_key to uploads

Revision ID: 3024a3aef37b
Revises: af4ff1877998
Create Date: 2026-11-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '3024a3aef37b'
down_revision: Union[str, Sequence[str], None] = 'af4ff1877998'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'uploads',
        sa.Column(
            's3_key', sa.String(length=512), nullable=True,
            comment='원본 파일이 보관된 S3 객체 키. S3 미설정이거나 업로드 실패 시 NULL(파일명·파싱 결과만 남음)',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('uploads', 's3_key')
