"""status and notes change times

Revision ID: b2d4e6f8a1c3
Revises: 9a1f3c5e7d20
Create Date: 2026-10-23 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2d4e6f8a1c3'
down_revision: Union[str, Sequence[str], None] = '9a1f3c5e7d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('tenders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('user_status_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('notes_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('tenders', schema=None) as batch_op:
        batch_op.drop_column('notes_at')
        batch_op.drop_column('user_status_at')
