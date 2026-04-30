#!/usr/bin/env python3
"""对话改写管理器：支持学习对话开关与自然英语对话。"""
import json
import os
import re
from pathlib import Path

import requests
from word_manager import list_words

project_root = Path(__file__).parent
state_file = project_root / "data" / "chat_state.json"
llm_config_file = project_root / "llm_config.json"
LOCAL_GENERATION_MODEL = "MiniMax-M2.7-highspeed"

LLM_SYSTEM_PROMPT = """You are an English conversation partner for a Chinese learner.

For every user turn, you must do all three things:
1. Rewrite the user's message into more natural, native-like English while preserving the original meaning.
2. Reply naturally in English to the user's actual meaning.
3. Ask one short follow-up question in English to continue the conversation.

Rules:
- Always rewrite the user's message first, whether it is a question, an opinion, or a fragment.
- Never ignore the user's actual meaning.
- Never switch into generic writing-coach mode unless the user explicitly asks for writing feedback.
- The reply should sound like a normal helpful conversation, not a speaking test template.
- If the user message is short or fragmentary, use the recent conversation context to infer the intended meaning.
- In every turn, guide the learner to use vocabulary from their personal word database in the next reply.
- If the vocabulary does not fit the current topic naturally, ask the learner to answer normally and then include one short extra example sentence with one target word.
- Keep the rewrite concise.
- Keep the reply practical and natural.
- Keep the follow-up question short and relevant.
- Return strict JSON only with keys: paraphrase, reply, follow_up.
"""


def _load_state() -> dict:
    if not state_file.exists():
        return {"chat_mode": False, "history": []}
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        history = data.get("history")
        if not isinstance(history, list):
            history = []
        return {
            "chat_mode": bool(data.get("chat_mode", False)),
            "history": history,
        }
    except Exception:
        return {"chat_mode": False, "history": []}


def _save_state(data: dict) -> None:
    _ensure_state_dir()
    payload = {
        "chat_mode": bool(data.get("chat_mode", False)),
        "history": data.get("history", []),
    }
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _append_history(role: str, text: str) -> None:
    state = _load_state()
    history = state.get("history", [])
    history.append({"role": role, "text": text.strip()})
    state["history"] = history[-12:]
    _save_state(state)


def _recent_user_messages(limit: int = 3) -> list[str]:
    history = _load_state().get("history", [])
    items = [item.get("text", "") for item in history if item.get("role") == "user"]
    return [item for item in items[-limit:] if item.strip()]


def _recent_user_context(limit: int = 3) -> str:
    messages = _recent_user_messages(limit)
    if not messages:
        return ""

    lines = ["Recent user context, from older to newer:"]
    for item in messages:
        lines.append(f"- {item}")
    lines.append("Use this context only to interpret the current turn if it is short or fragmentary.")
    return "\n".join(lines)


def _is_grammar_context() -> bool:
    recent = " ".join(_recent_user_messages(4)).lower()
    grammar_markers = [
        "grammar", "tense", "tenses", "preposition", "prepositions",
        "clause", "clauses", "verb", "verbs", "past", "present", "future",
    ]
    return any(marker in recent for marker in grammar_markers)


def _normalize_chat_input(user_text: str) -> str:
    text = re.sub(r"\s+", " ", user_text.strip())
    lowered = text.lower()

    if _is_grammar_context():
        lowered = re.sub(r"\btens\b", "tenses", lowered)
        if lowered != text.lower():
            return lowered

    return text


def _ensure_state_dir() -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)


def set_chat_mode(enabled: bool) -> None:
    state = _load_state()
    state["chat_mode"] = enabled
    state["history"] = []
    _save_state(state)


def is_chat_mode_enabled() -> bool:
    return bool(_load_state().get("chat_mode", False))


def _pick_upgrade_words(limit: int = 3) -> list[dict]:
    items = list_words(40)
    selected = []
    seen = set()

    for item in items:
        word = (item.get("word") or "").strip().lower()
        if not word or len(word) < 5 or word in seen:
            continue
        selected.append({
            "word": word,
            "definition": (item.get("definition") or "").strip(),
        })
        seen.add(word)
        if len(selected) >= limit:
            break

    return selected


def _format_vocab_prompt(vocab_items: list[dict]) -> str:
    if not vocab_items:
        return "No learner vocabulary is available for this turn."

    lines = ["Use these vocabulary words from the learner's database when you guide the next turn:"]
    for item in vocab_items:
        word = item["word"]
        definition = item.get("definition")
        if definition:
            lines.append(f"- {word}: {definition}")
        else:
            lines.append(f"- {word}")
    lines.append("In follow_up, explicitly invite the learner to use at least one of these words in the next reply.")
    return "\n".join(lines)


def _build_vocab_guidance(vocab_items: list[dict]) -> str:
    words = [item["word"] for item in vocab_items if item.get("word")]
    if not words:
        return ""
    if len(words) == 1:
        phrase = words[0]
    elif len(words) == 2:
        phrase = f"{words[0]} or {words[1]}"
    else:
        phrase = f"{words[0]}, {words[1]}, or {words[2]}"
    return (
        f"Try to use {phrase} in your next reply. "
        "If those words do not fit the topic, answer normally and add one short extra example sentence with one of them."
    )


def _ensure_vocab_guidance(text: str, vocab_items: list[dict]) -> str:
    guidance = _build_vocab_guidance(vocab_items)
    if not guidance:
        return text.strip()

    lowered = text.lower()
    vocab_words = [item["word"].lower() for item in vocab_items if item.get("word")]
    if any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in vocab_words):
        return text.strip()

    if not text.strip():
        return guidance
    return f"{text.strip()} {guidance}"


def _load_llm_settings() -> tuple[str, str, str]:
    base_url = os.getenv("CHAT_LLM_BASE_URL", "").strip()
    api_key = os.getenv("CHAT_LLM_API_KEY", "").strip()
    model = os.getenv("CHAT_LLM_MODEL", "").strip()

    if llm_config_file.exists():
        try:
            with open(llm_config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            base_url = base_url or str(data.get("base_url") or data.get("article_base_url") or "").strip()
            api_key = api_key or str(data.get("api_key") or data.get("article_api_key") or "").strip()
            model = model or str(data.get("model") or data.get("article_model") or LOCAL_GENERATION_MODEL).strip()
        except Exception:
            pass

    base_url = base_url or os.getenv("LLM_BASE_URL", "").strip()
    api_key = api_key or os.getenv("LLM_API_KEY", "").strip()
    model = model or os.getenv("LLM_MODEL", "").strip() or LOCAL_GENERATION_MODEL
    model = LOCAL_GENERATION_MODEL
    return base_url, api_key, model


def _build_chat_completions_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _strip_llm_artifacts(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _parse_llm_json(text: str) -> dict | None:
    cleaned = _strip_llm_artifacts(text)
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
        labeled = {}
        for key in ("paraphrase", "reply", "follow_up"):
            key_match = re.search(rf"{key}\s*:\s*(.+?)(?=\n(?:paraphrase|reply|follow_up)\s*:|$)", cleaned, flags=re.IGNORECASE | re.DOTALL)
            if key_match:
                labeled[key] = key_match.group(1).strip()
        return labeled or None


def _request_chat_completion(url: str, headers: dict, payload: dict) -> dict | None:
    response = requests.post(url, headers=headers, json=payload, timeout=45)
    if response.status_code != 200:
        return None
    content = response.json()["choices"][0]["message"]["content"]
    return _parse_llm_json(content)


def _call_chat_llm(user_text: str, vocab_items: list[dict]) -> dict | None:
    base_url, api_key, model = _load_llm_settings()
    url = _build_chat_completions_url(base_url)
    if not url or not api_key or not model:
        return None

    messages = [{"role": "system", "content": LLM_SYSTEM_PROMPT}]
    messages.append({"role": "system", "content": _format_vocab_prompt(vocab_items)})
    recent_context = _recent_user_context()
    if recent_context:
        messages.append({"role": "system", "content": recent_context})
    messages.append({"role": "user", "content": user_text})

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 500,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        parsed = _request_chat_completion(url, headers, payload)
        if not parsed:
            retry_payload = {
                "model": model,
                "messages": messages + [{
                    "role": "system",
                    "content": "Return valid JSON only. Do not add markdown fences or extra commentary.",
                }],
                "temperature": 0.2,
                "max_tokens": 500,
            }
            parsed = _request_chat_completion(url, headers, retry_payload)
        if not parsed:
            return None
        paraphrase = str(parsed.get("paraphrase", "")).strip()
        reply = str(parsed.get("reply", "")).strip()
        follow_up = str(parsed.get("follow_up", "")).strip()
        if not reply and not follow_up:
            return None
        return {
            "paraphrase": paraphrase,
            "reply": reply,
            "follow_up": follow_up,
        }
    except Exception:
        return None


def _normalize_user_sentence(user_text: str) -> str:
    text = re.sub(r"\s+", " ", user_text.strip())
    if not text:
        return "Please give me one English sentence first."

    if text[-1] not in ".!?":
        if text.lower().startswith(("how", "why", "what", "when", "where", "who", "can ", "could ", "should ", "is ", "are ", "do ", "does ", "did ")):
            text += "?"
        else:
            text += "."

    return text[0].upper() + text[1:]


def _contextualize_paraphrase(user_text: str) -> str:
    text = user_text.strip()
    if not text:
        return "Please give me one English sentence first."

    normalized = _normalize_user_sentence(text)
    lowered = text.lower().strip()
    previous_user_messages = _recent_user_messages(2)
    previous_full = previous_user_messages[-1] if previous_user_messages else ""

    if lowered.startswith("because "):
        if previous_full:
            base = _normalize_user_sentence(previous_full)
            base = re.sub(r"[.?!]+$", "", base)
            return f"{base} because {text[8:].strip()}." 
        return f"One reason is that {text[8:].strip()}."

    if lowered.startswith("what about "):
        topic = text[11:].strip()
        if topic.lower() == "tens":
            topic = "tenses"
        return f"What about {topic}?"

    if lowered.startswith("how about "):
        topic = text[10:].strip()
        return f"How about {topic}?"

    if lowered.startswith("so "):
        return f"So, {text[3:].strip()}."

    if lowered.startswith("and "):
        return f"In addition, {text[4:].strip()}."

    if lowered.startswith("but "):
        return f"However, {text[4:].strip()}."

    if lowered.startswith("also "):
        return f"Also, {text[5:].strip()}."

    if re.fullmatch(r"[a-zA-Z\s']+", text) and len(text.split()) <= 6 and previous_full and not lowered.endswith("?"):
        base = _normalize_user_sentence(previous_full)
        base = re.sub(r"[.?!]+$", "", base)
        return f"{base}, and {text.strip()}."

    return normalized


def _fallback_reply(user_text: str, paraphrase: str, vocab_items: list[dict]) -> tuple[str, str]:
    lowered = user_text.lower().strip()

    if "tense" in lowered or "tenses" in lowered:
        reply, follow_up = (
            "Tenses are much easier if you practice them in small groups instead of trying to memorize everything at once. "
            "Start with present simple, past simple, and present perfect, then compare them with short examples from your own life. "
            "For example: 'I go to class every day,' 'I went to class yesterday,' and 'I have gone to class three times this week.' "
            "That kind of contrast helps you feel the difference instead of only remembering rules.",
            "Which tense confuses you most right now: present perfect, past simple, or future forms?"
        )
        return reply, _ensure_vocab_guidance(follow_up, vocab_items)

    if "grammar" in lowered:
        reply, follow_up = (
            "A practical way to practice grammar is to study one pattern at a time, make your own example sentences, and then use that pattern in a short paragraph or voice message. "
            "It works better than memorizing rules alone because you start to notice how the pattern is actually used.",
            "Which grammar point feels hardest for you right now: tenses, clauses, or prepositions?"
        )
        return reply, _ensure_vocab_guidance(follow_up, vocab_items)

    if "listening" in lowered:
        reply, follow_up = (
            "A good method is to use short audio clips, listen once for the main idea, then listen again and check the transcript. "
            "After that, shadow the speaker for one or two minutes so your ear becomes more sensitive to natural rhythm.",
            "What is harder for you in listening: speed, pronunciation, or unknown words?"
        )
        return reply, _ensure_vocab_guidance(follow_up, vocab_items)

    if re.match(r"^(how|what|why|when|where|who|can|could|should|do|does|did|is|are)\b", lowered):
        reply, follow_up = (
            "A useful approach is to practice a little every day, pay attention to patterns, and immediately use what you learn in your own speaking or writing. "
            "That usually helps the language feel more natural much faster.",
            "What part do you want to improve first?"
        )
        return reply, _ensure_vocab_guidance(follow_up, vocab_items)

    if lowered.startswith(("because ", "and ", "but ", "so ", "also ")):
        reply, follow_up = (
            "That makes sense. Your idea is clear, and adding one specific example would make it sound even more convincing.",
            "Can you give me one concrete example?"
        )
        return reply, _ensure_vocab_guidance(follow_up, vocab_items)

    reply, follow_up = (
        f"That sounds natural. Building on that idea, you can make your English stronger by saying the same point with one more detail or example.",
        "Can you tell me a little more about that?"
    )
    return reply, _ensure_vocab_guidance(follow_up, vocab_items)


def _compose_response(paraphrase: str, reply: str, follow_up: str) -> str:
    return (
        f"Do you mean: {paraphrase}\n"
        f"{reply}\n"
        f"{follow_up}"
    )


def rewrite_sentence(user_text: str) -> str:
    """每轮都先做更地道的英文改写，再自然回复并继续追问。"""
    user_text = user_text.strip()
    if not user_text:
        return "Please send me one sentence in English first."

    if not re.search(r"[A-Za-z]", user_text):
        return "Please keep chatting with me in English. Send me one English sentence and I will refine it and continue the conversation."

    normalized_user_text = _normalize_chat_input(user_text)
    vocab_items = _pick_upgrade_words()
    fallback_paraphrase = _contextualize_paraphrase(normalized_user_text)
    llm_result = _call_chat_llm(normalized_user_text, vocab_items)

    if llm_result:
        paraphrase = llm_result.get("paraphrase") or fallback_paraphrase
        reply = (llm_result.get("reply") or "").strip()
        follow_up = (llm_result.get("follow_up") or "").strip()
        if not reply or not follow_up:
            reply, follow_up = _fallback_reply(normalized_user_text, paraphrase, vocab_items)
    else:
        paraphrase = fallback_paraphrase
        reply, follow_up = _fallback_reply(normalized_user_text, paraphrase, vocab_items)

    follow_up = _ensure_vocab_guidance(follow_up, vocab_items)
    response_text = _compose_response(paraphrase, reply, follow_up)

    _append_history("user", user_text)
    _append_history("assistant", response_text)

    return response_text
