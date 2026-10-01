"""Exercise the login boundary with real cookies and isolated credential storage."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

from calliope.auth import COOKIE, _issue_session, hash_password, initialize_auth, verify_password
from calliope.config import settings
from calliope.main import create_app

PASSWORD = "a-test-password-2026"
HEADERS = {"X-Calliope-Request": "1", "Origin": "https://testserver"}


@pytest.fixture
def auth_client(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_secure_cookie", True)
    monkeypatch.setattr(settings, "auth_allowed_origins", ["https://video.parcosepino.net"])
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "assets_dir", tmp_path / "data" / "assets")
    monkeypatch.setattr(settings, "dry_run", True)
    monkeypatch.setattr(settings, "queue_poll_interval_sec", 0.5)
    monkeypatch.setattr(type(settings), "save_config_file", lambda self: None)
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<title>Login shell</title>", encoding="utf-8")
    with TestClient(create_app(static_dir=static), base_url="https://testserver") as client:
        yield client


def _setup(client):
    code = (settings.data_dir / "auth-setup-code.txt").read_text().strip()
    return client.post(
        "/api/auth/setup",
        headers=HEADERS,
        json={
            "username": "admin",
            "password": PASSWORD,
            "setup_code": code,
        },
    )


def _login(client, *, username="admin", password=PASSWORD):
    return client.post(
        "/api/auth/login",
        headers=HEADERS,
        json={
            "username": username,
            "password": password,
        },
    )


@pytest.fixture
def signed_in(auth_client):
    assert _setup(auth_client).status_code == 200
    return auth_client


@pytest.mark.parametrize(
    "path",
    [
        "/api/projects",
        "/api/settings",
        "/api/workflows",
        "/api/jobs",
        "/api/library",
        "/api/file?path=secret.mp4",
        "/api/events",
        "/mcp",
        "/mcp/",
        "/docs",
        "/openapi.json",
    ],
)
def test_private_surfaces_require_login(auth_client, path):
    response = auth_client.get(path)
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_health_and_shell_remain_public(auth_client):
    assert auth_client.get("/api/health").status_code == 200
    assert "Login shell" in auth_client.get("/project/12").text
    status = auth_client.get("/api/auth/status")
    assert status.json() == {"enabled": True, "setup_required": True, "user": None}
    assert "setup_code" not in status.text


def test_setup_requires_local_code_and_cannot_be_replayed(auth_client):
    bad = auth_client.post(
        "/api/auth/setup",
        headers=HEADERS,
        json={
            "username": "admin",
            "password": PASSWORD,
            "setup_code": "wrong",
        },
    )
    assert bad.status_code == 401
    assert _setup(auth_client).status_code == 200
    assert not (settings.data_dir / "auth-setup-code.txt").exists()
    second = auth_client.post(
        "/api/auth/setup",
        headers=HEADERS,
        json={
            "username": "attacker",
            "password": PASSWORD,
            "setup_code": "wrong",
        },
    )
    assert second.status_code == 409
    assert auth_client.get("/api/auth/status").json()["user"] == {"username": "admin"}
    initialize_auth()
    assert not (settings.data_dir / "auth-setup-code.txt").exists()


def test_bootstrap_survives_server_restart(auth_client):
    path = settings.data_dir / "auth-setup-code.txt"
    original = path.read_text()
    initialize_auth()
    assert path.read_text() == original
    assert _setup(auth_client).status_code == 200


def test_setup_rejects_short_password(auth_client):
    response = auth_client.post(
        "/api/auth/setup",
        headers=HEADERS,
        json={
            "username": "admin",
            "password": "short",
            "setup_code": "anything",
        },
    )
    assert response.status_code == 422


def test_credentials_and_sessions_are_not_stored_in_plaintext(signed_in):
    token = signed_in.cookies.get(COOKIE)
    with sqlite3.connect(settings.data_dir / "auth.db") as conn:
        encoded = conn.execute("SELECT password_hash FROM account").fetchone()[0]
        stored_token = conn.execute("SELECT token_hash FROM sessions").fetchone()[0]
    assert encoded != PASSWORD and verify_password(PASSWORD, encoded)
    assert stored_token != token and len(stored_token) == 64
    assert hash_password(PASSWORD) != encoded
    assert not verify_password("wrong", encoded)


def test_cookie_is_secure_and_private(auth_client):
    response = _setup(auth_client)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert "Path=/" in cookie and "Max-Age=43200" in cookie
    assert auth_client.get("/api/projects").status_code == 200


def test_login_rotates_sessions_and_gives_generic_failures(signed_in):
    previous = signed_in.cookies.get(COOKIE)
    assert _login(signed_in).status_code == 200
    assert signed_in.cookies.get(COOKIE) != previous
    assert (
        signed_in.get("/api/projects", headers={"Cookie": f"{COOKIE}={previous}"}).status_code
        == 401
    )
    bad_name = _login(signed_in, username="unknown")
    bad_password = _login(signed_in, password="wrong")
    assert bad_name.status_code == bad_password.status_code == 401
    assert bad_name.json() == bad_password.json()


def test_logout_revokes_cookie_server_side(signed_in):
    previous = signed_in.cookies.get(COOKIE)
    assert signed_in.post("/api/auth/logout", headers=HEADERS, json={}).status_code == 200
    assert signed_in.cookies.get(COOKIE) is None
    assert (
        signed_in.get("/api/projects", headers={"Cookie": f"{COOKIE}={previous}"}).status_code
        == 401
    )
    assert signed_in.get("/api/auth/status").json()["user"] is None


def test_authenticated_media_supports_video_range_requests(signed_in):
    video = settings.assets_dir / "clip.mp4"
    video.write_bytes(b"fake-video-contents")
    response = signed_in.get(
        "/api/file",
        params={"path": str(video)},
        headers={"Range": "bytes=0-3"},
    )
    assert response.status_code == 206
    assert response.content == b"fake"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("column,value", [("expires_at", 1), ("last_seen", 1)])
def test_expired_and_idle_sessions_cannot_read_data(signed_in, column, value):
    with sqlite3.connect(settings.data_dir / "auth.db") as conn:
        conn.execute(f"UPDATE sessions SET {column} = ?", (value,))
    assert signed_in.get("/api/projects").status_code == 401
    assert signed_in.get("/api/auth/status").json()["user"] is None


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://testserver"},
        {"X-Calliope-Request": "1", "Origin": "https://attacker.example"},
        {"X-Calliope-Request": "1", "Origin": "null"},
    ],
)
def test_browser_mutations_reject_missing_marker_and_foreign_origins(signed_in, headers):
    assert (
        signed_in.post("/api/projects", headers=headers, json={"title": "Blocked"}).status_code
        == 403
    )
    assert signed_in.get("/api/projects").json() == []


def test_allowed_public_origin_can_mutate_and_preflight(signed_in):
    headers = {"X-Calliope-Request": "1", "Origin": "https://video.parcosepino.net"}
    response = signed_in.post("/api/projects", headers=headers, json={"title": "Allowed"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == headers["Origin"]
    preflight = signed_in.options(
        "/api/projects",
        headers={
            "Origin": headers["Origin"],
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-Calliope-Request,Content-Type",
        },
    )
    assert preflight.status_code == 200


def test_login_itself_rejects_cross_origin_form_submission(auth_client):
    response = auth_client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert response.status_code == 403


def test_rate_limit_applies_before_password_verification(signed_in):
    for _ in range(10):
        assert _login(signed_in, password="wrong").status_code == 401
    response = _login(signed_in)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "900"


def test_password_change_revokes_other_devices_and_tokens(signed_in):
    old_cookie = signed_in.cookies.get(COOKIE)
    with sqlite3.connect(settings.data_dir / "auth.db") as conn:
        api_token = _issue_session(conn, "api")
    new_password = "a-different-test-password"
    response = signed_in.post(
        "/api/auth/password",
        headers=HEADERS,
        json={
            "current_password": PASSWORD,
            "new_password": new_password,
        },
    )
    assert response.status_code == 200
    assert signed_in.get("/api/projects").status_code == 200
    assert (
        signed_in.get("/api/projects", headers={"Cookie": f"{COOKIE}={old_cookie}"}).status_code
        == 401
    )
    assert (
        signed_in.get("/api/projects", headers={"Authorization": f"Bearer {api_token}"}).status_code
        == 401
    )
    assert _login(signed_in, password=PASSWORD).status_code == 401
    assert _login(signed_in, password=new_password).status_code == 200


def test_wrong_current_password_does_not_change_account(signed_in):
    response = signed_in.post(
        "/api/auth/password",
        headers=HEADERS,
        json={
            "current_password": "wrong",
            "new_password": "another-test-password",
        },
    )
    assert response.status_code == 401
    assert signed_in.get("/api/projects").status_code == 200
    assert _login(signed_in).status_code == 200


def test_mcp_tokens_are_separate_from_browser_cookies(signed_in):
    browser_cookie = signed_in.cookies.get(COOKIE)
    with sqlite3.connect(settings.data_dir / "auth.db") as conn:
        api_token = _issue_session(conn, "api")
    assert (
        signed_in.get(
            "/api/projects", headers={"Authorization": f"Bearer {browser_cookie}"}
        ).status_code
        == 401
    )
    assert (
        signed_in.get("/api/projects", headers={"Cookie": f"{COOKIE}={api_token}"}).status_code
        == 401
    )
    headers = {"Authorization": f"Bearer {api_token}"}
    assert (
        signed_in.post("/api/projects", headers=headers, json={"title": "API client"}).status_code
        == 200
    )


def test_query_parameters_cannot_authenticate(signed_in):
    token = signed_in.cookies.get(COOKIE)
    signed_in.cookies.clear()
    assert signed_in.get(f"/api/projects?token={token}").status_code == 401


def test_remote_plain_http_is_rejected(auth_client):
    assert auth_client.get("http://public.example/api/auth/status").status_code == 403


def test_large_auth_requests_are_rejected_before_parsing(auth_client):
    response = auth_client.post("/api/auth/login", headers=HEADERS, content=b"x" * 5000)
    assert response.status_code == 413


@pytest.mark.parametrize("backlog", [True, False])
def test_event_stream_stops_when_session_is_revoked(monkeypatch, backlog):
    from calliope.routers import events

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(events, "current_user", lambda request: None)
    unsubscribed = []

    class Request:
        async def is_disconnected(self):
            return False

    async def scenario():
        q = asyncio.Queue()
        q.put_nowait({"type": "private", "data": {"secret": "hidden"}})

        async def subscribe(**kwargs):
            return q, [{"type": "private"}] if backlog else []

        async def unsubscribe(queue):
            unsubscribed.append(queue)

        monkeypatch.setattr(events.event_bus, "subscribe", subscribe)
        monkeypatch.setattr(events.event_bus, "unsubscribe", unsubscribe)
        response = await events.events_stream(Request())
        return [chunk async for chunk in response.body_iterator]

    assert asyncio.run(scenario()) == []
    assert len(unsubscribed) == 1
