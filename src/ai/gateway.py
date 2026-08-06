"""Small OpenAI-compatible HTTP client with no SDK dependency."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]


class AIServiceError(RuntimeError):
    """Raised when a configured model endpoint cannot complete a request."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _http_error_message(status_code: int, detail: str) -> str:
    provider_message = ""
    try:
        payload = json.loads(detail)
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        if isinstance(error, dict):
            provider_message = str(error.get("message", "")).strip()
        elif error:
            provider_message = str(error).strip()
    except json.JSONDecodeError:
        provider_message = detail.strip()

    common = {
        401: "DeepSeek API Key 无效或已失效，请重新复制平台中的 Key",
        402: "DeepSeek 账户余额不足，请充值后重试",
        403: "当前 API Key 没有该模型的访问权限",
        429: "DeepSeek 请求频率过高，请稍后重试",
    }
    message = common.get(status_code, f"DeepSeek 接口返回 HTTP {status_code}")
    return f"{message}：{provider_message[:300]}" if provider_message else message


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
        # AlphaLens 默认 DeepSeek；DEEPSEEK_API_KEY / ALPHALENS_* 环境变量可覆盖，
        # OPENAI_API_KEY 保留兼容（OpenAI 兼容协议）。
        api_key = (
            source.get("DEEPSEEK_API_KEY", "").strip()
            or source.get("OPENAI_API_KEY", "").strip()
        )
        mode = source.get("ALPHALENS_AI_MODE", "api" if api_key else "off").strip().lower()
        if mode not in {"off", "api", "local"}:
            mode = "off"
        json_mode = source.get("ALPHALENS_AI_JSON_MODE", "schema").strip().lower()
        if json_mode not in {"schema", "object"}:
            json_mode = "schema"
        return cls(
            mode=mode,
            base_url=source.get("ALPHALENS_LLM_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            api_key=api_key,
            chat_model=source.get("ALPHALENS_LLM_MODEL", "deepseek-v4-flash").strip(),
            embedding_model=source.get("ALPHALENS_EMBEDDING_MODEL", "").strip(),
            timeout_seconds=float(source.get("ALPHALENS_AI_TIMEOUT", "45")),
            json_mode=json_mode,
        )

    @property
    def enabled(self) -> bool:
        has_models = bool(self.base_url and self.chat_model)
        return has_models and (self.mode == "local" or (self.mode == "api" and bool(self.api_key)))

    @property
    def provider(self) -> str:
        if "api.deepseek.com" in self.base_url or self.chat_model.startswith("deepseek-"):
            return "deepseek"
        return "local-openai-compatible" if self.mode == "local" else "openai-compatible-api"

    def public_status(self) -> dict[str, Any]:
        reason = ""
        if not self.enabled:
            reason = "AI_MODE=off 或未配置 API Key"
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
            raise AIServiceError(_http_error_message(exc.code, detail), status_code=exc.code) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise AIServiceError(f"模型接口不可用: {exc}") from exc

    def _get(self, path: str) -> dict[str, Any]:
        if not self.settings.enabled:
            raise AIServiceError("模型网关尚未配置")
        headers = {"Accept": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        request = Request(
            f"{self.settings.base_url}/{path.lstrip('/')}",
            headers=headers,
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise AIServiceError(_http_error_message(exc.code, detail), status_code=exc.code) from exc
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
            "max_tokens": 8192,
            "stream": False,
        }
        if self.settings.provider == "deepseek":
            payload["thinking"] = {"type": "disabled"}
        try:
            response = self._post("chat/completions", payload)
        except AIServiceError as exc:
            if response_format["type"] != "json_schema" or not (
                exc.status_code is not None and 400 <= exc.status_code < 500
            ):
                raise
            response_format = {"type": "json_object"}
            payload["response_format"] = response_format
            response = self._post("chat/completions", payload)
        parsed: dict[str, Any] | None = None
        parse_error: Exception | None = None
        for attempt in range(2):
            try:
                content = response["choices"][0]["message"]["content"]
                if isinstance(content, list):
                    content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
                parsed = _parse_json_content(str(content))
                break
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                parse_error = exc
                if attempt == 0:
                    payload["messages"] = [
                        *messages,
                        {
                            "role": "user",
                            "content": "上一条输出为空或不是合法 JSON。请重新只输出一个完整 JSON object，不要输出其他文字。",
                        },
                    ]
                    response = self._post("chat/completions", payload)
        if parsed is None:
            raise AIServiceError("DeepSeek 连续两次未返回可解析的结构化 JSON，请重试") from parse_error
        metadata = {
            "request_id": response.get("id", ""),
            "model": response.get("model", self.settings.chat_model),
            "system_fingerprint": response.get("system_fingerprint", ""),
            "usage": response.get("usage", {}),
            "response_format": response_format["type"],
        }
        return parsed, metadata

    def embeddings(self, inputs: list[str]) -> tuple[list[list[float]], dict[str, Any]]:
        if not self.settings.embedding_model:
            raise AIServiceError("当前提供方未配置 Embedding 模型")
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

    def check_connection(self) -> dict[str, Any]:
        """Verify credentials, model entitlement and the model actually returned."""
        model_payload = self._get("models")
        models = model_payload.get("data", [])
        if not isinstance(models, list):
            raise AIServiceError("模型列表接口返回格式不合法")
        model_record = next(
            (
                row
                for row in models
                if isinstance(row, dict) and row.get("id") == self.settings.chat_model
            ),
            None,
        )
        if model_record is None:
            raise AIServiceError(
                f"当前 Key 的模型列表不包含 {self.settings.chat_model}，不能确认 Flash 权限",
                status_code=403,
            )
        payload: dict[str, Any] = {
            "model": self.settings.chat_model,
            "messages": [
                {"role": "system", "content": "只输出 JSON。"},
                {"role": "user", "content": '返回 {"ok": true}。'},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 16,
            "stream": False,
        }
        if self.settings.provider == "deepseek":
            payload["thinking"] = {"type": "disabled"}
        response = self._post("chat/completions", payload)
        returned_model = str(response.get("model", "")).strip()
        if returned_model != self.settings.chat_model:
            raise AIServiceError(
                f"请求模型为 {self.settings.chat_model}，接口实际返回 {returned_model or '未知模型'}，身份校验未通过"
            )
        return {
            "ok": True,
            "requested_model": self.settings.chat_model,
            "returned_model": returned_model,
            "owned_by": str(model_record.get("owned_by", "")),
            "request_id": response.get("id", ""),
            "system_fingerprint": response.get("system_fingerprint", ""),
            "base_url_host": urlparse(self.settings.base_url).netloc,
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
