"""Apply the existing tenant isolation policy to message submission receipts.

Revision ID: 20260909_0057
Revises: 20260909_0056
"""
import sqlalchemy as sa
from alembic import op

revision = "20260909_0057"
down_revision = "20260909_0056"
branch_labels = None
depends_on = None

_TABLE = "core_assistant_message_submissions"
_POLICY = f"tenant_isolation_{_TABLE}"


def upgrade() -> None:
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
    op.execute(sa.text(f'ALTER TABLE "{_TABLE}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{_TABLE}" FORCE ROW LEVEL SECURITY'))
    op.execute(sa.text(
        f'CREATE POLICY "{_POLICY}" ON "{_TABLE}" '
        f"USING ({predicate}) WITH CHECK ({predicate})"
    ))


def downgrade() -> None:
    op.execute(sa.text(f'DROP POLICY "{_POLICY}" ON "{_TABLE}"'))
    op.execute(sa.text(f'ALTER TABLE "{_TABLE}" NO FORCE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{_TABLE}" DISABLE ROW LEVEL SECURITY'))
