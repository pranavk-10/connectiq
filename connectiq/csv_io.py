"""Strict UTF-8 CSV imports and spreadsheet-safe exports; imports never update existing rows."""
import csv
import io
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from connectiq.models import Company, Prospect
from connectiq.services import change_status, save_company, save_prospect
from connectiq.validation import DuplicateError, ValidationError, aware

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10000
COMPANY_COLUMNS = {"Company Name": "name", "Website": "website", "Locations": "locations",
                   "Domains": "domains", "Verification Status": "verification_status", "Notes": "notes"}
PROSPECT_COLUMNS = {"Full Name": "full_name", "Company Name": "company_name", "Designation": "designation",
                    "LinkedIn URL": "linkedin_url", "Location": "location", "Category": "category",
                    "Discovery Date": "discovery_date", "Date Added": "created_at", "Request Sent At": "request_sent_at",
                    "Accepted At": "accepted_at", "Last Interaction At": "last_interaction_at", "Status": "status",
                    "Priority": "priority", "Notes": "notes"}


@dataclass
class ImportSummary:
    total: int = 0
    imported: int = 0
    duplicates: list = field(default_factory=list)
    invalid: list = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


def read_csv(content, required):
    if not isinstance(content, bytes) or len(content) > MAX_BYTES:
        raise ValidationError("Upload a UTF-8 CSV smaller than 5 MB.")
    try:
        decoded = content.decode("utf-8-sig")
        if "\x00" in decoded:
            raise ValidationError("CSV contains null bytes.")
        reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
        header = next(reader, [])
        if len(set(header)) != len(header):
            raise ValidationError("CSV has duplicate column headers.")
        if not set(required).issubset(header):
            raise ValidationError("Required CSV columns: " + ", ".join(required))
        rows = []
        for line, row in enumerate(reader, 2):
            if len(rows) >= MAX_ROWS:
                raise ValidationError("CSV is limited to 10,000 records per import.")
            if len(row) != len(header):
                rows.append((line, None))
            else:
                rows.append((line, dict(zip(header, row))))
        return rows
    except (UnicodeDecodeError, csv.Error):
        raise ValidationError("The file must be a valid UTF-8 CSV with consistent columns.") from None


def import_companies(session, content):
    rows = read_csv(content, ["Company Name"])
    summary = ImportSummary(total=len(rows))
    for line, row in rows:
        if row is None:
            summary.invalid.append({"row": line, "reason": "Wrong number of columns"})
            continue
        try:
            with session.begin_nested():
                data = {field: row[col] for col, field in COMPANY_COLUMNS.items() if col in row}
                save_company(session, data)
            summary.imported += 1
        except (DuplicateError, IntegrityError):
            summary.duplicates.append({"row": line, "name": row["Company Name"]})
        except ValidationError as exc:
            summary.invalid.append({"row": line, "name": row["Company Name"], "reason": str(exc)})
    return summary


def parse_instant(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Export uses offsets. Bare CSV dates and datetimes are explicitly interpreted as UTC.
        return aware(parsed)
    except ValueError:
        raise ValidationError("Use ISO dates/times, for example 2026-10-20T10:30:00+05:30.") from None


def import_prospects(session, user_id, content):
    rows = read_csv(content, ["Full Name", "Company Name", "LinkedIn URL"])
    summary = ImportSummary(total=len(rows))
    for line, row in rows:
        if row is None:
            summary.invalid.append({"row": line, "reason": "Wrong number of columns"})
            continue
        try:
            with session.begin_nested():
                data = {field: row[col] for col, field in PROSPECT_COLUMNS.items() if col in row}
                company = session.scalar(select(Company).where(Company.name == data.pop("company_name")))
                if not company:
                    raise ValidationError("Company is missing. Import companies first; names must match exactly.")
                data["company_id"] = company.id
                if data.get("discovery_date"):
                    try:
                        data["discovery_date"] = date.fromisoformat(data["discovery_date"])
                    except ValueError:
                        raise ValidationError("Discovery Date must use YYYY-MM-DD.") from None
                else:
                    data.pop("discovery_date", None)
                p = save_prospect(session, user_id, data)
                requested = data.get("request_sent_at", "")
                accepted = data.get("accepted_at", "")
                status = data.get("status") or "Discovered"
                if requested:
                    change_status(session, user_id, p.id, "Request Sent", parse_instant(requested))
                if accepted:
                    change_status(session, user_id, p.id, "Accepted", parse_instant(accepted))
                if status in ("Request Sent", "Accepted", "Declined", "Withdrawn") and not requested:
                    raise ValidationError("Request Sent At is required for this imported status.")
                if status == "Accepted" and not accepted:
                    raise ValidationError("Accepted At is required for an accepted imported prospect.")
                if data.get("last_interaction_at"):
                    from connectiq.services import add_interaction
                    add_interaction(session, user_id, p.id, "Interaction date imported from CSV.", parse_instant(data["last_interaction_at"]))
                if data.get("created_at"):
                    from connectiq.services import record_event
                    from connectiq.validation import past_instant
                    original = past_instant(parse_instant(data["created_at"]))
                    p.created_at = original
                    record_event(session, user_id, "imported_date_added", p, details={"date_added": original.isoformat()})
                change_status(session, user_id, p.id, status)
            summary.imported += 1
        except (DuplicateError, IntegrityError) as exc:
            summary.duplicates.append({"row": line, "name": row["Full Name"], "reason": str(exc) if isinstance(exc, DuplicateError) else "Duplicate record"})
        except ValidationError as exc:
            summary.invalid.append({"row": line, "name": row["Full Name"], "reason": str(exc)})
    return summary


def safe_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def export_frame(frame):
    safe = frame.map(safe_cell)
    return safe.to_csv(index=False, lineterminator="\n").encode("utf-8-sig")


def export_companies(session):
    rows = [{col: getattr(c, field) for col, field in COMPANY_COLUMNS.items()}
            for c in session.scalars(select(Company).order_by(Company.name))]
    return export_frame(pd.DataFrame(rows, columns=list(COMPANY_COLUMNS)))


def export_prospects(session, user_id):
    rows = []
    query = select(Prospect, Company.name).join(Company).where(Prospect.user_id == user_id, Prospect.deleted_at.is_(None))
    for p, company_name in session.execute(query):
        row = {}
        for col, field in PROSPECT_COLUMNS.items():
            value = company_name if field == "company_name" else getattr(p, field)
            if isinstance(value, datetime):
                value = aware(value).isoformat()
            row[col] = value
        rows.append(row)
    return export_frame(pd.DataFrame(rows, columns=list(PROSPECT_COLUMNS)))
