# ConnectIQ

A private semiconductor networking CRM for India. Discover people through LinkedIn search links, save reviewed prospects, record your own invitations and acceptances, and manage networking follow-ups. All charts use your actual records.

**LinkedIn interactions are manual.** ConnectIQ never scrapes LinkedIn, logs in to LinkedIn, sends invitations or messages, generates outreach, or extracts session cookies.

## Run locally

Requires Python 3.11 or newer. From this directory:

```powershell
python -m venv .venv
.venv/Scripts/Activate.ps1
python -m pip install -r requirements.txt
python -m connectiq.cli setup
python -m connectiq.cli serve
```

On macOS/Linux, activate with `source .venv/bin/activate`. Open **http://localhost:8501** and sign in with the owner credentials you chose. The setup command creates a private, ignored `.env` with a salted password hash and a random cookie secret. It refuses to overwrite an existing `.env`.

Alternatively, copy `.env.example` to `.env`, set your owner email and generated hash, then run `serve`. Generate a password hash with `python -m connectiq.cli password`; enter the password interactively. Keep the entire `scrypt$…` output, wrapped in single quotes in `.env`. Do not put passwords in shell commands or Git.

Startup runs Alembic migrations, creates/updates the configured owner, and imports the authoritative company CSV once. Subsequent restarts preserve prospects, reminders, settings, company edits and deletions. `streamlit run app.py` also supports initialization; use the `serve` command for deployment configuration checks.

## Original 66-company database

The supplied `india_rtl_asic_companies.csv` was found in the repository. `data/companies.csv` is a byte-for-byte copy: **66 rows, 66 exact unique names, required `Company Name` header, no invalid records**. Source SHA-256:

```text
e112ab8939225aa78d1bf62c6e749cc9d3e29afa0765f6de38173ccfa9087503
```

No names were renamed or substituted. The local `data/connectiq.db` has been seeded with these 66 companies. Local database files are ignored by Git and excluded from Docker; production reconstructs the same initial company inventory from the committed CSV. The original import summary and checksum are visible in Companies → Import / export.

Imports skip only exact duplicate company names. A case or whitespace difference remains a different company so the original source is preserved. Blank names, invalid websites, malformed CSV rows and oversized values are reported with row numbers. CSV additions never overwrite existing companies. All initial records start as **Unverified**. Being listed does not establish that a company has an India RTL/ASIC team.

If the source file is removed, the app instructs you to restore it at `data/companies.csv`; it never substitutes a company list. The initial seed requires 66 source records. Future CSV imports through Companies may have any number of valid rows up to the upload limit.

## Working in ConnectIQ

| Page | Capabilities |
| --- | --- |
| Overview | Live inventory and connection KPIs, daily activity, pipeline, recent activity, distributions and additional trends |
| Discover People | Company + predefined/custom title + Indian city searches, new-tab LinkedIn links, URL copy, deduplicated search history, completion and prospect entry |
| Prospects | Add/edit, all eight statuses, filtering, priority/date sorting, canonical profile URL duplicates, same-company name warnings, CSV import/export, audit history and archive |
| Follow-ups | Manual reminders, overdue/upcoming/completed views, date filters, pending-age review and interaction notes |
| Analytics | Date ranges, daily/Monday-week/month summaries, acceptance trends, company/role/location/status breakdowns, pending ageing and CSV audit/report downloads |
| Companies | Original directory, company count, prospect/accepted/pending counts, add/edit/delete, search, verification filtering and CSV tools |
| Settings | Profile, preferred cities/categories, timezone, light/dark appearance, database health, exports and authentication guidance |

Company deletion requires confirmation and is blocked when any prospect history references the company. Prospect deletion requires confirmation and **archives** the prospect; the Archive view restores it. Archived LinkedIn URLs remain reserved, and archived activity remains in historical reports. This behavior protects audit history from silent loss.

## Dates, activity and analytics

All instants are stored in UTC; the UI and calendar boundaries default to **Asia/Kolkata**. Date-only discovery/reminder fields remain calendar dates. Weeks start Monday. Change the reporting timezone in Settings.

* `Request Sent` records an invitation event and its actual date. Re-saving the same status does not count again. A new request cycle is allowed after Declined or Withdrawn; it records another invitation rather than modifying the old one.
* `Accepted` requires a recorded request, rejects an acceptance before the request, and links the acceptance to that request cycle. Record the original request date first for existing connections.
* Future activity dates are rejected. Reminder dates can be future or overdue.
* Date corrections require a reason. They append a `date_corrected` event referring to the original event; the original remains unchanged. Reports use the latest correction. Database triggers reject updates/deletions to the activity table.
* **Total requests** counts recorded invitation cycles, including archived people. **Current accepted/pending/declined/withdrawn counts** describe active prospects. Follow-up Required with an acceptance is an accepted connection; without an acceptance and with a request it is pending.
* **Overall acceptance rate** = recorded accepted request cycles / all recorded request cycles. **Period cohort rate** = requests sent in the selected period and accepted by its end / requests sent in that period. This avoids misleading rates above 100% when this month's acceptances came from last month's requests.
* The acceptance trend is cumulative through each date, including pre-period requests. Daily acceptances are counted on their acceptance dates. The activity audit filters by when a change was recorded; charts of requests/acceptances use effective dates.
* Overview KPIs are explicitly labelled live totals; its date range controls charts and recent activity. Analytics distinguishes period metrics, discovered-in-period coverage and the current pending snapshot. A partial week/month contains only days in the selected range.

## CSV formats

Use UTF-8 or UTF-8 with BOM. Limit: **5 MB and 10,000 records** per upload. Required column names are case-sensitive. Download templates from Companies or Prospects. Imports are transactional, validate each row in a savepoint, preserve existing records, and show imported, duplicate and invalid row details.

Company fields:

```text
Company Name,Website,Locations,Domains,Verification Status,Notes
```

Only `Company Name` is required. Optional verification values: Unverified, Research in progress, Verified by user.

Prospect fields:

```text
Full Name,Company Name,Designation,LinkedIn URL,Location,Category,Discovery Date,Date Added,Request Sent At,Accepted At,Last Interaction At,Status,Priority,Notes
```

Required: `Full Name`, `Company Name`, `LinkedIn URL`. Companies must exist with exactly matching names; unknown companies are reported, never invented. Valid LinkedIn person paths are `/in/profile-slug`; tracking parameters are discarded and supported LinkedIn host variants normalize to `https://www.linkedin.com/in/...`.

Discovery Date: `YYYY-MM-DD`. Activity timestamps: ISO 8601, for example `2026-10-20T14:30:00+05:30`; timestamps without offsets are interpreted as UTC. Imports with Request Sent/Accepted/Declined/Withdrawn statuses require a real `Request Sent At`; Accepted also requires `Accepted At`. No invented historical dates are filled in. Duplicate URLs and likely duplicate names are skipped with an explanation; use manual review to approve two different people with the same name.

Exports have a UTF-8 BOM for spreadsheet compatibility. Values beginning with spreadsheet formula characters are prefixed with an apostrophe to prevent formula execution; that safety prefix is retained if you reimport such a value. Use the **full export** in Import / export for migration; a filtered-view export is a human-readable report. CSV exports do not contain the complete audit/settings/reminder database; use database backups for disaster recovery.

## Architecture and schema

Python 3.11+, Streamlit, custom CSS, Plotly, Pandas, SQLAlchemy 2, Alembic. SQLite locally with WAL, enforced foreign keys and explicit transactions; PostgreSQL in production with connection pooling and migration/seed advisory locks. Every database URL comes from `DATABASE_URL`.

| Table | Relationships and purpose |
| --- | --- |
| users | Private owner identity, password hash, persistent lockout state |
| companies | Unique exact name, optional research information and verification |
| prospects | User + company foreign keys; unique `(user_id, linkedin_url)`; status/priority checks, indexed filters, optimistic versioning and archive date |
| activity_events | User + prospect + optional referenced event; recorded/effective UTC timestamps; append-only database triggers |
| follow_up_reminders | User + prospect; due date, note, completion timestamp |
| search_history | User + optional company; original search parameters and unique URL hash per user |
| user_settings | Unique user/key preferences, appearance and completed initial-import marker |

See [the schema migration](migrations/versions/a7195b0d964a_initial_schema.py) and [audit protection migration](migrations/versions/b620d120b407_append_only_audit.py). Models are in `connectiq/models.py`; transactional business operations in `services.py`; import/export in `csv_io.py`; analytics in `analytics.py`; presentation in `pages.py` and `ui.py`.

This release supports one private owner. Prospect, activity, reminder, search and settings operations are owner-scoped; the company directory is shared reference data. A multi-user release would need account provisioning and workspace-level company authorization before exposing multiple logins.

## Deploy on Render

1. Create a private GitHub repository and push this project, including both company CSV files and migrations. Never commit `.env`, local databases, backups or personal exports.
2. In Render, choose **New → Blueprint** and connect the repository. Render reads `render.yaml` and creates a PostgreSQL database and Docker web service in Singapore. The blueprint selects paid persistent service/database plans; confirm current pricing in Render.
3. Provide `ADMIN_EMAIL` and the entire hash from `python -m connectiq.cli password` as `ADMIN_PASSWORD_HASH`. In Render's value field, paste the raw hash **without wrapping quotes**. The blueprint generates `STREAMLIT_SERVER_COOKIE_SECRET` and injects the PostgreSQL internal connection string as `DATABASE_URL`.
4. Confirm `APP_ENV=production`, `DEFAULT_TIMEZONE=Asia/Kolkata`, `COMPANY_CSV_PATH=data/companies.csv` and `PORT=8501`. Deploy. Migrations run before the web process starts; the company import runs once. The application rejects SQLite in production and missing production credentials/cookie secrets.
5. Visit the HTTPS Render URL. Signed-out visitors must see only sign-in. Sign in, open Companies, and confirm 66 companies and the import summary. Record a real reviewed prospect and verify the dashboard changes. Sign out and confirm the private content disappears.
6. Verify `https://YOUR-SERVICE.onrender.com/_stcore/health` returns `ok`. In Render Shell run `python -m connectiq.cli health` for database reachability and `python -m alembic current` for schema status. The Docker health check tests both database and HTTP.

For manual Render setup, create PostgreSQL first; copy its **internal database URL** into the Docker web service's `DATABASE_URL` and set the variables above. SQLAlchemy accepts `postgres://` and `postgresql://` environment URLs and normalizes them to the psycopg driver. Use the same region. Use TLS (`sslmode=require`) for external PostgreSQL connections and restrict external database access in the provider settings.

Keep a single web-service instance for this initial deployment; Streamlit keeps UI sessions on its server. Horizontal scaling requires sticky WebSocket sessions or an authentication/session redesign. Render terminates HTTPS and proxies WebSockets. The app retains Streamlit's CORS/XSRF protection and disables usage telemetry. The public health endpoint contains no personal records.

GitHub Actions installs the pinned dependencies, runs lint, SQLite and isolated PostgreSQL tests plus real Streamlit AppTest flows, and builds the Docker image. `autoDeployTrigger: checksPass` gates automatic deploys on those checks. See [TEST_RESULTS.md](TEST_RESULTS.md) for which checks were actually run in this workspace.

## Environment variables

| Variable | Value / action |
| --- | --- |
| DATABASE_URL | SQLite local path or production PostgreSQL URL; Render injects this |
| ADMIN_EMAIL | Your private sign-in email |
| ADMIN_PASSWORD_HASH | Interactive scrypt hash from the CLI; never a plaintext password |
| STREAMLIT_SERVER_COOKIE_SECRET | Random secret of at least 32 characters; Render generates one |
| APP_ENV | `development` locally, `production` on Render |
| DEFAULT_TIMEZONE | `Asia/Kolkata` by default |
| COMPANY_CSV_PATH | `data/companies.csv` |
| SESSION_HOURS | Absolute login lifetime, 1–24 hours; default 8 |
| PORT | Streamlit listening port; blueprint uses 8501 |
| LOG_LEVEL | `INFO` by default |

Five failed attempts lock the owner for 15 minutes, persisted in the database. Sessions expire after the configured lifetime, clear on sign-out, and invalidate on password-hash rotation after restart. Change `ADMIN_PASSWORD_HASH` and restart to rotate credentials without replacing database records. A lost password is recovered through the deployment environment; there is no public registration or reset email service.

Only supply your GitHub/Render access, owner email/password hash, and deployment database configuration. No AI API key, LinkedIn password or LinkedIn cookies are required.

## Backup and restore

Enable managed PostgreSQL backups/recovery appropriate to your Render plan. Also keep periodic encrypted off-provider backups and test restoration. Treat backups as personal data. Database credentials never belong in backup filenames, Git or screenshots.

**Local SQLite:**

```powershell
python scripts/backup.py
```

The script uses SQLite's online backup API so WAL data is included. Backups go to the ignored `backups/` directory. To restore, stop ConnectIQ, point `DATABASE_URL` at a *new* SQLite filename, copy the selected backup to that filename, then run `python -m connectiq.cli serve`. Keep the old database until you have verified the restored counts, activity and reminders. Do not simply copy an active `.db` file without its WAL.

**PostgreSQL:** install PostgreSQL client tools on a trusted computer. Configure `DATABASE_URL` with the provider's external URL and TLS, then run `python scripts/backup.py`. It creates a `pg_dump --format=custom` archive, passing credentials through the subprocess environment. Render's application image need not contain the client tools.

To restore safely, create a **new, empty PostgreSQL database** in Render. Configure standard PostgreSQL environment variables (`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGSSLMODE=require`) for that new database, then:

```text
pg_restore --no-owner --no-acl --exit-on-error --dbname=NEW_DATABASE_NAME backups/connectiq-TIMESTAMP.dump
```

Run migrations against the restored database, check company/prospect/event/reminder counts and sign in. Switch the web service's `DATABASE_URL` only after verifying the restored data. Keep the previous database during the recovery window. Do not restore into an occupied production database or run downgrade/drop/reset commands as an update strategy. Audit triggers protect activity history during normal use; PostgreSQL dumps restore table data before triggers, so full disaster recovery is supported.

## Update without losing data

Back up first. Test migrations against a restored database, run CI, then deploy the new image with the **same PostgreSQL DATABASE_URL**. Startup runs `alembic upgrade head`, never `drop_all` or a database reset. The initial company seed marker prevents a restart from re-adding deliberately deleted companies. Use the Companies CSV importer for future additions, not a changed initial seed.

Create future schema migrations with `alembic revision --autogenerate -m description`, review their operations, test on SQLite and PostgreSQL, and commit them. Roll back application images only when compatible with the migrated schema; prefer a forward fix or restoring a verified backup over destructive schema downgrade.

## Test and troubleshoot

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest --cov=connectiq --cov-report=term-missing
python -m ruff check .
python -m alembic check
python -m connectiq.cli health
```

Set `TEST_DATABASE_URL` to a **dedicated test PostgreSQL database** to run the shared backend suite on both engines. Tests create and remove random `connectiq_test_*` schemas in that database. CI provides an isolated PostgreSQL 16 service automatically. UI tests run Streamlit's real AppTest runtime, including sign-in, all seven pages, prospect entry and discovery.

If setup appears instead of sign-in, configure the owner email and valid hash. If startup fails, run `health` and `alembic current` and verify the environment. User-visible errors contain a reference ID; logs contain the exception type and reference, not SQL parameters or prospect information. A schema migration failure stops deployment rather than starting against an incompatible database.

The project was built in a network-restricted workspace. Backend packages could be recovered from local caches; Streamlit, Plotly, psycopg, Docker and a PostgreSQL service were unavailable locally. Production-pinned runtime installation and browser/Render validation therefore still require the configured CI or an environment with those dependencies. This limitation is recorded explicitly rather than claiming an unrun deployment or visual audit.
