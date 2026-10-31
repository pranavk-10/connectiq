"""Real Streamlit script execution, authentication, navigation, forms and database-backed UI."""
import pytest

pytest.importorskip("streamlit", reason="Streamlit package is required for UI integration tests")
pytest.importorskip("plotly", reason="Plotly package is required for UI integration tests")
from streamlit.testing.v1 import AppTest  # noqa: E402

from connectiq.config import ROOT  # noqa: E402


def button(at, label):
    return next(b for b in at.button if b.label == label)


@pytest.fixture
def app(monkeypatch, tmp_path, password_hash):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "ui.db").as_posix())
    monkeypatch.setenv("ADMIN_EMAIL", "owner@example.test")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", password_hash)
    monkeypatch.setenv("APP_ENV", "development")
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    app.run()
    return app


def test_unauthenticated_content_is_private(app):
    assert not app.exception
    assert not app.dataframe
    assert not any(b.label == "Prospects" for b in app.button)


def sign_in(app):
    app.text_input[0].set_value("owner@example.test")
    app.text_input[1].set_value("Test-only-long-password")
    button(app, "Sign in").click().run()
    assert not app.exception and not app.error
    return app


def test_all_pages_and_signout(app):
    sign_in(app)
    for page in ["Overview", "Discover People", "Prospects", "Follow-ups", "Analytics", "Companies", "Settings"]:
        button(app, page).click().run()
        assert not app.exception and not app.error, page
        assert any(t.value == page for t in app.title), page
    button(app, "Sign out").click().run()
    assert not app.dataframe


def test_add_prospect_and_search_flow(app):
    sign_in(app)
    button(app, "Prospects").click().run()
    app.radio[0].set_value("Add prospect").run()
    next(t for t in app.text_input if t.label == "Full name").set_value("UI test person")
    next(t for t in app.text_input if t.label == "LinkedIn profile URL").set_value("linkedin.com/in/ui-test-person")
    button(app, "Add prospect").click().run()
    assert not app.error and not app.exception
    app.radio[0].set_value("All prospects").run()
    assert any("UI test person" in str(df.value) for df in app.dataframe)
    button(app, "Discover People").click().run()
    button(app, "Generate search link").click().run()
    assert not app.exception and not app.error
    assert any("linkedin.com/search/results/people" in code.value for code in app.code)
