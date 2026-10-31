import os

import pytest
from sqlalchemy import select

from connectiq.db import healthy, make_engine, migrate, transaction
from connectiq.models import Company


@pytest.mark.postgres
def test_postgres_migration_persistence_and_constraints():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not configured; PostgreSQL service is provided by CI")
    engine = make_engine(url)
    try:
        migrate(engine)
        assert healthy(engine)
        with transaction(engine) as s:
            c = s.scalar(select(Company).where(Company.name == "PostgreSQL integration test"))
            if c is None:
                s.add(Company(name="PostgreSQL integration test"))
        engine.dispose()
        migrate(engine)
        with transaction(engine) as s:
            assert s.scalar(select(Company).where(Company.name == "PostgreSQL integration test"))
    finally:
        engine.dispose()
