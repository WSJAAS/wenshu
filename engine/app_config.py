from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import json
import os
from pathlib import Path
from typing import Any

from aigc_round_service import normalize_prompt_profile

APP_DIR_NAME = "BaibaiAIGC"
CONFIG_FILE_NAME = "config.json"
SECRET_MARKER = "dpapi:v1:"

DEFAULT_MODEL_CONFIG = {
    "baseUrl": "",
    "apiKey": "",
    "model": "",
    "temperature": 0.7,
    "offlineMode": False,
    "promptProfile": "cn",
}


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi_available() -> bool:
    return os.name == "nt"


def _protect(plaintext: str) -> str | None:
    """用 Windows DPAPI 加密（绑定当前用户），失败时返回 None。"""
    if not _dpapi_available() or not plaintext:
        return None
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        buffer = ctypes.create_string_buffer(plaintext.encode("utf-8"))
        blob_in = _DataBlob(len(plaintext.encode("utf-8")), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = _DataBlob()

        if not crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            return None

        try:
            encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)

        return SECRET_MARKER + base64.b64encode(encrypted).decode("ascii")
    except Exception:
        return None


def _unprotect(stored: str) -> str:
    """解密 DPAPI 密文；遇到明文（历史配置）原样返回。"""
    if not stored.startswith(SECRET_MARKER):
        return stored
    if not _dpapi_available():
        return ""
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        encrypted = base64.b64decode(stored[len(SECRET_MARKER):])
        buffer = ctypes.create_string_buffer(encrypted)
        blob_in = _DataBlob(len(encrypted), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = _DataBlob()

        if not crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            return ""

        try:
            decrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)

        return decrypted.decode("utf-8", "replace")
    except Exception:
        return ""


def get_app_config_dir() -> Path:
    base_dir = os.getenv("APPDATA")
    if base_dir:
        return Path(base_dir) / APP_DIR_NAME
    return Path.home() / ".baibaiaigc"


def get_app_config_path() -> Path:
    return get_app_config_dir() / CONFIG_FILE_NAME


def normalize_model_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = config or {}
    base_url = str(payload.get("baseUrl", DEFAULT_MODEL_CONFIG["baseUrl"])).strip()
    api_key = str(payload.get("apiKey", DEFAULT_MODEL_CONFIG["apiKey"])).strip()
    model = str(payload.get("model", DEFAULT_MODEL_CONFIG["model"])).strip()
    try:
        temperature = float(payload.get("temperature", DEFAULT_MODEL_CONFIG["temperature"]))
    except (TypeError, ValueError):
        temperature = float(DEFAULT_MODEL_CONFIG["temperature"])

    return {
        "baseUrl": base_url,
        "apiKey": api_key,
        "model": model,
        "temperature": temperature,
        "offlineMode": bool(payload.get("offlineMode", DEFAULT_MODEL_CONFIG["offlineMode"])),
        "promptProfile": normalize_prompt_profile(payload.get("promptProfile", DEFAULT_MODEL_CONFIG["promptProfile"])),
    }


def load_app_config() -> dict[str, Any]:
    path = get_app_config_path()
    if not path.exists():
        return dict(DEFAULT_MODEL_CONFIG)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return dict(DEFAULT_MODEL_CONFIG)
    if not raw.strip():
        return dict(DEFAULT_MODEL_CONFIG)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return dict(DEFAULT_MODEL_CONFIG)
    if not isinstance(data, dict):
        return dict(DEFAULT_MODEL_CONFIG)

    if isinstance(data.get("apiKey"), str) and data["apiKey"].startswith(SECRET_MARKER):
        data["apiKey"] = _unprotect(data["apiKey"])
    return normalize_model_config(data)


def save_app_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_model_config(config)
    path = get_app_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    stored = dict(normalized)
    encrypted = _protect(normalized["apiKey"]) if normalized["apiKey"] else None
    if encrypted:
        stored["apiKey"] = encrypted
    # DPAPI 不可用时退化为明文，并在配置里显式标注，避免用户误以为已加密。
    stored["apiKeyStorage"] = "dpapi" if encrypted else ("plaintext" if normalized["apiKey"] else "none")

    path.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized
