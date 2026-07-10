"""Enqueue every canonical domain event in the transactional outbox."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260710_0004"
down_revision: str | Sequence[str] | None = "20260710_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text(_CREATE_OUTBOX_TRIGGER))


def downgrade() -> None:
    op.execute(sa.text(_DROP_OUTBOX_TRIGGER))


_CREATE_OUTBOX_TRIGGER = r"""
CREATE FUNCTION fairy_enqueue_domain_event()
RETURNS trigger
LANGUAGE plpgsql
AS $fairy$
BEGIN
    INSERT INTO outbox (tenant_id, event_id, topic, payload)
    VALUES (
        NEW.tenant_id,
        NEW.event_id,
        'domain.events',
        json_build_object(
            'tenant_id', NEW.tenant_id,
            'cursor', NEW.cursor,
            'event_id', NEW.event_id,
            'run_id', NEW.run_id,
            'user_id', NEW.user_id,
            'device_id', NEW.device_id,
            'project_id', NEW.project_id,
            'conversation_id', NEW.conversation_id,
            'task_id', NEW.task_id,
            'version_id', NEW.version_id,
            'task_sequence', NEW.task_sequence,
            'schema_version', NEW.schema_version,
            'event_type', NEW.event_type,
            'visibility', NEW.visibility,
            'message', NEW.message,
            'payload', NEW.payload,
            'created_at', to_char(
                NEW.created_at AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
            )
        )
    )
    ON CONFLICT (tenant_id, event_id) DO NOTHING;
    RETURN NEW;
END;
$fairy$;

CREATE TRIGGER trg_domain_event_outbox
AFTER INSERT ON domain_events
FOR EACH ROW
EXECUTE FUNCTION fairy_enqueue_domain_event();
"""

_DROP_OUTBOX_TRIGGER = """
DROP TRIGGER IF EXISTS trg_domain_event_outbox ON domain_events;
DROP FUNCTION IF EXISTS fairy_enqueue_domain_event();
"""
