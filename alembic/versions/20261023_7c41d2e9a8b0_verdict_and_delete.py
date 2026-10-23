"""tender verdict and soft delete

Revision ID: 7c41d2e9a8b0
Revises: 5fb1dc1ada4b
Create Date: 2026-10-23 09:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c41d2e9a8b0'
down_revision: Union[str, Sequence[str], None] = '5fb1dc1ada4b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('tenders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('verdict', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('verdict_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f('ix_tenders_verdict'), ['verdict'], unique=False)
        batch_op.create_index(batch_op.f('ix_tenders_deleted_at'), ['deleted_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('tenders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tenders_deleted_at'))
        batch_op.drop_index(batch_op.f('ix_tenders_verdict'))
        batch_op.drop_column('deleted_at')
        batch_op.drop_column('verdict_at')
        batch_op.drop_column('verdict')
