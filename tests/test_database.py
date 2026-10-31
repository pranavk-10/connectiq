import hashlib

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from connectiq.bootstrap import initialize
from connectiq.config import ROOT
from connectiq.csv_io import import_companies
from connectiq.db import healthy, migrate, transaction
from connectiq.models import Company


def test_initialization_and_migrations(engine):
    assert healthy(engine)
    assert set(inspect(engine).get_table_names()) >= {"users", "companies", "prospects", "activity_events", "follow_up_reminders", "search_history", "user_settings", "alembic_version"}
    migrate(engine)
    assert len(inspect(engine).get_indexes("prospects")) >= 5


def test_original_csv_exact_and_66(session):
    original = (ROOT / "india_rtl_asic_companies.csv").read_bytes()
    canonical = (ROOT / "data/companies.csv").read_bytes()
    assert original == canonical
    assert hashlib.sha256(canonical).hexdigest() == "e112ab8939225aa78d1bf62c6e749cc9d3e29afa0765f6de38173ccfa9087503"
    result = import_companies(session, canonical)
    assert (result.total, result.imported, result.duplicates, result.invalid) == (66, 66, [], [])
    import csv
    source_names = [r["Company Name"] for r in csv.DictReader(canonical.decode("utf-8-sig").splitlines())]
    assert set(session.scalars(select(Company.name))) == set(source_names)
    again = import_companies(session, canonical)
    assert again.imported == 0 and len(again.duplicates) == 66
    assert session.scalar(select(func.count()).select_from(Company)) == 66


def test_restart_preserves_deletions_and_custom_companies(engine, config):
    first = initialize(engine, config)
    assert first["imported"] == 66
    with transaction(engine) as s:
        s.delete(s.scalar(select(Company).where(Company.name == "NVIDIA")))
        s.add(Company(name="Additional test company"))
    second = initialize(engine, config)
    assert second == first
    with transaction(engine) as s:
        assert s.scalar(select(Company).where(Company.name == "NVIDIA")) is None
        assert s.scalar(select(func.count()).select_from(Company)) == 66


def test_atomic_import_rollback(engine):
    with pytest.raises(RuntimeError):
        with transaction(engine) as s:
            import_companies(s, b"Company Name\nTransactional test\n")
            raise RuntimeError("rollback")
    with transaction(engine) as s:
        assert s.scalar(select(func.count()).select_from(Company)) == 0


def test_foreign_keys_enabled(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text("INSERT INTO follow_up_reminders (user_id,prospect_id,due_date,note,created_at) VALUES (999,999,'2026-01-01','test','2026-01-01')"))


def test_invalid_initial_csv_does_not_partially_commit(engine, config, tmp_path):
    from dataclasses import replace
    from connectiq.validation import ValidationError
    bad = tmp_path / "bad.csv"
    bad.write_text("Company Name\n" + "\n".join([f"Test {i}" for i in range(65)]) + "\n\n")
    with pytest.raises(ValidationError):
        initialize(engine, replace(config, company_csv=bad))
    with transaction(engine) as s:
        assert s.scalar(select(func.count()).select_from(Company)) == 0


def test_missing_source_is_explicit(engine, config, tmp_path):
    from dataclasses import replace
    result = initialize(engine, replace(config, company_csv=tmp_path / "absent.csv"))
    assert result["status"] == "missing_csv"
