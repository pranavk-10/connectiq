from dataclasses import replace
from datetime import date
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from connectiq.auth import hash_password
from connectiq.config import ROOT, get_config
from connectiq.db import make_engine, migrate, transaction
from connectiq.models import User
from connectiq.services import save_company, save_prospect


@pytest.fixture(scope="session")
def password_hash():
    return hash_password("Test-only-long-password")


@pytest.fixture(params=["sqlite", "postgresql"] if os.getenv("TEST_DATABASE_URL") else ["sqlite"])
def engine(tmp_path, request):
    admin = None
    schema = "connectiq_test_" + uuid.uuid4().hex
    if request.param == "postgresql":
        admin = make_engine(os.environ["TEST_DATABASE_URL"])
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = make_url(os.environ["TEST_DATABASE_URL"]).update_query_dict({"options": f"-csearch_path={schema}"})
        engine = make_engine(url.render_as_string(hide_password=False))
    else:
        engine = make_engine("sqlite:///" + (tmp_path / "test.db").as_posix())
    migrate(engine)
    yield engine
    engine.dispose()
    if admin:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def session(engine, password_hash):
    with transaction(engine) as s:
        s.add(User(email="owner@example.test", password_hash=password_hash))
    with Session(engine, expire_on_commit=False) as s:
        yield s
        s.rollback()


@pytest.fixture
def owner(session):
    return session.get(User, 1)


@pytest.fixture
def company(session):
    return save_company(session, {"name": "Test silicon company"})


@pytest.fixture
def prospect(session, company):
    return save_prospect(session, 1, dict(full_name="Asha Rao", company_id=company.id,
        linkedin_url="https://www.linkedin.com/in/asha-rao-test", designation="RTL Design Engineer",
        location="Bengaluru", category="RTL Design", discovery_date=date(2026, 1, 1)))


@pytest.fixture
def config(engine, password_hash):
    return replace(get_config(), database_url=str(engine.url), admin_email="owner@example.test",
                   password_hash=password_hash, company_csv=ROOT / "data/companies.csv")
