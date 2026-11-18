"""环境配置。所有可调项集中在这里。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

PROVIDERS = ("openai", "anthropic")
DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "deepseek-v4.1-flash-1017",
}


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    projects_root: Path
    styles_root: Path
    db_path: Path
    # OpenAI 兼容
    api_key: str
    base_url: str
    # Anthropic 兼容
    anthropic_token: str
    anthropic_base_url: str
    anthropic_version: str
    provider: str
    model: str
    author: str
    default_theme: str
    target_chars: int
    max_review_rounds: int
    demo: bool
    seed: bool
    host: str
    port: int
    request_timeout: float
    max_context_chars: int
    max_read_bytes: int
    max_image_bytes: int
    max_upload_bytes: int
    log_level: str

    @property
    def provider_key(self) -> str:
        return self.anthropic_token if self.provider == "anthropic" else self.api_key

    @property
    def demo_mode(self) -> bool:
        return self.demo or not bool(self.provider_key)


def load_settings(data_dir: str | Path | None = None) -> Settings:
    load_dotenv(ROOT / ".env")
    raw_data = data_dir or os.environ.get("GZH_DATA_DIR") or str(ROOT / "workspace")
    data_path = Path(raw_data).expanduser()
    data = (data_path if data_path.is_absolute() else ROOT / data_path).resolve()
    projects_path = Path(os.environ.get("GZH_PROJECTS_ROOT", data / "wechat-projects")).expanduser()
    styles_path = Path(os.environ.get("GZH_STYLES_ROOT", data / "styles")).expanduser()
    db_path = Path(os.environ.get("GZH_DB", data / "gzh.sqlite")).expanduser()
    projects = (projects_path if projects_path.is_absolute() else ROOT / projects_path).resolve()
    styles = (styles_path if styles_path.is_absolute() else ROOT / styles_path).resolve()
    db = (db_path if db_path.is_absolute() else ROOT / db_path).resolve()

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    anthropic_token = (os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    anthropic_base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    anthropic_version = os.environ.get("ANTHROPIC_VERSION", "2023-06-01")

    # 显式 GZH_PROVIDER 优先；否则有 Anthropic token 用 anthropic，再否则 openai
    provider = os.environ.get("GZH_PROVIDER", "").strip().lower()
    if provider not in PROVIDERS:
        provider = "anthropic" if anthropic_token and not api_key else "openai"
    provider_configured = anthropic_token if provider == "anthropic" else api_key
    explicit_demo = _bool(os.environ.get("GZH_DEMO"), False)

    model = os.environ.get("GZH_MODEL", "").strip() or DEFAULT_MODELS[provider]

    return Settings(
        data_dir=data,
        projects_root=projects,
        styles_root=styles,
        db_path=db,
        api_key=api_key,
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        anthropic_token=anthropic_token,
        anthropic_base_url=anthropic_base,
        anthropic_version=anthropic_version,
        provider=provider,
        model=model,
        author=os.environ.get("GZH_AUTHOR", ""),
        default_theme=os.environ.get("GZH_THEME", "wechat"),
        target_chars=int(os.environ.get("GZH_TARGET_CHARS", "2800")),
        max_review_rounds=max(1, int(os.environ.get("GZH_MAX_REVIEW_ROUNDS", "3"))),
        demo=explicit_demo or not bool(provider_configured),
        seed=_bool(os.environ.get("GZH_SEED"), True),
        host=os.environ.get("GZH_HOST", "127.0.0.1"),
        port=int(os.environ.get("GZH_PORT", "8765")),
        request_timeout=float(os.environ.get("GZH_REQUEST_TIMEOUT", "180")),
        max_context_chars=max(4000, int(os.environ.get("GZH_MAX_CONTEXT_CHARS", "48000"))),
        max_read_bytes=max(4096, int(os.environ.get("GZH_MAX_READ_BYTES", str(512 * 1024)))),
        max_image_bytes=10 * 1024 * 1024,
        max_upload_bytes=20 * 1024 * 1024,
        log_level=os.environ.get("GZH_LOG_LEVEL", "INFO").upper(),
    )
