from __future__ import annotations

import json
import os
from typing import Any, Dict

from fastapi import FastAPI, Header, HTTPException

from robotos.control.api.app import ControlAPI


DEFAULT_ROLE_POLICIES = {
    "admin": {"create", "submit_intent", "cancel", "pause", "resume", "preempt", "get_session"},
    "operator": {"create", "submit_intent", "cancel", "pause", "resume", "get_session"},
    "viewer": {"get_session"},
}


def _load_auth_table() -> Dict[str, Dict[str, Any]]:
    """Load API key table from env; fallback to safe local default."""
    raw = os.getenv("ROBOTOS_API_KEYS")
    if raw:
        data = json.loads(raw)
        return {str(k): {"actor": str(v.get("actor", "unknown")), "role": str(v.get("role", "viewer"))} for k, v in data.items()}
    default = os.getenv("ROBOTOS_API_TOKEN", "dev-token")
    return {default: {"actor": "local-dev", "role": "admin"}}


def _authorize(api_key: str | None, action: str) -> str:
    table = _load_auth_table()
    token = (api_key or "").strip()
    user = table.get(token)
    if not user:
        raise HTTPException(status_code=401, detail="invalid api key")
    role = user.get("role", "viewer")
    allowed = DEFAULT_ROLE_POLICIES.get(role, {"get_session"})
    if action not in allowed:
        raise HTTPException(status_code=403, detail=f"role {role} cannot perform {action}")
    return str(user.get("actor", "unknown"))


def build_fastapi(control: ControlAPI) -> FastAPI:
    app = FastAPI(title="RobotOS Control API", version="1.1")

    @app.post("/v1/sessions")
    def create_session(body: Dict[str, Any], x_api_key: str | None = Header(default=None)) -> Dict[str, Any]:
        actor = _authorize(x_api_key, "create")
        return control.post_sessions(body, actor=actor)

    @app.post("/v1/sessions/{session_id}/submit_intent")
    def submit_intent(session_id: str, body: Dict[str, Any], x_api_key: str | None = Header(default=None)) -> Dict[str, str]:
        actor = _authorize(x_api_key, "submit_intent")
        return control.post_submit_intent(session_id, body, actor=actor)

    @app.post("/v1/sessions/{session_id}/cancel")
    def cancel(session_id: str, x_api_key: str | None = Header(default=None)) -> Dict[str, str]:
        actor = _authorize(x_api_key, "cancel")
        return control.post_cancel(session_id, actor=actor)

    @app.post("/v1/sessions/{session_id}/pause")
    def pause(session_id: str, x_api_key: str | None = Header(default=None)) -> Dict[str, str]:
        actor = _authorize(x_api_key, "pause")
        return control.post_pause(session_id, actor=actor)

    @app.post("/v1/sessions/{session_id}/resume")
    def resume(session_id: str, x_api_key: str | None = Header(default=None)) -> Dict[str, str]:
        actor = _authorize(x_api_key, "resume")
        return control.post_resume(session_id, actor=actor)

    @app.post("/v1/preempt")
    def preempt(body: Dict[str, Any], x_api_key: str | None = Header(default=None)) -> Dict[str, str]:
        actor = _authorize(x_api_key, "preempt")
        return control.post_preempt(body, actor=actor)

    @app.get("/v1/sessions/{session_id}")
    def get_session(session_id: str, x_api_key: str | None = Header(default=None)) -> Dict[str, Any]:
        _authorize(x_api_key, "get_session")
        return control.get_session(session_id)

    return app
