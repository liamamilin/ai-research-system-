"""Pydantic schemas for the web API."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# ----- Auth -----


class LoginRequest(BaseModel):
    username: str
    password: str
    # Ask for the longer session. The password itself is never stored anywhere.
    remember: bool = False


class UserOut(BaseModel):
    id: int
    username: str
    role: Literal["admin", "editor", "viewer"]
    created_at: Optional[str] = None
    last_login_at: Optional[str] = None
    disabled: bool = False


class TokenResponse(BaseModel):
    user: UserOut
    csrf_token: str


# ----- Errors -----


class ApiError(BaseModel):
    error: Dict[str, Any]

    @classmethod
    def make(cls, code: str, message: str, details: Optional[dict] = None) -> dict:
        return {"error": {"code": code, "message": message, "details": details or {}}}


# ----- Jobs -----


class JobState(BaseModel):
    job_name: str
    last_run_at: Optional[str] = None
    last_status: Optional[str] = None
    last_output: Optional[str] = None
    last_error: Optional[str] = None
    last_duration_seconds: Optional[float] = None
    last_usage: Optional[Dict[str, Any]] = None


class JobSummary(BaseModel):
    name: str
    description: str = ""
    category: str = ""
    enabled: bool = True
    keywords: List[str] = Field(default_factory=list)
    output_template: str = ""
    state: Optional[JobState] = None
    is_running: bool = False
    # When the YAML was last written: the only honest "when was this created or
    # changed" signal, since jobs carry no created_at.
    file_mtime: Optional[float] = None


class JobDetail(JobSummary):
    yaml_content: str
    yaml_mtime: float
    file_path: str
    prompt: str = ""
    language: str = "zh"
    timeout_seconds: Optional[int] = None
    schedule: Optional[dict] = None


class JobYamlUpdate(BaseModel):
    yaml_content: str
    expected_mtime: Optional[float] = None  # for conflict detection
