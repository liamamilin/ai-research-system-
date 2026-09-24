"""Web UI configuration loader using pydantic-settings."""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from typing import List, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


# Load .env file if present
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

ENVIRONMENTS = ("development", "staging", "production")

MIN_SECRET_LENGTH = 32
WEAK_SECRETS = {
    "CHANGE_ME",
    "change-me-to-a-long-random-string",
    "dev-only-change-me-in-production-aaaaaaaaaaaaaaaaaa",
}


def is_weak_secret(secret: str) -> bool:
    """True when a JWT secret is a known placeholder or too weak to trust."""
    value = (secret or "").strip()
    if not value:
        return True
    if value.lower() in {s.lower() for s in WEAK_SECRETS}:
        return True
    if len(value) < MIN_SECRET_LENGTH:
        return True
    # Reject low-entropy padding such as "aaaa...".
    return len(set(value)) < 8


def generate_secret() -> str:
    return secrets.token_urlsafe(48)


def _persist_secret(secret: str) -> Optional[str]:
    """Write the generated secret to .env (0600). Returns the path or None."""
    path = Path(__file__).resolve().parent.parent / ".env"
    try:
        lines: List[str] = []
        if path.is_file():
            lines = path.read_text(encoding="utf-8").splitlines()
        replaced = False
        out: List[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("AI_RESEARCH_SECRET_KEY=", "WEB_SECRET_KEY=")):
                if not replaced:
                    out.append(f"AI_RESEARCH_SECRET_KEY={secret}")
                    replaced = True
                continue
            out.append(line)
        if not replaced:
            out.append(f"AI_RESEARCH_SECRET_KEY={secret}")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".env.tmp")
        tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        return str(path)
    except OSError:
        return None


def detect_environment() -> str:
    """Detect the current environment."""
    env = os.environ.get("AI_RESEARCH_ENV", "development")
    if env not in ENVIRONMENTS:
        print(f"  [config] Warning: unknown environment '{env}', falling back to development",
              file=sys.stderr)
        return "development"
    return env


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    base_url: str = "http://127.0.0.1:8765"


class AuthConfig(BaseModel):
    secret_key: str = "CHANGE_ME"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = False
    cookie_samesite: str = "strict"


class CorsConfig(BaseModel):
    enabled: bool = True
    origins: List[str] = Field(default_factory=lambda: ["http://localhost:5173"])


class RateLimitConfig(BaseModel):
    login_per_minute: int = 5
    job_run_per_minute: int = 10


class PathsConfig(BaseModel):
    jobs_dir: str = "jobs"
    config_dir: str = "config"
    output_dir: str = "output"
    state_dir: str = "state"
    logs_dir: str = "logs"


class WebSettings(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    cors: CorsConfig = Field(default_factory=CorsConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)

    @classmethod
    def load(cls, config_path: str | None = None) -> "WebSettings":
        """Load settings from YAML file. Env vars can override secret_key.

        Search order:
          1. Explicit config_path
          2. $AI_RESEARCH_WEB_CONFIG
          3. config/web.yaml

        Raises ValueError if critical settings are missing in production.
        """
        path = config_path or os.environ.get("AI_RESEARCH_WEB_CONFIG") or "config/web.yaml"
        data = {}
        if Path(path).is_file():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

        settings = cls(**data)
        env = detect_environment()

        # Env override for secrets (WEB_SECRET_KEY kept for older .env files)
        env_secret = (os.environ.get("AI_RESEARCH_SECRET_KEY")
                      or os.environ.get("WEB_SECRET_KEY"))
        if env_secret:
            settings.auth.secret_key = env_secret

        # Secret hygiene: a known/weak JWT secret lets anyone forge tokens, so
        # replace it with a strong generated one instead of running with it.
        if is_weak_secret(settings.auth.secret_key):
            generated = generate_secret()
            saved = _persist_secret(generated)
            settings.auth.secret_key = generated
            os.environ["AI_RESEARCH_SECRET_KEY"] = generated
            if saved:
                print("  [config] ⚠ 已检测到弱 JWT 密钥，已自动生成强密钥并写入 .env（0600）。"
                      "登录状态需重新登录。", file=sys.stderr)
            elif env == "production":
                raise RuntimeError(
                    "auth.secret_key 是弱密钥且无法写入 .env；"
                    "请设置 AI_RESEARCH_SECRET_KEY 环境变量后重启。"
                )
            else:
                print("  [config] ⚠ 弱 JWT 密钥已替换为临时强密钥（无法持久化，"
                      "服务重启后登录状态会失效）。", file=sys.stderr)

        # Production validation
        if env == "production":
            errors: list[str] = []
            if is_weak_secret(settings.auth.secret_key):
                errors.append("auth.secret_key 使用了不安全的默认值。设置 AI_RESEARCH_SECRET_KEY 环境变量。")
            if not settings.auth.cookie_secure:
                errors.append("auth.cookie_secure 应为 true（生产环境需 HTTPS）")
            if errors:
                print("\n  [config] ═══════════════════════════════════════", file=sys.stderr)
                print("  [config]  生产环境配置错误:", file=sys.stderr)
                for e in errors:
                    print(f"  [config]    ✗ {e}", file=sys.stderr)
                print("  [config] ═══════════════════════════════════════\n", file=sys.stderr)
                raise RuntimeError("生产环境配置校验失败：" + "; ".join(errors))

        return settings


_settings: WebSettings | None = None


def get_settings() -> WebSettings:
    """Return the singleton settings instance."""
    global _settings
    if _settings is None:
        _settings = WebSettings.load()
    return _settings


def reset_settings():
    """Reset the singleton (used in tests)."""
    global _settings
    _settings = None
