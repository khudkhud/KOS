from __future__ import annotations

from typing import Any, Dict

from fastapi import FastAPI

from robotos.control.api.app import ControlAPI


def build_fastapi(control: ControlAPI) -> FastAPI:
    app = FastAPI(title="RobotOS Control API", version="1.0")

    @app.post("/v1/sessions")
    def create_session(body: Dict[str, Any]) -> Dict[str, Any]:
        return control.post_sessions(body)

    @app.post("/v1/sessions/{session_id}/submit_intent")
    def submit_intent(session_id: str, body: Dict[str, Any]) -> Dict[str, str]:
        return control.post_submit_intent(session_id, body)

    @app.post("/v1/sessions/{session_id}/cancel")
    def cancel(session_id: str) -> Dict[str, str]:
        return control.post_cancel(session_id)

    @app.post("/v1/sessions/{session_id}/pause")
    def pause(session_id: str) -> Dict[str, str]:
        return control.post_pause(session_id)

    @app.post("/v1/sessions/{session_id}/resume")
    def resume(session_id: str) -> Dict[str, str]:
        return control.post_resume(session_id)

    @app.post("/v1/preempt")
    def preempt(body: Dict[str, Any]) -> Dict[str, str]:
        return control.post_preempt(body)

    @app.get("/v1/sessions/{session_id}")
    def get_session(session_id: str) -> Dict[str, Any]:
        return control.get_session(session_id)

    return app
