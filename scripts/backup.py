"""Create a private SQLite backup or PostgreSQL custom-format archive.

Uses pg_dump installed on the operator's machine for PostgreSQL. Credentials stay
in the subprocess environment, rather than command arguments or logs.
"""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from urllib.parse import unquote, urlsplit, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from connectiq.config import get_config  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("backups"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    url = get_config().database_url
    if url.startswith("sqlite:///"):
        source = Path(url.removeprefix("sqlite:///"))
        if not source.is_file():
            raise SystemExit("SQLite database does not exist.")
        target = args.output / f"connectiq-{stamp}.sqlite3"
        with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True) as current, sqlite3.connect(target) as backup:
            current.backup(backup)
    else:
        parts = urlsplit(url.replace("postgresql+psycopg://", "postgresql://"))
        target = args.output / f"connectiq-{stamp}.dump"
        env = os.environ.copy()
        env.update(PGHOST=parts.hostname or "", PGPORT=str(parts.port or 5432),
                   PGUSER=unquote(parts.username or ""), PGPASSWORD=unquote(parts.password or ""),
                   PGDATABASE=unquote(parts.path.lstrip("/")))
        query = parse_qs(parts.query)
        env["PGSSLMODE"] = query.get("sslmode", ["require"])[0]
        try:
            subprocess.run(["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(target)], env=env, check=True)
        except FileNotFoundError:
            raise SystemExit("Install the PostgreSQL client tools (pg_dump), then retry.") from None
    if os.name != "nt":
        target.chmod(0o600)
    print(f"Backup created: {target}")


if __name__ == "__main__":
    main()
