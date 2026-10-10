"""Optional Google sign-in limited to one email domain. Off when no client id is configured."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from .settings import Settings

OPEN_PATHS = {"/login", "/auth/callback", "/logout", "/healthz"}


def current_user(request: Request) -> str:
    return (request.scope.get("session") or {}).get("user", {}).get("email", "")


def install_auth(app: FastAPI, settings: Settings) -> None:
    if not settings.google_client_id:
        return
    if not settings.google_client_secret or not settings.session_secret:
        raise ValueError("ASA_GOOGLE_CLIENT_SECRET and ASA_SESSION_SECRET are required when sign-in is enabled.")
    from authlib.integrations.starlette_client import OAuth

    oauth = OAuth()
    oauth.register(
        "google", client_id=settings.google_client_id, client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"})
    domain = settings.allowed_domain.lower()

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        if request.url.path in OPEN_PATHS or current_user(request):
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Not signed in"}, status_code=401)
        return RedirectResponse("/login")

    # Added after require_login so it runs first and the session is available there.
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")

    @app.get("/login")
    async def login(request: Request):
        return await oauth.google.authorize_redirect(request, str(request.url_for("auth_callback")), hd=domain)

    @app.get("/auth/callback", name="auth_callback")
    async def auth_callback(request: Request):
        token = await oauth.google.authorize_access_token(request)
        info = token.get("userinfo") or {}
        email = (info.get("email") or "").lower()
        if not info.get("email_verified") or not email.endswith("@" + domain):
            return HTMLResponse(f"Access is limited to @{domain} accounts.", status_code=403)
        request.session["user"] = {"email": email, "name": info.get("name", "")}
        return RedirectResponse("/")

    @app.get("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login")
