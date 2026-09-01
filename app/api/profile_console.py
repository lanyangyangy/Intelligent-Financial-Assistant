from __future__ import annotations

import asyncio
from time import monotonic
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.common.response import ApiResponse
from app.common.security.auth import current_user
from app.common.security.roles import is_customer_user
from app.models.auth import User

router = APIRouter(prefix="/profile-console", tags=["profile-console"])
_SESSION_CACHE_TTL_SECONDS = 60.0


def _require_customer(user: User) -> None:
    if not is_customer_user(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="customer account required",
        )


async def _ensure_profile_console_customer(request: Request, user: User) -> dict[str, str]:
    _require_customer(user)
    settings = request.app.state.settings
    if not settings.profile_console_bridge_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="profile console bridge is not configured",
        )

    cache_key = str(user.id)
    now = monotonic()
    cached = getattr(request.app.state, "profile_console_session_cache", {}).get(cache_key)
    if cached and cached["expires_at"] > now:
        return cached["session"]

    cache_lock = getattr(request.app.state, "profile_console_session_cache_lock", None)
    if cache_lock is None:
        cache_lock = asyncio.Lock()
        request.app.state.profile_console_session_cache_lock = cache_lock
        request.app.state.profile_console_session_cache = {}

    async with cache_lock:
        now = monotonic()
        cache = request.app.state.profile_console_session_cache
        cached = cache.get(cache_key)
        if cached and cached["expires_at"] > now:
            return cached["session"]

        try:
            async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
                response = await client.post(
                    f"{settings.profile_console_base_url.rstrip('/')}/api/bridge/main-customer",
                    headers={"X-Internal-Bridge-Key": settings.profile_console_bridge_key},
                    json={
                        "main_user_id": str(user.id),
                        "display_name": user.display_name,
                        "role": "customer",
                    },
                )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="profile console service is unavailable",
            ) from exc

        try:
            payload = response.json()
            session = payload["data"]
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="profile console returned an invalid bridge response",
            ) from exc
        if not response.is_success or payload.get("code") != "SUCCESS":
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="profile console customer synchronization failed",
            )
        normalized_session = {
            "customer_id": str(session["customer_id"]),
            "login_name": str(session["login_name"]),
            "display_name": str(session["display_name"]),
            "user_role": "CUSTOMER",
            "auth_mode": "MAIN_APP_BRIDGE",
        }
        cache[cache_key] = {
            "session": normalized_session,
            "expires_at": monotonic() + _SESSION_CACHE_TTL_SECONDS,
        }
        return normalized_session


@router.get("/session", response_model=ApiResponse[dict[str, object]])
async def create_profile_console_session(
    request: Request,
    user: Annotated[User, Depends(current_user)],
) -> ApiResponse[dict[str, object]]:
    session = await _ensure_profile_console_customer(request, user)
    settings = request.app.state.settings
    return ApiResponse(
        data={
            "console_url": f"{settings.profile_console_base_url.rstrip('/')}/console?embedded=1",
            "api_base": settings.profile_console_bff_url.rstrip("/"),
            "session": session,
        }
    )


@router.get("/overview", response_model=ApiResponse[dict[str, object]])
async def get_profile_console_overview(
    request: Request,
    user: Annotated[User, Depends(current_user)],
) -> ApiResponse[dict[str, object]]:
    session = await _ensure_profile_console_customer(request, user)
    customer_id = session["customer_id"]
    settings = request.app.state.settings
    upstream_headers = {
        "X-User-Id": session["customer_id"],
        "X-User-Role": "CUSTOMER",
        "X-Customer-Id": session["customer_id"],
        "X-Trace-Id": request.headers.get("X-Trace-Id", "profile-console-overview"),
        "Accept": "application/json",
    }
    resources = {
        "pending_tasks": f"/api/risk/assessment-tasks/pending?customer_id={customer_id}",
        "onboarding": f"/api/customers/{customer_id}/onboarding-status",
        "profile": f"/api/profile/{customer_id}",
        "history": f"/api/profile/{customer_id}/history?page=1&page_size=20",
        "profile_data": f"/api/customers/{customer_id}/profile-data",
        "tags": f"/api/profile/{customer_id}/tags",
        "conflicts": f"/api/profile/{customer_id}/tag-conflicts",
        "portfolio": f"/api/customers/{customer_id}/portfolio",
        "basic_information": f"/api/customers/{customer_id}/basic-information",
        "products": f"/api/customers/{customer_id}/products",
    }

    async def fetch_resource(
        client: httpx.AsyncClient, name: str, path: str
    ) -> tuple[str, object | None, dict[str, object] | None]:
        try:
            response = await client.get(path, headers=upstream_headers)
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return name, None, {"status": 502, "code": "UPSTREAM_UNAVAILABLE"}
        if response.is_success and payload.get("code") == "SUCCESS":
            return name, payload.get("data"), None
        if name == "profile" and response.status_code == status.HTTP_404_NOT_FOUND:
            return name, None, None
        return name, None, {
            "status": response.status_code,
            "code": str(payload.get("code", "UPSTREAM_ERROR")),
        }

    try:
        async with httpx.AsyncClient(
            base_url=settings.profile_console_base_url.rstrip("/"), timeout=15.0, trust_env=False
        ) as client:
            loaded = await asyncio.gather(
                *(fetch_resource(client, name, path) for name, path in resources.items())
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="profile console service is unavailable",
        ) from exc

    overview: dict[str, object] = {}
    errors: dict[str, object] = {}
    for name, data, error in loaded:
        overview[name] = data
        if error is not None:
            errors[name] = error
    overview["errors"] = errors
    return ApiResponse(data=overview)


@router.get("/health")
async def profile_console_health(
    request: Request,
    user: Annotated[User, Depends(current_user)],
) -> Response:
    _require_customer(user)
    settings = request.app.state.settings
    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            upstream = await client.get(f"{settings.profile_console_base_url.rstrip('/')}/health")
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="profile console service is unavailable",
        ) from exc
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


@router.api_route(
    "/proxy/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def proxy_profile_console_request(
    path: str,
    request: Request,
    user: Annotated[User, Depends(current_user)],
) -> Response:
    if not path.startswith("api/") or path.startswith("api/bridge"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="route not found")

    session = await _ensure_profile_console_customer(request, user)
    forwarded_headers = {
        "X-User-Id": session["customer_id"],
        "X-User-Role": "CUSTOMER",
        "X-Customer-Id": session["customer_id"],
        "X-Trace-Id": request.headers.get("X-Trace-Id", "profile-console-bff"),
        "Accept": request.headers.get("Accept", "application/json"),
    }
    for header_name in ("Content-Type", "Idempotency-Key"):
        value = request.headers.get(header_name)
        if value:
            forwarded_headers[header_name] = value

    settings = request.app.state.settings
    try:
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
            upstream = await client.request(
                request.method,
                f"{settings.profile_console_base_url.rstrip('/')}/{path}",
                params=list(request.query_params.multi_items()),
                content=await request.body(),
                headers=forwarded_headers,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="profile console service is unavailable",
        ) from exc

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )
