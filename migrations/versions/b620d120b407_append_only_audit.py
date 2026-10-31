"""Enforce the append-only audit log at the database boundary."""
from alembic import op

revision = "b620d120b407"
down_revision = "a7195b0d964a"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "sqlite":
        op.execute("CREATE TRIGGER activity_events_no_update BEFORE UPDATE ON activity_events BEGIN SELECT RAISE(ABORT, 'Activity events are append-only'); END")
        op.execute("CREATE TRIGGER activity_events_no_delete BEFORE DELETE ON activity_events BEGIN SELECT RAISE(ABORT, 'Activity events are append-only'); END")
    elif op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION connectiq_reject_event_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Activity events are append-only'; END; $$""")
        op.execute("CREATE TRIGGER activity_events_immutable BEFORE UPDATE OR DELETE ON activity_events FOR EACH ROW EXECUTE FUNCTION connectiq_reject_event_mutation()")


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS activity_events_no_update")
        op.execute("DROP TRIGGER IF EXISTS activity_events_no_delete")
    elif op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS activity_events_immutable ON activity_events")
        op.execute("DROP FUNCTION IF EXISTS connectiq_reject_event_mutation()")
