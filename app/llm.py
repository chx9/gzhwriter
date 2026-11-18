"""OpenAI-compatible 模型接入与演示模式调度。"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from .config import Settings
from .demo import DemoWriter
from .errors import ApiError
from .materials import load_content, material_context, read_refs
from .prompts import (
    REVIEW_ROLES,
    brief_prompt,
    clean_prompt,
    compare_prompt,
    digest_prompt,
    draft_prompt,
    edit_prompt,
    merge_review_prompt,
    outline_prompt,
    review_prompt,
    system_prompt,
    title_prompt,
)
from .storage import Project, char_count, read_text, truncate
from .styles import StyleStore
from .render.themes import ThemeLibrary


class LLMError(ApiError):
    def __init__(self, message: str, code: str = "LLM_ERROR") -> None:
        super().__init__(message, code, 502)


def _strip_fence(text: str) -> str:
    value = str(text or "").strip()
    match = re.match(r"^```(?:markdown|md|json)?\s*\n([\s\S]*?)\n```$", value, re.I)
    return match.group(1).strip() if match else value


class WriterEngine:
    def __init__(self, settings: Settings, styles: StyleStore, themes: ThemeLibrary) -> None:
        self.settings = settings
        self.styles = styles
        self.themes = themes
        self.demo = DemoWriter()
        self._client = None

    @property
    def demo_mode(self) -> bool:
        return self.settings.demo_mode

    def _client_or_raise(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise LLMError("没有安装 openai 包；请运行 pip install -e .", "NO_OPENAI") from exc
            self._client = OpenAI(
                api_key=self.settings.api_key,
                base_url=self.settings.base_url,
                timeout=self.settings.request_timeout,
                max_retries=2,
            )
        return self._client

    def _chat(self, messages: list[dict[str, str]], *, json_mode: bool = False, temperature: float = 0.7) -> str:
        if self.demo_mode:
            raise LLMError("演示模式不应调用真实模型", "DEMO")
        if self.settings.provider == "anthropic":
            return self._chat_anthropic(messages, temperature=temperature)
        client = self._client_or_raise()
        kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            try:
                response = client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001
                # 有些 OpenAI-compatible 接口不支持 response_format，退化为普通文本再解析。
                if json_mode and type(exc).__name__ == "BadRequestError":
                    kwargs.pop("response_format", None)
                    response = client.chat.completions.create(**kwargs)
                else:
                    raise
        except Exception as exc:  # noqa: BLE001 - 按异常类型映射可操作信息
            raise self._map_error(exc) from exc
        content = response.choices[0].message.content if response.choices else ""
        if not content:
            raise LLMError("模型返回了空内容", "EMPTY_RESPONSE")
        return _strip_fence(content)

    def _chat_anthropic(self, messages: list[dict[str, str]], *, temperature: float = 0.7) -> str:
        """Anthropic Messages API（/v1/messages）。system 单独提取，只取 text 块，忽略 thinking。

        max_tokens 默认取 GZH_MAX_TOKENS（默认 1,000,000）；如果网关/模型不接受这个上限，
        自动按 262144 → 131072 → … → 8192 依次降级重试。
        """
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        conversation = [m for m in messages if m.get("role") != "system"]
        base_tokens = max(1024, int(os.environ.get("GZH_MAX_TOKENS", "1000000")))
        candidates: list[int] = []
        for value in (base_tokens, 262144, 131072, 65536, 32768, 16384, 8192):
            if value <= base_tokens and value not in candidates:
                candidates.append(value)
        headers = {
            "Authorization": f"Bearer {self.settings.anthropic_token}",
            "x-api-key": self.settings.anthropic_token,
            "anthropic-version": self.settings.anthropic_version,
            "content-type": "application/json",
        }
        url = f"{self.settings.anthropic_base_url}/v1/messages"
        response = None
        used_tokens = candidates[0]
        for max_tokens in candidates:
            used_tokens = max_tokens
            payload: dict[str, Any] = {
                "model": self.settings.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": conversation,
            }
            if system_parts:
                payload["system"] = "\n\n".join(system_parts)
            try:
                response = httpx.post(url, headers=headers, json=payload, timeout=self.settings.request_timeout)
            except httpx.TimeoutException as exc:
                raise LLMError(f"模型请求超时（{self.settings.request_timeout:g}s）：可以调大 GZH_REQUEST_TIMEOUT。", "TIMEOUT") from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"连不上模型服务：检查 ANTHROPIC_BASE_URL（当前 {self.settings.anthropic_base_url}）和网络。", "CONNECTION") from exc
            if response.status_code == 400:
                detail = response.text.lower()
                if any(word in detail for word in ("max_tokens", "too large", "exceed", "maximum")):
                    continue  # 降级重试
            break
        assert response is not None
        if response.status_code in (401, 403):
            raise LLMError("Anthropic Token 无效或没有权限：检查 ANTHROPIC_AUTH_TOKEN。", "AUTH")
        if response.status_code == 404:
            raise LLMError(f"模型名或路径错误：检查 GZH_MODEL（当前 {self.settings.model}）和 ANTHROPIC_BASE_URL。", "MODEL_NOT_FOUND")
        if response.status_code == 429:
            raise LLMError("模型服务限流或额度不足，稍后再试。", "RATE_LIMIT")
        if response.status_code >= 400:
            detail = ""
            try:
                detail = str(response.json().get("error", {}).get("message", ""))[:200]
            except Exception:
                detail = response.text[:200]
            raise LLMError(f"模型调用失败：HTTP {response.status_code} {detail}", "LLM_ERROR")
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("模型返回了无法解析的响应", "BAD_RESPONSE") from exc
        types = [str(block.get("type")) for block in data.get("content", [])]
        texts = [str(block.get("text") or "") for block in data.get("content", []) if block.get("type") == "text"]
        content = "".join(texts).strip()
        if not content:
            raise LLMError(
                f"模型返回了空内容（blocks={types}，stop_reason={data.get('stop_reason')}，max_tokens={used_tokens}）："
                "thinking 可能占满了输出额度，调大 GZH_MAX_TOKENS 或调小要求。",
                "EMPTY_RESPONSE",
            )
        return _strip_fence(content)

    def _map_error(self, exc: Exception) -> LLMError:
        name = type(exc).__name__
        if name in {"AuthenticationError", "PermissionDeniedError"}:
            return LLMError("API Key 无效或没有权限：检查 OPENAI_API_KEY。", "AUTH")
        if name in {"APIConnectionError", "ConnectError"}:
            return LLMError(f"连不上模型服务：检查 OPENAI_BASE_URL（当前 {self.settings.base_url}）和网络。", "CONNECTION")
        if name == "NotFoundError":
            return LLMError(f"模型名或路径错误：检查 GZH_MODEL（当前 {self.settings.model}）和 Base URL。", "MODEL_NOT_FOUND")
        if name == "RateLimitError":
            return LLMError("模型服务限流或额度不足，稍后再试。", "RATE_LIMIT")
        if name in {"APITimeoutError", "TimeoutError"}:
            return LLMError(f"模型请求超时（{self.settings.request_timeout:g}s）：可以调大 GZH_REQUEST_TIMEOUT，或检查网络。", "TIMEOUT")
        if name == "BadRequestError":
            return LLMError(f"模型拒绝了请求：{exc}", "BAD_REQUEST")
        return LLMError(f"模型调用失败：{exc}", "LLM_ERROR")

    def _system(self, project: Project) -> str:
        directory = self.styles.ensure_session(project.slug)
        writing = self.styles.read(directory, "writing")["text"]
        layout = self.styles.read(directory, "layout")["text"]
        params = self.styles.layout_params(project.slug)
        theme_id = self.themes.effective_id(project.slug, params.get("legacyTheme"))
        theme = self.themes.resolve(theme_id, accent=params.get("accent"))
        return system_prompt(
            writing_style=writing,
            layout_style=layout,
            theme_name=f"{theme['id']}（{theme['meta'].get('name', theme['id'])}）",
            theme_guide=theme["meta"].get("guide", ""),
            original_mode=True,
            max_rounds=self.settings.max_review_rounds,
        )

    # ── high-level generation ───────────────────────────────────────

    def brief(self, project: Project, *, topic: str, goal: str, audience: str, target_chars: int, feedback: str = "") -> str:
        if self.demo_mode:
            return self.demo.brief(project, topic=topic, goal=goal, audience=audience, target_chars=target_chars)
        sources = material_context(project, max_chars=12000)
        return self._chat(
            [
                {"role": "system", "content": self._system(project)},
                *brief_prompt(
                    title=project.meta.get("title", ""),
                    topic=topic,
                    goal=goal,
                    audience=audience,
                    target_chars=target_chars,
                    sources=sources,
                    feedback=feedback,
                ),
            ]
        )

    def clean(self, project: Project, source: dict[str, Any], content: str) -> str:
        if self.demo_mode:
            return self.demo.clean(project, source, content)
        return self._chat(
            [
                {"role": "system", "content": self._system(project)},
                *clean_prompt(source, content[: self.settings.max_context_chars]),
            ],
            temperature=0.2,
        )

    def digest(self, project: Project, source: dict[str, Any]) -> str:
        if self.demo_mode:
            return self.demo.digest(project, source)
        paragraphs = load_content(project, source, "content") or []
        content = "\n\n".join(f"[{source.get('id')}¶{p['n']}] {p['text']}" for p in paragraphs)
        return self._chat(
            [
                {"role": "system", "content": self._system(project)},
                *digest_prompt(source, content[: self.settings.max_context_chars]),
            ],
            temperature=0.3,
        )

    def compare(self, project: Project, digests: list[str]) -> str:
        if self.demo_mode:
            return self.demo.compare(project)
        return self._chat(
            [
                {"role": "system", "content": self._system(project)},
                *compare_prompt("\n\n".join(digests)[: self.settings.max_context_chars]),
            ],
            temperature=0.3,
        )

    def outline(self, project: Project, *, brief: str, feedback: str = "") -> str:
        if self.demo_mode:
            return self.demo.outline(project, brief=brief, feedback=feedback)
        return self._chat(
            [
                {"role": "system", "content": self._system(project)},
                *outline_prompt(
                    brief=brief,
                    material_context=material_context(project, max_chars=self.settings.max_context_chars),
                    feedback=feedback,
                ),
            ]
        )

    def _snippets_for(self, project: Project, outline: str) -> str:
        refs = re.findall(r"\[([AS]\d{2,3}(?:¶\d+(?:-\d+)?|@\d{1,2}:\d{2}(?::\d{2})?(?:-\d{1,2}:\d{2}(?::\d{2})?)?))", outline)
        unique = list(dict.fromkeys(refs))[:40]
        if not unique:
            unique = [source.get("id", "") for source in project.meta.get("sources", [])[:3] if source.get("id")]
        try:
            result = read_refs(project, unique, context=1)
        except Exception:
            return ""
        parts = [item.get("text", "") for item in result if not item.get("error")]
        return "\n\n".join(parts)[: self.settings.max_context_chars]

    def draft(self, project: Project, *, brief: str, outline: str, feedback: str = "", existing: str = "") -> str:
        if self.demo_mode:
            return self.demo.draft(project, brief=brief, outline=outline, feedback=feedback, existing=existing)
        return self._chat(
            [
                {"role": "system", "content": self._system(project)},
                *draft_prompt(
                    brief=brief,
                    outline=outline,
                    snippets=self._snippets_for(project, outline),
                    feedback=feedback,
                    existing=existing[-self.settings.max_context_chars :] if existing else "",
                ),
            ],
            temperature=0.8,
        )

    def review(self, project: Project, *, article: str, brief: str) -> str:
        if self.demo_mode:
            return self.demo.review(project, article=article)
        style = self.styles.read(self.styles.ensure_session(project.slug), "writing")["text"]
        params = self.styles.layout_params(project.slug)
        theme = self.themes.resolve(
            self.themes.effective_id(project.slug, params.get("legacyTheme")), accent=params.get("accent")
        )
        theme_guide = f"{theme['id']}（{theme['meta'].get('name', theme['id'])}）\n{theme['meta'].get('guide', '')}"
        system = {"role": "system", "content": self._system(project)}
        outputs: list[str] = []
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=min(4, len(REVIEW_ROLES))) as pool:
            futures = {
                pool.submit(
                    self._chat,
                    [
                        system,
                        *review_prompt(
                            article=article,
                            brief=brief,
                            role=role,
                            focus=focus,
                            style=style,
                            theme=theme_guide,
                        ),
                    ],
                    temperature=0.4,
                ): role
                for role, focus in REVIEW_ROLES
            }
            for future in as_completed(futures):
                role = futures[future]
                try:
                    outputs.append(f"## {role}\n\n{future.result()}")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{role}: {exc}")
        if not outputs:
            raise LLMError("所有评审角色都失败了：" + "；".join(errors), "REVIEW_FAILED")
        merged = self._chat(
            [
                system,
                *merge_review_prompt("\n\n".join(outputs)[: self.settings.max_context_chars]),
            ],
            temperature=0.2,
        )
        if errors:
            merged += "\n\n> 部分评审角色失败：" + "；".join(errors)
        return merged

    def title(self, project: Project, *, article: str, brief: str) -> dict[str, Any]:
        if self.demo_mode:
            return self.demo.title(project, article=article, brief=brief)
        raw = self._chat(
            [
                {"role": "system", "content": self._system(project)},
                *title_prompt(article=article[-self.settings.max_context_chars :], brief=brief),
            ],
            json_mode=True,
            temperature=0.9,
        )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", raw)
            data = json.loads(match.group(0)) if match else {}
        titles = data.get("titles") or []
        normalized = []
        for item in titles[:5]:
            if isinstance(item, str):
                normalized.append({"title": item, "style": "", "reason": ""})
            elif isinstance(item, dict) and item.get("title"):
                normalized.append(
                    {
                        "title": str(item["title"]),
                        "style": str(item.get("style", "")),
                        "reason": str(item.get("reason", "")),
                    }
                )
        while len(normalized) < 5:
            normalized.append({"title": f"候选标题 {len(normalized) + 1}", "style": "", "reason": ""})
        digest = str(data.get("digest") or "")[:120]
        return {
            "titles": normalized,
            "digest": digest,
            "recommended": int(data.get("recommended") or 1),
            "reason": str(data.get("reason") or ""),
        }

    def edit(self, project: Project, *, article: str, instruction: str, selected: str = "", file_label: str = "article.md") -> str:
        if self.demo_mode:
            return self.demo.edit(project, article=article, instruction=instruction, selected=selected)
        return self._chat(
            [
                {"role": "system", "content": self._system(project)},
                *edit_prompt(article=article, instruction=instruction, selected=selected, file_label=file_label),
            ],
            temperature=0.6,
        )

    def health(self) -> dict[str, Any]:
        return {
            "demo": self.demo_mode,
            "provider": self.settings.provider,
            "model": None if self.demo_mode else self.settings.model,
            "baseUrl": None
            if self.demo_mode
            else (self.settings.anthropic_base_url if self.settings.provider == "anthropic" else self.settings.base_url),
            "maxTokens": int(os.environ.get("GZH_MAX_TOKENS", "1000000")),
        }
