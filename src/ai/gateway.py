"""Small OpenAI-compatible HTTP client with no SDK dependency."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]


class AIServiceError(RuntimeError):
    """Raised when a configured model endpoint cannot complete a request."""


def _load_local_env() -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = ROOT / ".env"
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class AISettings:
    mode: str
    base_url: str
    api_key: str
    chat_model: str
    embedding_model: str
    timeout_seconds: float
    json_mode: str

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "AISettings":
        local_values = _load_local_env() if environ is None else {}
        source = dict(local_values)
        source.update(dict(os.environ if environ is None else environ))
        api_key = source.get("OPENAI_API_KEY", "").strip()
        mode = source.get("ALPHALENS_AI_MODE", "api" if api_key else "off").strip().lower()
        if mode not in {"off", "api", "local"}:
            mode = "off"
        json_mode = source.get("ALPHALENS_AI_JSON_MODE", "schema").strip().lower()
        if json_mode not in {"schema", "object"}:
            json_mode = "schema"
        return cls(
            mode=mode,
            base_url=source.get("ALPHALENS_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            api_key=api_key,
            chat_model=source.get("ALPHALENS_LLM_MODEL", "gpt-5-mini").strip(),
            embedding_model=source.get("ALPHALENS_EMBEDDING_MODEL", "text-embedding-3-small").strip(),
            timeout_seconds=float(source.get("ALPHALENS_AI_TIMEOUT", "45")),
            json_mode=json_mode,
        )

    @property
    def enabled(self) -> bool:
        has_models = bool(self.base_url and self.chat_model and self.embedding_model)
        return has_models and (self.mode == "local" or (self.mode == "api" and bool(self.api_key)))

    @property
    def provider(self) -> str:
        return "local-openai-compatible" if self.mode == "local" else "openai-compatible-api"

    def public_status(self) -> dict[str, Any]:
        reason = ""
        if not self.enabled:
            reason = "AI_MODE=off 或未配置 OPENAI_API_KEY"
        return {
            "configured": self.enabled,
            "mode": self.mode,
            "provider": self.provider,
            "base_url": self.base_url,
            "chat_model": self.chat_model,
            "embedding_model": self.embedding_model,
            "structured_output": self.json_mode in {"schema", "object"},
            "reason": reason,
        }


class OpenAICompatibleGateway:
    def __init__(self, settings: AISettings | None = None) -> None:
        self.settings = settings or AISettings.from_environment()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.enabled:
            raise AIServiceError("模型网关尚未配置")
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        request = Request(
            f"{self.settings.base_url}/{path.lstrip('/')}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise AIServiceError(f"模型接口 HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise AIServiceError(f"模型接口不可用: {exc}") from exc

    def chat_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        schema_name: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        response_format: dict[str, Any]
        if self.settings.json_mode == "object":
            response_format = {"type": "json_object"}
        else:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            }
        payload = {
            "model": self.settings.chat_model,
            "messages": messages,
            "response_format": response_format,
        }
        try:
            response = self._post("chat/completions", payload)
        except AIServiceError as exc:
            if response_format["type"] != "json_schema" or "HTTP 4" not in str(exc):
                raise
            response_format = {"type": "json_object"}
            payload["response_format"] = response_format
            response = self._post("chat/completions", payload)
        try:
            content = response["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
            parsed = _parse_json_content(str(content))
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AIServiceError("模型未返回可解析的结构化 JSON") from exc
        metadata = {
            "request_id": response.get("id", ""),
            "model": response.get("model", self.settings.chat_model),
            "usage": response.get("usage", {}),
            "response_format": response_format["type"],
        }
        return parsed, metadata

    def embeddings(self, inputs: list[str]) -> tuple[list[list[float]], dict[str, Any]]:
        response = self._post(
            "embeddings",
            {"model": self.settings.embedding_model, "input": inputs},
        )
        try:
            ordered = sorted(response["data"], key=lambda item: int(item["index"]))
            vectors = [[float(value) for value in item["embedding"]] for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise AIServiceError("Embedding 接口返回格式不合法") from exc
        if len(vectors) != len(inputs):
            raise AIServiceError("Embedding 返回数量与输入数量不一致")
        return vectors, {
            "model": response.get("model", self.settings.embedding_model),
            "usage": response.get("usage", {}),
        }


def _parse_json_content(content: str) -> dict[str, Any]:
    value = content.strip()
    if value.startswith("```"):
        first_newline = value.find("\n")
        value = value[first_newline + 1 :] if first_newline >= 0 else value[3:]
        if value.endswith("```"):
            value = value[:-3]
    parsed = json.loads(value.strip())
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError("expected JSON object", value, 0)
    return parsed
