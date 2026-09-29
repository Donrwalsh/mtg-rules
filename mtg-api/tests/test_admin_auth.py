import pytest
from conftest import admin_client, memory_engine
from fastapi.testclient import TestClient
from pydantic import SecretStr

from mtg_api.admin_auth import (
    ADMIN_SESSION_COOKIE,
    session_secret,
    sign_session,
    verify_session,
)
from mtg_api.config import settings
from mtg_api.main import app, get_db_engine

SECRET = session_secret("pw")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def password(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", SecretStr("pw"))


def test_session_round_trips():
    assert verify_session(sign_session(SECRET, now=1000.0), SECRET, now=1001.0)


def test_session_expires():
    value = sign_session(SECRET, now=1000.0)
    assert not verify_session(value, SECRET, now=1000.0 + 91 * 24 * 3600)


@pytest.mark.parametrize("value", [None, "", "garbage", "123.", ".abc", "12x.abc"])
def test_malformed_sessions_are_rejected(value):
    assert not verify_session(value, SECRET, now=0.0)


def test_session_signed_with_another_password_is_rejected():
    assert not verify_session(sign_session(session_secret("other"), now=0.0), SECRET, now=1.0)


def test_tampered_expiry_is_rejected():
    expires, sig = sign_session(SECRET, now=0.0).split(".")
    assert not verify_session(f"{int(expires) + 1}.{sig}", SECRET, now=1.0)


def test_login_with_wrong_password_is_403(password):
    resp = TestClient(app).post("/api/v1/auth/login", json={"password": "nope"})
    assert resp.status_code == 403


def test_login_is_disabled_without_a_configured_password(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", SecretStr(""))
    resp = TestClient(app).post("/api/v1/auth/login", json={"password": ""})
    assert resp.status_code == 403


def test_login_sets_a_strict_httponly_cookie(password):
    resp = TestClient(app).post("/api/v1/auth/login", json={"password": "pw"})
    assert resp.status_code == 200
    cookie = resp.headers["set-cookie"].lower()
    assert cookie.startswith(f"{ADMIN_SESSION_COOKIE}=")
    assert "httponly" in cookie
    assert "samesite=strict" in cookie


def test_me_reflects_login_and_logout(password):
    client = TestClient(app)
    assert client.get("/api/v1/auth/me").json() == {"is_admin": False}
    client.post("/api/v1/auth/login", json={"password": "pw"})
    assert client.get("/api/v1/auth/me").json() == {"is_admin": True}
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/auth/me").json() == {"is_admin": False}


def test_history_requires_login(password):
    app.dependency_overrides[get_db_engine] = memory_engine
    assert TestClient(app).get("/api/v1/queries").status_code == 401


def test_history_requires_the_marker_header(password):
    app.dependency_overrides[get_db_engine] = memory_engine
    client = TestClient(app)
    client.post("/api/v1/auth/login", json={"password": "pw"})
    assert client.get("/api/v1/queries").status_code == 403


def test_history_with_admin_session(monkeypatch):
    app.dependency_overrides[get_db_engine] = memory_engine
    assert admin_client(monkeypatch).get("/api/v1/queries").status_code == 200
