"""Boot the real server against an isolated database and verify its HTTP health."""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from connectiq.auth import hash_password  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="connectiq-smoke-") as directory:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = os.environ.copy()
        env.update(DATABASE_URL="sqlite:///" + (Path(directory) / "smoke.db").as_posix(),
                   APP_ENV="development", ADMIN_EMAIL="smoke@example.test",
                   ADMIN_PASSWORD_HASH=hash_password(secrets.token_urlsafe(32)),
                   STREAMLIT_SERVER_COOKIE_SECRET=secrets.token_urlsafe(48), PORT=str(port),
                   COMPANY_CSV_PATH=str(ROOT / "data/companies.csv"))
        subprocess.run([sys.executable, "-m", "connectiq.cli", "init"], env=env, cwd=ROOT, check=True,
                       stdout=subprocess.DEVNULL)
        with (Path(directory) / "server.log").open("w+") as log:
            process = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app.py",
                "--server.address=127.0.0.1", f"--server.port={port}", "--server.headless=true"],
                env=env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError("Server exited before readiness: " + log.read())
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2) as response:
                            if response.status == 200:
                                print("Streamlit startup and HTTP health passed.")
                                return
                    except (urllib.error.URLError, TimeoutError):
                        time.sleep(0.3)
                raise RuntimeError("Streamlit did not become healthy within 45 seconds.")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    main()
