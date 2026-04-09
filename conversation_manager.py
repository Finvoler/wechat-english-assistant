#!/usr/bin/env python3
"""对话改写管理器：支持学习对话开关与句子升级。"""
import json
from pathlib import Path

from word_manager import list_words

project_root = Path(__file__).parent
state_file = project_root / "data" / "chat_state.json"


def _ensure_state_dir() -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)


def set_chat_mode(enabled: bool) -> None:
    _ensure_state_dir()
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump({"chat_mode": enabled}, f, ensure_ascii=False, indent=2)


def is_chat_mode_enabled() -> bool:
    if not state_file.exists():
        return False
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("chat_mode", False))
    except Exception:
        return False


def _pick_upgrade_words(limit: int = 3) -> list[str]:
    items = list_words(20)
    words = [x["word"] for x in items if x.get("word")]
    return words[:limit]


def rewrite_sentence(user_text: str) -> str:
    """在无外部 LLM 时，提供稳定可用的本地改写模板。"""
    user_text = user_text.strip()
    if not user_text:
        return "你想说的是：请先输入一句英文。对吧？"

    upgrade_words = _pick_upgrade_words()
    if not upgrade_words:
        upgrade_words = ["sustainable", "cognizant", "paradigm"]

    enriched = (
        f"I mean that {user_text[0].lower() + user_text[1:] if len(user_text) > 1 else user_text.lower()}, "
        f"and this idea is {upgrade_words[0]} in a broader {upgrade_words[1]} learning {upgrade_words[2]} context."
    )

    return (
        f"你想说的是：{enriched} 对吧？\n"
        "继续这个话题：请用 2 句话解释一个托福常见社会议题（如教育公平/科技与隐私）。"
    )
