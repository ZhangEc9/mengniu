"""add qc status

Revision ID: 2667d594ebe6
Revises: 149862fa154b
Create Date: 2026-09-22 13:06:57.732587

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2667d594ebe6'
down_revision: Union[str, Sequence[str], None] = '149862fa154b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "qc_result",
        sa.Column("qc_status", sa.String(length=16), nullable=False, server_default="SKIPPED"),
    )
    op.execute(
        "UPDATE qc_result SET qc_status = CASE "
        "WHEN can_proceed_to_price THEN 'PASSED' ELSE 'BLOCKED' END"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("qc_result", "qc_status")
