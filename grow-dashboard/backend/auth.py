"""Google Workspace OAuth login, gated to a single allowed domain.

Replaces "you're logged into Claude Cowork" with a normal login: anyone with a
Google account on the configured Workspace domain (GOOGLE_ALLOWED_DOMAIN) can
sign in; everyone else is rejected. Session state is a signed cookie
(SessionMiddleware, itsdangerous) — no separate user database needed.
"""

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from .config import get_settings

router = APIRouter()

_settings = get_settings()

oauth = OAuth()
oauth.register(
    name="google",
    client_id=_settings.google_client_id,
    client_secret=_settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


@router.get("/auth/login")
async def login(request: Request):
    return await oauth.google.authorize_redirect(request, _settings.oauth_redirect_url)


@router.get("/auth/callback")
async def callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as exc:  # noqa: BLE001 - surface auth failures plainly
        raise HTTPException(status_code=401, detail=f"OAuth error: {exc}") from exc

    userinfo = token.get("userinfo") or {}
    hd = userinfo.get("hd")
    email = userinfo.get("email")

    if hd != _settings.google_allowed_domain:
        raise HTTPException(
            status_code=403,
            detail=f"Access restricted to @{_settings.google_allowed_domain} accounts",
        )

    request.session["user"] = {"email": email, "name": userinfo.get("name")}
    return RedirectResponse(url="/")


@router.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")


@router.get("/auth/me")
async def me(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    return user


def require_user(request: Request) -> dict:
    """FastAPI dependency — raises 401 if there's no authenticated session."""
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    return user
