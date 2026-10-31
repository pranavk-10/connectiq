import hashlib

from sqlalchemy import select, text

from connectiq.auth import bootstrap_owner
from connectiq.csv_io import import_companies, read_csv
from connectiq.db import migrate, transaction
from connectiq.models import User
from connectiq.services import get_setting, set_setting
from connectiq.validation import ValidationError


def initialize(engine, config):
    migrate(engine)
    with transaction(engine) as session:
        if engine.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(706614322)"))
        owner_id = bootstrap_owner(session, config)
        if owner_id is None:
            return {"status": "authentication_required"}
        # Serialize first-run seeding; the marker prevents deleted companies returning at restart.
        session.scalar(select(User).where(User.id == owner_id).with_for_update())
        previous = get_setting(session, owner_id, "initial_company_import")
        if previous:
            return previous
        if not config.company_csv.exists():
            return {"status": "missing_csv", "message": "Place the original 66-company CSV at data/companies.csv, then restart."}
        content = config.company_csv.read_bytes()
        rows = read_csv(content, ["Company Name"])
        if len(rows) != 66:
            raise ValidationError(f"The initial company CSV must contain 66 records; found {len(rows)}. Source was not modified.")
        summary = import_companies(session, content).as_dict()
        if summary["invalid"]:
            raise ValidationError(f"Initial company CSV contains invalid rows: {summary['invalid']}")
        summary.update(status="imported", sha256=hashlib.sha256(content).hexdigest())
        set_setting(session, owner_id, "initial_company_import", summary)
        return summary
