#!/usr/bin/env python3
"""OpenClaw command entry for English learning WeChat routing."""
import json
import os
import sys

from wechat_handler import handle_wechat_message


def _extract_message_from_json(payload: str) -> str:
    try:
        data = json.loads(payload)
    except Exception:
        return ""

    candidates = [
        data.get("message"),
        data.get("text"),
        data.get("content"),
        data.get("input"),
    ]

    event = data.get("event") or {}
    if isinstance(event, dict):
        candidates.extend([
            event.get("message"),
            event.get("text"),
            event.get("content"),
        ])

    for item in candidates:
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def _read_message() -> str:
    if len(sys.argv) > 1:
        return " ".join(sys.argv[1:]).strip()

    stdin_text = ""
    try:
        if not sys.stdin.isatty():
            stdin_text = sys.stdin.read().strip()
    except Exception:
        stdin_text = ""

    if stdin_text:
        parsed = _extract_message_from_json(stdin_text)
        return parsed or stdin_text

    for key in ["OPENCLAW_MESSAGE", "MESSAGE", "TEXT", "CONTENT"]:
        value = os.environ.get(key, "").strip()
        if value:
            return value

    return ""


def main() -> int:
    message = _read_message()
    if not message:
        return 0

    response = handle_wechat_message(message)
    if response:
        print(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
