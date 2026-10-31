from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from connectiq.config import ROOT


def make_engine(url):
    kwargs = {"pool_pre_ping": True, "hide_parameters": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if ":memory:" not in url:
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    else:
        kwargs.update(pool_size=5, max_overflow=5, pool_recycle=300)
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _):
            # Explicit BEGIN fixes sqlite3's legacy savepoint behavior: releasing a nested
            # import savepoint must never commit ahead of the outer transaction.
            connection.isolation_level = None
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
        @event.listens_for(engine, "begin")
        def begin_sqlite(connection):
            connection.exec_driver_sql("BEGIN")
    return engine


def migrate(engine):
    cfg = AlembicConfig(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(706614321)"))
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


@contextmanager
def transaction(engine):
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        with session.begin():
            yield session
    finally:
        session.close()


def healthy(engine):
    with engine.connect() as connection:
        return connection.execute(text("SELECT 1")).scalar_one() == 1
