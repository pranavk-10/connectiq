# Verification results

Run date: **2026-10-31**. Platform: Windows, Python **3.11.9**.

## Completed checks

```text
python -m pytest -q --cov=connectiq --cov-report=term-missing --junitxml=test-results/pytest.xml
58 passed, 2 skipped in 21.01s
```

The passed tests cover:

* SQLite initialization, Alembic migrations, indexes, foreign keys, transactional rollback and restart persistence.
* The actual supplied CSV: exact header, 66 company records, preservation of all names, byte equality and SHA-256.
* Import summaries, exact duplicates, invalid records, upload limits, UTF-8 validation, prospect CSV timestamps and row rollback.
* Company CRUD rules, duplicate detection, prospect creation/editing, canonical LinkedIn URLs and likely duplicate names.
* Status changes, request/acceptance timestamps, repeated saves, multiple request cycles and chronology validation.
* Immutable activity events, multiple audited date corrections and corrections after backfilled interactions.
* Asia/Kolkata midnight/week/month boundaries, acceptance-rate denominators, weekly/monthly grouping, empty reports and pending ageing.
* Reminders, completion idempotency, interaction notes, archive/restore and owner isolation.
* CSV exports and spreadsheet formula protection, search URL encoding/history deduplication and completion.
* Password hashing/verification, login failure throttling and lockout expiration.

Whole-package statement coverage: **47%**, including the UI and CLI that pytest did not execute. Core modules measured 87–100% (analytics 91%, services 90%, database 96%). This is not full application coverage.

Additional checks actually run:

| Check | Result |
| --- | --- |
| `alembic upgrade head` | Applied through `b620d120b407` |
| `alembic check` | No new upgrade operations detected |
| `python -m connectiq.cli seed` | 66 imported, 0 duplicates, 0 invalid |
| Direct local database inspection | 66 companies; 0 prospects; no sample people |
| SQLite `PRAGMA integrity_check` | `ok` |
| Original/canonical CSV byte comparison | Identical |
| `python -m connectiq.cli health` | `healthy` |
| SQLite online backup and backup query | Backup created; verified 66 companies |
| `compileall` for application/tests/migrations/scripts | Passed |
| Static Python global-name and unused-import analysis | Passed |
| `pip check` for the installed offline test environment | No broken requirements |
| Design skill CSS source lint | 0 FAIL, 0 WARN |

## Unavailable verification

Dependency installation from PyPI was attempted and blocked by network policy (`WinError 10013`). The local cache did not contain Streamlit, Plotly, psycopg or Ruff. Docker and a local PostgreSQL server were not available.

Consequently:

* The Streamlit UI integration test module was skipped because Streamlit was unavailable.
* The PostgreSQL integration test was skipped because `TEST_DATABASE_URL` was not configured.
* A real Streamlit startup was attempted and failed with `No module named streamlit`. Application startup is **not verified locally**.
* Browser screenshots, the real-browser design inspector, interactive visual review, Ruff, Docker build and an actual Render deployment were **not run**.

The CI workflow installs the pinned deployment/test requirements, runs Ruff, the shared backend suite against SQLite and isolated PostgreSQL schemas, Streamlit AppTest navigation/form flows, a real server/HTTP health smoke test, and a Docker build. That workflow is implemented but has not been executed on GitHub in this session. A successful CI run and deployment smoke check remain required before calling this production-verified.

## Offline dependency versions used

The core suite used packages recovered from the pre-existing local pip cache; these differ from deployment pins:

| Package | Exercised locally | Deployment / development pin |
| --- | --- | --- |
| SQLAlchemy | 2.0.55 | 2.0.43 |
| pandas | 2.1.3 | 2.3.2 |
| Alembic | 1.13.3 | 1.16.5 |
| pytest | 9.2.0 | 8.4.2 |
| python-dotenv | 1.2.4 | 1.1.1 |
| tzdata | 2026.6 | 2025.2 |

No Streamlit replacement, mocked frontend runtime, invented screenshots or fabricated production test results were used. See [README.md](README.md) for installation, configuration, deployment and recovery instructions, and [docs/DESIGN.md](docs/DESIGN.md) for the design decisions and visual verification limitation.
