from __future__ import annotations

import os

# 在 import litellm 之前设置：跳过远端 model cost map 拉取。
# 否则每次启动都会尝试联网请求 GitHub，在无网或走代理的环境下刷出
# 一大段 WARNING，既拖慢启动也干扰用户判断服务是否正常。
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm
from litellm.types.utils import LlmProviders
from litellm.utils import ProviderConfigManager

litellm.drop_params = True


class LLMClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        stage: str,
        retriable: bool = False,
        provider_status: int | None = None,
        detail: str = "",
    ):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.retriable = retriable
        self.provider_status = provider_status
        self.detail = detail


def _raise_litellm_error(exc: Exception, *, stage: str) -> None:
    status_code = getattr(exc, "status_code", None)
    retriable = isinstance(status_code, int) and (status_code >= 500 or status_code == 429)
    raise LLMClientError(
        f"LLM request failed: {exc}",
        code="provider_http_error" if isinstance(status_code, int) else "provider_request_error",
        stage=stage,
        retriable=retriable,
        provider_status=status_code if isinstance(status_code, int) else None,
        detail=str(exc),
    ) from exc


def _resolve_provider_model(model: str) -> str:
    hint = model.strip()
    if not hint:
        return "openai/"
    if "/" in hint:
        return hint
    try:
        litellm.get_llm_provider(hint)
        return hint
    except Exception:
        return f"{hint}/"


def llm_completion(
    prompt: str,
    *,
    model: str,
    api_key: str,
    base_url: str,
    temperature: float = 0.7,
    timeout: int = 120,
) -> str:
    if "/" not in model.strip() and base_url.strip():
        model = f"openai/{model.strip()}"

    try:
        response = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            api_key=api_key or None,
            base_url=base_url.strip() or None,
            temperature=temperature,
            timeout=timeout,
        )
    except Exception as exc:
        _raise_litellm_error(exc, stage="llm_http")

    try:
        return response.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError) as exc:
        raise LLMClientError(
            f"Unexpected LLM response payload: {response}",
            code="provider_unexpected_schema",
            stage="llm_schema",
        ) from exc


def test_llm_connection(
    *,
    model: str,
    api_key: str,
    base_url: str,
    timeout: int = 20,
) -> dict[str, object]:
    llm_completion("ping", model=model, api_key=api_key, base_url=base_url, temperature=0, timeout=timeout)
    return {
        "ok": True,
        "endpoint": base_url,
        "model": model,
        "status": 200,
    }


def fetch_model_list(
    *,
    api_key: str,
    base_url: str,
    model: str,
) -> list[str]:
    model_hint = _resolve_provider_model(model)
    try:
        _, provider, _, _ = litellm.get_llm_provider(model_hint)
    except Exception as exc:
        _raise_litellm_error(exc, stage="llm_models")

    if provider == "openai":
        from openai import OpenAI

        try:
            client = OpenAI(api_key=api_key or None, base_url=base_url.strip() or None)
            return sorted({"openai/" + m.id for m in client.models.list().data})
        except Exception as exc:
            _raise_litellm_error(exc, stage="llm_models")

    config = ProviderConfigManager.get_provider_chat_config(model_hint, LlmProviders(provider))
    if config is None or not hasattr(config, "get_models"):
        raise LLMClientError(
            f"Model list is not supported for provider {provider}.",
            code="provider_models_unsupported",
            stage="llm_models",
        )

    try:
        models = config.get_models(api_key=api_key or None, api_base=base_url.strip() or None)
    except Exception as exc:
        _raise_litellm_error(exc, stage="llm_models")
    return sorted(set(models))


def read_api_config(
    api_key: str | None,
    model: str | None,
    base_url: str | None,
) -> tuple[str | None, str | None, str | None]:
    resolved_api_key = api_key or os.getenv("BAIBAIAIGC_API_KEY") or os.getenv("OPENAI_API_KEY")
    resolved_model = model or os.getenv("BAIBAIAIGC_MODEL")
    resolved_base_url = (
        base_url
        or os.getenv("BAIBAIAIGC_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
    )
    return resolved_api_key, resolved_model, resolved_base_url
