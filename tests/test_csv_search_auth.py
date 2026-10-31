import csv
import io
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pandas as pd
import pytest
from sqlalchemy import func, select

from connectiq.auth import authenticate, hash_password, verify_password
from connectiq.csv_io import MAX_BYTES, export_companies, export_frame, export_prospects, import_companies, import_prospects
from connectiq.models import Company, Prospect, SearchHistory
from connectiq.services import complete_search, create_search, search_url
from connectiq.validation import ValidationError


def test_company_csv_validation_and_summary(session):
    with pytest.raises(ValidationError, match="Required CSV columns"):
        import_companies(session, b"Name\nMissing header\n")
    summary = import_companies(session, b'Company Name\nTest company\nTest company\n""\nAnother,extra\n')
    assert summary.imported == 1 and len(summary.duplicates) == 1 and len(summary.invalid) == 2


@pytest.mark.parametrize("content", [b"Company Name\n\xff", b"Company Name\n\x00", b"Company Name,Company Name\nA,A", b"x" * (MAX_BYTES + 1)])
def test_csv_malformed_inputs(session, content):
    with pytest.raises(ValidationError):
        import_companies(session, content)


def test_company_export_preserves_source_names(session):
    import_companies(session, 'Company Name\n"Exact, punctuation"\nUnicode — name\n'.encode())
    rows = list(csv.DictReader(io.StringIO(export_companies(session).decode("utf-8-sig"))))
    assert {r["Company Name"] for r in rows} == {"Exact, punctuation", "Unicode — name"}


def test_prospect_import_export_and_bad_row_rollback(session, company):
    frame = pd.DataFrame([
        {"Full Name": "Imported person", "Company Name": company.name, "LinkedIn URL": "linkedin.com/in/imported-test",
         "Status": "Accepted", "Request Sent At": "2026-01-05T10:00:00+05:30", "Accepted At": "2026-01-07T10:00:00+05:30"},
        {"Full Name": "Invalid chronology", "Company Name": company.name, "LinkedIn URL": "linkedin.com/in/bad-test",
         "Status": "Accepted", "Request Sent At": "2026-01-07T10:00:00+05:30", "Accepted At": "2026-01-05T10:00:00+05:30"},
    ])
    summary = import_prospects(session, 1, frame.to_csv(index=False).encode())
    assert summary.imported == 1 and len(summary.invalid) == 1
    assert session.scalar(select(func.count()).select_from(Prospect)) == 1
    exported = list(csv.DictReader(io.StringIO(export_prospects(session, 1).decode("utf-8-sig"))))
    assert exported[0]["Status"] == "Accepted" and exported[0]["Request Sent At"] == "2026-01-05T04:30:00+00:00"
    repeated = import_prospects(session, 1, export_prospects(session, 1))
    assert repeated.imported == 0 and len(repeated.duplicates) == 1


def test_import_requires_actual_connection_dates(session, company):
    content = f'Full Name,Company Name,LinkedIn URL,Status\nMissing date,{company.name},linkedin.com/in/missing-date,Request Sent\n'.encode()
    summary = import_prospects(session, 1, content)
    assert summary.imported == 0 and len(summary.invalid) == 1
    assert session.scalar(select(func.count()).select_from(Prospect)) == 0


def test_unknown_company_not_invented(session):
    content = b"Full Name,Company Name,LinkedIn URL\nA person,Unknown company,linkedin.com/in/unknown\n"
    result = import_prospects(session, 1, content)
    assert result.imported == 0 and len(result.invalid) == 1
    assert session.scalar(select(func.count()).select_from(Company)) == 0


def test_formula_injection_export():
    payload = export_frame(pd.DataFrame({"Notes": ["=1+2", "+cmd", "@SUM(A1)", "normal text"]})).decode("utf-8-sig")
    values = [r["Notes"] for r in csv.DictReader(io.StringIO(payload))]
    assert values == ["'=1+2", "'+cmd", "'@SUM(A1)", "normal text"]


def test_linkedin_search_url_encoding():
    url = search_url("L&T", "RTL Design Engineer", "Bengaluru")
    assert urlsplit(url).netloc == "www.linkedin.com"
    assert parse_qs(urlsplit(url).query)["keywords"] == ['"RTL Design Engineer" "L&T" "Bengaluru"']
    assert '"India"' in parse_qs(urlsplit(search_url("Test", "ASIC", "All India")).query)["keywords"][0]


def test_search_deduplication_and_completion(session, company):
    first, created = create_search(session, 1, company.id, "RTL Design Engineer", "Bengaluru")
    second, again = create_search(session, 1, company.id, "RTL Design Engineer", "Bengaluru")
    assert created and not again and first.id == second.id
    complete_search(session, 1, first.id)
    assert session.get(SearchHistory, first.id).completed_at


def test_password_hashes_and_no_plaintext(password_hash):
    assert verify_password("Test-only-long-password", password_hash)
    assert not verify_password("wrong", password_hash)
    assert "Test-only" not in password_hash
    assert not verify_password("wrong", "invalid")
    with pytest.raises(ValidationError):
        hash_password("short")


def test_authentication_lockout(session):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for _ in range(5):
        assert authenticate(session, "owner@example.test", "wrong", now) is None
    assert authenticate(session, "owner@example.test", "Test-only-long-password", now + timedelta(minutes=1)) is None
    assert authenticate(session, "owner@example.test", "Test-only-long-password", now + timedelta(minutes=16)) == 1
    assert authenticate(session, "another@example.test", "Test-only-long-password", now + timedelta(minutes=17)) is None
