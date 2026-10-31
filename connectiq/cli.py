"""Operations: python -m connectiq.cli {setup,password,init,seed,health,serve}."""
import argparse
import getpass
import json
import os
import secrets
import subprocess
import sys
import urllib.request

from connectiq.auth import hash_password, valid_hash
from connectiq.bootstrap import initialize
from connectiq.config import ROOT, get_config
from connectiq.csv_io import import_companies, read_csv
from connectiq.db import healthy, make_engine, migrate, transaction
from connectiq.validation import ValidationError


def setup():
    path = ROOT / ".env"
    if path.exists():
        raise ValidationError(".env already exists; edit it directly. Use the password command to generate a new hash.")
    email = input("Owner email: ").strip().lower()
    import re
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValidationError("Enter a valid email address.")
    password = getpass.getpass("Password (12+ characters): ")
    if password != getpass.getpass("Confirm password: "):
        raise ValidationError("Passwords did not match.")
    encoded = hash_password(password)
    path.write_text(f"APP_ENV=development\nDATABASE_URL=sqlite:///data/connectiq.db\nADMIN_EMAIL={email}\nADMIN_PASSWORD_HASH='{encoded}'\nSTREAMLIT_SERVER_COOKIE_SECRET={secrets.token_urlsafe(48)}\nDEFAULT_TIMEZONE=Asia/Kolkata\nCOMPANY_CSV_PATH=data/companies.csv\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)
    print("Private .env created. Run python -m connectiq.cli serve.")


def main():
    parser = argparse.ArgumentParser(description="ConnectIQ operations")
    parser.add_argument("command", choices=["setup", "password", "init", "seed", "health", "serve"])
    parser.add_argument("--http", action="store_true", help="Also check Streamlit HTTP health")
    args = parser.parse_args()
    if args.command == "setup":
        setup()
        return
    if args.command == "password":
        print(hash_password(getpass.getpass("Password (12+ characters): ")))
        return
    config = get_config()
    engine = make_engine(config.database_url)
    try:
        if args.command == "health":
            if not healthy(engine):
                raise ValidationError("Database is unavailable.")
            if args.http:
                with urllib.request.urlopen(f"http://127.0.0.1:{os.getenv('PORT', '8501')}/_stcore/health", timeout=5) as response:
                    if response.status != 200:
                        raise ValidationError("HTTP health check failed.")
            print("healthy")
        elif args.command == "seed":
            # Useful for validating the supplied dataset without creating an owner account.
            migrate(engine)
            content = config.company_csv.read_bytes()
            if len(read_csv(content, ["Company Name"])) != 66:
                raise ValidationError("Initial CSV must contain 66 records.")
            with transaction(engine) as s:
                summary = import_companies(s, content)
                if summary.invalid:
                    raise ValidationError("Invalid rows in initial CSV; no import committed.")
                print(json.dumps(summary.as_dict(), indent=2))
        else:
            if config.environment == "production":
                if not config.admin_email or not valid_hash(config.password_hash):
                    raise ValidationError("Configure ADMIN_EMAIL and ADMIN_PASSWORD_HASH before deployment.")
                if len(os.getenv("STREAMLIT_SERVER_COOKIE_SECRET", "")) < 32:
                    raise ValidationError("Production requires a random STREAMLIT_SERVER_COOKIE_SECRET of at least 32 characters.")
            summary = initialize(engine, config)
            if args.command == "init":
                print(json.dumps(summary, indent=2))
            else:
                # Streamlit reads its cookie secret from the environment; never place it in argv.
                engine.dispose()
                command = [sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"),
                           "--server.address=0.0.0.0", f"--server.port={os.getenv('PORT', '8501')}"]
                if os.name == "nt":
                    raise SystemExit(subprocess.call(command, cwd=ROOT))
                os.execv(sys.executable, command)
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except (ValidationError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
