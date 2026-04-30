#!/usr/bin/env python3
"""统一的 minimax LLM 客户端，被对话、写作、口语、测验、周报共用。"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable

import requests

_project_root = Path(__file__).resolve().parent.parent
_llm_config_file = _project_root / "llm_config.json"
LOCAL_GENERATION_MODEL = "MiniMax-M2.7-highspeed"


def _load_llm_settings() -> tuple[str, str, str]:
    base_url = os.getenv("CHAT_LLM_BASE_URL", "").strip()
    api_key = os.getenv("CHAT_LLM_API_KEY", "").strip()
    model = os.getenv("CHAT_LLM_MODEL", "").strip()

    if _llm_config_file.exists():
        try:
            with open(_llm_config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            base_url = base_url or str(data.get("base_url") or data.get("article_base_url") or "").strip()
            api_key = api_key or str(data.get("api_key") or data.get("article_api_key") or "").strip()
            model = model or str(data.get("model") or data.get("article_model") or LOCAL_GENERATION_MODEL).strip()
        except Exception:
            pass

    base_url = base_url or os.getenv("LLM_BASE_URL", "").strip()
    api_key = api_key or os.getenv("LLM_API_KEY", "").strip()
    model = model or os.getenv("LLM_MODEL", "").strip() or LOCAL_GENERATION_MODEL
    # 按项目约定统一本地生成模型，避免不同模块模型漂移导致体验不一致。
    model = LOCAL_GENERATION_MODEL
    return base_url, api_key, model


def _build_chat_completions_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _strip_artifacts(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def parse_json_response(text: str) -> dict | None:
    cleaned = _strip_artifacts(text)
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def is_configured() -> bool:
    base_url, api_key, model = _load_llm_settings()
    return bool(base_url and api_key and model)


def chat_completion(
    messages: Iterable[dict],
    *,
    temperature: float = 0.4,
    max_tokens: int = 800,
    force_json: bool = False,
    timeout: int = 60,
) -> str | None:
    base_url, api_key, model = _load_llm_settings()
    url = _build_chat_completions_url(base_url)
    if not url or not api_key or not model:
        return None

    payload = {
        "model": model,
        "messages": list(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if force_json:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if response.status_code != 200:
            return None
        return response.json()["choices"][0]["message"]["content"]
    except Exception:
        return None


def chat_json(
    messages: Iterable[dict],
    *,
    temperature: float = 0.4,
    max_tokens: int = 800,
    timeout: int = 60,
    retries: int = 1,
) -> dict | None:
    """调用 LLM 并强制要求 JSON 返回，失败时会做一次降级重试。"""
    raw = chat_completion(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        force_json=True,
        timeout=timeout,
    )
    if raw:
        parsed = parse_json_response(raw)
        if parsed:
            return parsed

    for _ in range(retries):
        hardened = list(messages) + [
            {
                "role": "system",
                "content": "Return valid JSON only. No markdown fences, no commentary.",
            }
        ]
        raw = chat_completion(
            hardened,
            temperature=max(0.2, temperature - 0.2),
            max_tokens=max_tokens,
            force_json=False,
            timeout=timeout,
        )
        if raw:
            parsed = parse_json_response(raw)
            if parsed:
                return parsed

    return None
