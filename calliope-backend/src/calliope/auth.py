"""Installation-wide administrator login; credentials never travel with projects."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import logging
import secrets
import sqlite3
import time
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from calliope.config import settings

router = APIRouter()
COOKIE = "calliope_session"
REQUEST_HEADER = "X-Calliope-Request"
SESSION_SECONDS = 12 * 3600
IDLE_SECONDS = 3600
TOKEN_SECONDS = 30 * 24 * 3600
RATE_WINDOW = 15 * 60
RATE_LIMIT = 10
PUBLIC_PATHS = frozenset({"/api/auth/status", "/api/auth/login", "/api/auth/setup", "/api/health"})
SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    username TEXT NOT NULL, password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, kind TEXT NOT NULL,
    created_at INTEGER NOT NULL, last_seen INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    key TEXT PRIMARY KEY, started_at INTEGER NOT NULL, count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bootstrap (
    id INTEGER PRIMARY KEY CHECK (id = 1), token_hash TEXT NOT NULL
);
"""


@contextmanager
def _db():
    conn = sqlite3.connect(settings.data_dir / "auth.db", timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=16384, r=8, p=5, maxmem=64 * 1024 * 1024
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt, expected = encoded.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=16384,
            r=8,
            p=5,
            maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def initialize_auth() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with _db() as conn:
        conn.executescript(SCHEMA)
        (settings.data_dir / "auth.db").chmod(0o600)
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM account").fetchone():
            conn.commit()
            return
        token_file = settings.data_dir / "auth-setup-code.txt"
        token = token_file.read_text(encoding="utf-8").strip() if token_file.exists() else ""
        if not token:
            token = secrets.token_urlsafe(32)
            token_file.write_text(token + "\n", encoding="utf-8")
            token_file.chmod(0o600)
        conn.execute("INSERT OR REPLACE INTO bootstrap VALUES (1, ?)", (_digest(token),))
        conn.commit()
    logging.getLogger("calliope.auth").info(
        "Create your administrator account in the browser. Setup code: %s",
        token_file.resolve(),
    )


def _check_attempt(request: Request, action: str) -> None:
    # Do not trust forwarded addresses supplied by unauthenticated clients.
    address = request.client.host if request.client else "unknown"
    key, now = f"{action}:{address}", int(time.time())
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM attempts WHERE started_at <= ?", (now - RATE_WINDOW,))
        row = conn.execute("SELECT count FROM attempts WHERE key = ?", (key,)).fetchone()
        if row and row["count"] >= RATE_LIMIT:
            raise HTTPException(
                429,
                "Too many attempts. Try again in 15 minutes.",
                headers={"Retry-After": str(RATE_WINDOW)},
            )
        conn.execute(
            "INSERT INTO attempts VALUES (?, ?, 1) "
            "ON CONFLICT(key) DO UPDATE SET count = count + 1",
            (key, now),
        )
        conn.commit()


def _issue_session(conn, kind: str = "browser") -> str:
    token, now = secrets.token_urlsafe(32), int(time.time())
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
        (
            _digest(token),
            kind,
            now,
            now,
            now + (TOKEN_SECONDS if kind == "api" else SESSION_SECONDS),
        ),
    )
    return token


def current_user(request: StarletteRequest, *, touch: bool = False) -> dict | None:
    authorization = request.headers.get("authorization", "")
    bearer = authorization.startswith("Bearer ")
    token = authorization[7:] if bearer else request.cookies.get(COOKIE)
    if not token or len(token) > 256:
        return None
    now = int(time.time())
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE token_hash = ?", (_digest(token),)
        ).fetchone()
        if not row or row["kind"] != ("api" if bearer else "browser"):
            return None
        if row["expires_at"] <= now or (not bearer and row["last_seen"] + IDLE_SECONDS <= now):
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_digest(token),))
            conn.commit()
            return None
        account = conn.execute("SELECT username FROM account WHERE id = 1").fetchone()
        if not account:
            return None
        if touch and not bearer:
            conn.execute(
                "UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, _digest(token))
            )
            conn.commit()
        return {"username": account["username"]}


def _cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        COOKIE,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.auth_secure_cookie or request.url.scheme == "https",
    )
    response.headers["Cache-Control"] = "no-store"


def _csrf_allowed(request: StarletteRequest) -> bool:
    if request.headers.get(REQUEST_HEADER) != "1":
        return False
    origin = request.headers.get("origin")
    same_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
    return origin is None or origin == same_origin or origin in settings.auth_allowed_origins


class AuthMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not settings.auth_enabled:
            await self.app(scope, receive, send)
            return
        request = StarletteRequest(scope, receive)
        path = request.url.path
        protected = (
            path == "/api"
            or path.startswith("/api/")
            or path in {"/mcp", "/docs", "/redoc", "/openapi.json"}
            or path.startswith("/mcp/")
        )
        if not protected or request.method == "OPTIONS":
            await self.app(scope, receive, send)
            return
        if (
            settings.auth_secure_cookie
            and request.url.scheme != "https"
            and request.url.hostname not in {"localhost", "127.0.0.1", "::1"}
        ):
            await JSONResponse({"detail": "Use HTTPS to access Calliope"}, 403)(
                scope, receive, send
            )
            return
        public = path in PUBLIC_PATHS
        user = None if public else await run_in_threadpool(current_user, request, touch=True)
        if not public and not user:
            await JSONResponse(
                {"detail": "Authentication required"}, 401, headers={"Cache-Control": "no-store"}
            )(scope, receive, send)
            return
        bearer = request.headers.get("authorization", "").startswith("Bearer ")
        if request.method not in {"GET", "HEAD"} and not bearer and not _csrf_allowed(request):
            await JSONResponse({"detail": "Invalid request origin"}, 403)(scope, receive, send)
            return
        if path.startswith("/api/auth/") and request.method == "POST":
            try:
                size = int(request.headers.get("content-length", "-1"))
            except ValueError:
                size = -1
            if not 0 <= size <= 4096:
                await JSONResponse({"detail": "Invalid authentication request size"}, 413)(
                    scope, receive, send
                )
                return
        scope.setdefault("state", {})["auth_user"] = user

        async def no_cache(message):
            if message["type"] == "http.response.start":
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"
                ]
                message = {**message, "headers": [*headers, (b"cache-control", b"no-store")]}
            await send(message)

        await self.app(scope, receive, no_cache)


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=1, max_length=512)


class Setup(Credentials):
    setup_code: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=12, max_length=512)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=12, max_length=512)


@router.get("/status")
def status(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    if not settings.auth_enabled:
        return {"enabled": False, "setup_required": False, "user": None}
    with _db() as conn:
        configured = bool(conn.execute("SELECT 1 FROM account").fetchone())
    return {"enabled": True, "setup_required": not configured, "user": current_user(request)}


@router.post("/setup")
def setup(payload: Setup, request: Request, response: Response) -> dict:
    if not settings.auth_enabled:
        raise HTTPException(404, "Authentication is disabled")
    _check_attempt(request, "setup")
    password_hash = hash_password(payload.password)
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        bootstrap = conn.execute("SELECT token_hash FROM bootstrap WHERE id = 1").fetchone()
        if conn.execute("SELECT 1 FROM account").fetchone() or not bootstrap:
            raise HTTPException(409, "An administrator account already exists")
        if not hmac.compare_digest(bootstrap["token_hash"], _digest(payload.setup_code.strip())):
            raise HTTPException(401, "Invalid setup code")
        conn.execute("INSERT INTO account VALUES (1, ?, ?)", (payload.username, password_hash))
        conn.execute("DELETE FROM bootstrap")
        token = _issue_session(conn)
        conn.commit()
    (settings.data_dir / "auth-setup-code.txt").unlink(missing_ok=True)
    _cookie(response, token, request)
    return {"username": payload.username}


@router.post("/login")
def login(payload: Credentials, request: Request, response: Response) -> dict:
    if not settings.auth_enabled:
        raise HTTPException(404, "Authentication is disabled")
    _check_attempt(request, "login")
    with _db() as conn:
        account = conn.execute("SELECT * FROM account WHERE id = 1").fetchone()
        dummy = "scrypt$" + "00" * 16 + "$" + "00" * 64
        valid = verify_password(payload.password, account["password_hash"] if account else dummy)
        if (
            not valid
            or not account
            or not hmac.compare_digest(payload.username.encode(), account["username"].encode())
        ):
            raise HTTPException(401, "Invalid username or password")
        # Login always creates a new session; never adopt a caller-supplied token.
        old_token = request.cookies.get(COOKIE)
        if old_token:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_digest(old_token),))
        token = _issue_session(conn)
        conn.commit()
    _cookie(response, token, request)
    return {"username": account["username"]}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(COOKIE)
    if token:
        with _db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_digest(token),))
            conn.commit()
    response.delete_cookie(
        COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.auth_secure_cookie or request.url.scheme == "https",
    )
    return {"ok": True}


@router.post("/password")
def change_password(payload: PasswordChange, request: Request, response: Response) -> dict:
    if not settings.auth_enabled or not getattr(request.state, "auth_user", None):
        raise HTTPException(401, "Authentication required")
    _check_attempt(request, "password")
    with _db() as conn:
        account = conn.execute("SELECT * FROM account WHERE id = 1").fetchone()
        if not account or not verify_password(payload.current_password, account["password_hash"]):
            raise HTTPException(401, "Invalid current password")
        conn.execute(
            "UPDATE account SET password_hash = ? WHERE id = 1",
            (hash_password(payload.new_password),),
        )
        conn.execute("DELETE FROM sessions")
        token = _issue_session(conn)
        conn.commit()
    _cookie(response, token, request)
    return {"ok": True}


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Calliope's administrator account locally")
    parser.add_argument("command", choices=["setup-code", "reset-password", "token"])
    args = parser.parse_args()
    initialize_auth()
    if args.command == "setup-code":
        path = settings.data_dir / "auth-setup-code.txt"
        if not path.exists():
            parser.error("Account already configured. Use reset-password if needed.")
        print(path.read_text(encoding="utf-8").strip())
        return
    with _db() as conn:
        account = conn.execute("SELECT * FROM account WHERE id = 1").fetchone()
        if not account:
            parser.error("Create the administrator account in the browser first.")
        if args.command == "reset-password":
            password = getpass.getpass("New password (at least 12 characters): ")
            confirmation = getpass.getpass("Repeat new password: ")
            if password != confirmation or not 12 <= len(password) <= 512:
                parser.error("Passwords must match and contain 12–512 characters.")
            conn.execute(
                "UPDATE account SET password_hash = ? WHERE id = 1", (hash_password(password),)
            )
            conn.execute("DELETE FROM sessions")
            conn.commit()
            print(f"Password reset for {account['username']}; all sessions and API tokens revoked.")
        else:
            token = _issue_session(conn, "api")
            conn.commit()
            print(token)


if __name__ == "__main__":
    main()
