#!/usr/bin/env python3
"""TOEFL 口语教练（按 2026 官方口语任务结构做文本版训练）。

官方页面当前强调两类 Speaking Task：Listen and Repeat、Take an Interview。
这里按微信语音转文字的现实输入方式，把听力原文与问题原文直接展示出来，
然后对用户的文字版口语回答做评分、讲解、范例和升级建议。
"""
from __future__ import annotations

import json
import random
import re
from difflib import SequenceMatcher
from datetime import datetime
from pathlib import Path

from database import SessionLocal
from models import SpeakingScore, StudyEvent
from services.llm_client import chat_json, is_configured


project_root = Path(__file__).resolve().parent.parent
speaking_state_file = project_root / "data" / "speaking_state.json"


SPEAKING_TASK_SPECS = {
    "listen_repeat": {
        "label": "Listen and Repeat",
        "label_cn": "听后复述",
        "prep_time": "建议 10-15 秒熟悉文本",
        "target_words": "8-28 words",
        "description": "聚焦准确复现听力内容，考察信息完整度、自然节奏和语言控制。",
        "min_words": 5,
    },
    "take_interview": {
        "label": "Take an Interview",
        "label_cn": "模拟采访回答",
        "prep_time": "建议 15 秒准备，45-60 秒作答",
        "target_words": "85-130 words",
        "description": "聚焦校园/学术语境下的即时表达，考察回应完整度、语言质量和组织能力。",
        "min_words": 35,
    },
}

TTS_BROWSER_NAME = "Microsoft Edge"
TTS_SITE_URL = "http://new.text-to-speech.cn/tts/?ref=tts"

SPEAKING_TASK_ALIASES = {
    "repeat": "listen_repeat",
    "listen": "listen_repeat",
    "listenrepeat": "listen_repeat",
    "listen_repeat": "listen_repeat",
    "听后复述": "listen_repeat",
    "复述": "listen_repeat",
    "interview": "take_interview",
    "takeinterview": "take_interview",
    "take_interview": "take_interview",
    "采访": "take_interview",
    "模拟采访": "take_interview",
    "口语采访": "take_interview",
}

SAMPLE_STYLE_ALIASES = {
    "保守版": "conservative",
    "稳健版": "conservative",
    "conservative": "conservative",
    "平衡版": "balanced",
    "balanced": "balanced",
    "默认": "balanced",
    "激进版": "aggressive",
    "进阶版": "aggressive",
    "aggressive": "aggressive",
}

SAMPLE_MODE_ALIASES = {
    "标准模式": "standard",
    "标准": "standard",
    "standard": "standard",
    "词库强化": "local_vocab",
    "词库": "local_vocab",
    "本地词库": "local_vocab",
    "local_vocab": "local_vocab",
}

DEFAULT_SAMPLE_STYLE = "balanced"
DEFAULT_SAMPLE_MODE = "standard"

SPEAKING_STYLE_PROFILES = {
    "conservative": {
        "label_cn": "保守版",
        "description": "优先稳、清楚、贴题，不为了炫技牺牲自然度。",
        "recommended_advanced_count": 2,
        "recommended_local_vocab_count": 1,
        "boosted_local_vocab_count": 2,
        "level_cn": "稳健高分表达",
        "target_density_cn": "保守版以清楚和自然为先，只放 1-2 处高级表达。",
        "ceiling_cn": "不要把口语说成背范文，句子宁可短一点，也要干净。",
        "lexical_targets": [
            "from a practical standpoint",
            "what matters most is",
            "that would make it easier to",
            "a realistic way to improve",
        ],
    },
    "balanced": {
        "label_cn": "平衡版",
        "description": "兼顾自然口语感和高分表达，是最接近考试场景的默认风格。",
        "recommended_advanced_count": 3,
        "recommended_local_vocab_count": 1,
        "boosted_local_vocab_count": 3,
        "level_cn": "成熟高分表达",
        "target_density_cn": "平衡版建议 2-3 处高分表达，同时保持像真实口语而不是朗读作文。",
        "ceiling_cn": "要显得熟练，但不能每句都过长，否则会像书面语。",
        "lexical_targets": [
            "from my perspective",
            "one immediate advantage is that",
            "the stronger argument is that",
            "in the long run",
            "that would be far more effective",
        ],
    },
    "aggressive": {
        "label_cn": "激进版",
        "description": "允许更强判断和更密实的句式升级，但仍要求像高分口语而不是写作朗读。",
        "recommended_advanced_count": 5,
        "recommended_local_vocab_count": 2,
        "boosted_local_vocab_count": 4,
        "level_cn": "强观点高阶表达",
        "target_density_cn": "激进版会主动拉高观点力度、词汇精度和句式层次。",
        "ceiling_cn": "再高级也必须保留口语呼吸感，不能变成 GRE 词汇堆砌。",
        "lexical_targets": [
            "I would strongly favor",
            "the central concern is not X but Y",
            "that trade-off is still worth making",
            "a far more convincing reason is that",
            "it ultimately comes down to",
        ],
    },
}

PROMPT_BANK = {
    "listen_repeat": [
        {
            "prompt_id": "repeat_biology_fieldwork",
            "topic_tags": ["biology", "education"],
            "topic_label_cn": "生物",
            "listening_transcript": "The biology department plans to expand its field research program next semester, so students will need more flexible lab schedules.",
            "question": "Repeat the sentence as accurately and naturally as possible. Keep the wording and meaning unchanged.",
        },
        {
            "prompt_id": "repeat_humanities_archive",
            "topic_tags": ["humanities", "history"],
            "topic_label_cn": "人文",
            "listening_transcript": "The history professor encouraged students to compare local archives with national records before drawing broad conclusions.",
            "question": "Repeat the sentence exactly, keeping the information complete and the rhythm natural.",
        },
        {
            "prompt_id": "repeat_humanities_museum",
            "topic_tags": ["humanities", "history"],
            "topic_label_cn": "人文",
            "listening_transcript": "The museum studies lecturer reminded students to examine regional artifacts alongside written evidence before forming historical interpretations.",
            "question": "Repeat the sentence accurately and keep the key academic relationships unchanged.",
        },
        {
            "prompt_id": "repeat_humanities_literature",
            "topic_tags": ["humanities", "literature"],
            "topic_label_cn": "人文",
            "listening_transcript": "The literature seminar asked students to trace recurring symbols across several poems before discussing the author's larger argument.",
            "question": "Repeat the sentence naturally without dropping any of the important analytical details.",
        },
        {
            "prompt_id": "repeat_technology_lab",
            "topic_tags": ["technology", "campus"],
            "topic_label_cn": "科技",
            "listening_transcript": "The engineering lab will test a new solar charging system this winter to see whether it can reduce overall energy costs.",
            "question": "Repeat the sentence as clearly as possible without dropping or changing key details.",
        },
        {
            "prompt_id": "repeat_policy_library",
            "topic_tags": ["policy", "campus"],
            "topic_label_cn": "校园政策",
            "listening_transcript": "The library plans to keep one quiet floor open later during exam week so students can study in a less distracting environment.",
            "question": "Repeat the sentence accurately and keep the idea fully intact.",
        },
        {
            "prompt_id": "repeat_transport_city",
            "topic_tags": ["transportation", "technology"],
            "topic_label_cn": "交通",
            "listening_transcript": "City planners believe the new electric shuttle route could shorten commute times for students who live off campus.",
            "question": "Repeat the sentence naturally and make sure all the important information is preserved.",
        },
    ],
    "take_interview": [
        {
            "prompt_id": "interview_biology_lab",
            "topic_tags": ["biology", "campus"],
            "topic_label_cn": "生物",
            "listening_transcript": "Interviewer: The university is discussing whether first-year biology majors should spend more time in fieldwork and less time in traditional indoor labs.",
            "question": "Question: Do you think increasing fieldwork would improve learning for biology students? Explain your opinion with specific reasons or examples.",
            "follow_up": ["your own learning preference", "one likely benefit or drawback"],
        },
        {
            "prompt_id": "interview_humanities_seminar",
            "topic_tags": ["humanities", "education"],
            "topic_label_cn": "人文",
            "listening_transcript": "Interviewer: Some humanities professors want to replace one weekly lecture with a discussion-based seminar so students can analyze readings more actively.",
            "question": "Question: Would that change make humanities classes more effective? Give clear reasons for your view.",
            "follow_up": ["classroom participation", "quality of analysis"],
        },
        {
            "prompt_id": "interview_technology_ai",
            "topic_tags": ["technology", "education"],
            "topic_label_cn": "科技",
            "listening_transcript": "Interviewer: The university is considering using AI feedback tools in speaking courses to give students faster pronunciation and fluency feedback.",
            "question": "Question: Should the university adopt AI feedback tools for speaking practice? Support your answer with reasons or examples.",
            "follow_up": ["learning efficiency", "one possible concern"],
        },
        {
            "prompt_id": "interview_technology_analytics",
            "topic_tags": ["technology", "education"],
            "topic_label_cn": "科技",
            "listening_transcript": "Interviewer: Some departments want to use learning analytics dashboards so students can monitor speaking progress, vocabulary growth, and recurring grammar problems in one place.",
            "question": "Question: Would those dashboards help students improve their spoken English more effectively? Explain your answer clearly.",
            "follow_up": ["self-monitoring", "motivation or pressure"],
        },
        {
            "prompt_id": "interview_policy_housing",
            "topic_tags": ["policy", "campus"],
            "topic_label_cn": "校园政策",
            "listening_transcript": "Interviewer: Campus housing staff are debating whether quiet hours in dormitories should start earlier during finals week.",
            "question": "Question: Do you support earlier quiet hours during finals week? Explain why or why not.",
            "follow_up": ["student routines", "a practical compromise"],
        },
        {
            "prompt_id": "interview_transport_green",
            "topic_tags": ["transportation", "technology"],
            "topic_label_cn": "交通",
            "listening_transcript": "Interviewer: The city near campus wants to replace some parking spaces with bike lanes and electric bus stops.",
            "question": "Question: In your opinion, is that a good idea for students? Explain your answer clearly.",
            "follow_up": ["daily convenience", "long-term impact"],
        },
    ],
}

TOPIC_ALIASES = {
    "生物": "biology",
    "biology": "biology",
    "bio": "biology",
    "医学": "biology",
    "medical": "biology",
    "人文": "humanities",
    "history": "humanities",
    "historical": "humanities",
    "humanities": "humanities",
    "文学": "humanities",
    "科技": "technology",
    "technology": "technology",
    "tech": "technology",
    "ai": "technology",
    "人工智能": "technology",
    "校园": "campus",
    "campus": "campus",
    "政策": "policy",
    "policy": "policy",
    "交通": "transportation",
    "transportation": "transportation",
    "教育": "education",
    "education": "education",
}

TOPIC_LABELS = {
    "biology": "生物",
    "humanities": "人文",
    "technology": "科技",
    "campus": "校园",
    "policy": "政策",
    "transportation": "交通",
    "education": "教育",
}

LOW_VALUE_USEFUL_PHRASES = {
    "i think",
    "in my opinion",
    "for example",
    "a lot of",
    "very important",
    "good for students",
    "bad for students",
    "i would say",
}


def _ensure_state_dir() -> None:
    speaking_state_file.parent.mkdir(parents=True, exist_ok=True)


def _load_state() -> dict:
    if not speaking_state_file.exists():
        return {}
    try:
        with open(speaking_state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    _ensure_state_dir()
    with open(speaking_state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _normalize_task_type(task_type: str | None) -> str | None:
    if not task_type:
        return None
    key = task_type.strip().lower().replace("-", "_").replace(" ", "_")
    if key in SPEAKING_TASK_SPECS:
        return key
    return SPEAKING_TASK_ALIASES.get(key)


def _normalize_sample_style(sample_style: str | None) -> str | None:
    if not sample_style:
        return None
    lowered = sample_style.strip().lower()
    return SAMPLE_STYLE_ALIASES.get(sample_style.strip()) or SAMPLE_STYLE_ALIASES.get(lowered)


def _normalize_sample_mode(sample_mode: str | None) -> str | None:
    if not sample_mode:
        return None
    lowered = sample_mode.strip().lower()
    return SAMPLE_MODE_ALIASES.get(sample_mode.strip()) or SAMPLE_MODE_ALIASES.get(lowered)


def _style_display_name(sample_style: str | None) -> tuple[str, str]:
    normalized = _normalize_sample_style(sample_style) or DEFAULT_SAMPLE_STYLE
    profile = SPEAKING_STYLE_PROFILES.get(normalized) or SPEAKING_STYLE_PROFILES[DEFAULT_SAMPLE_STYLE]
    return normalized, profile.get("label_cn") or "平衡版"


def _sample_mode_label(sample_mode: str | None) -> str:
    normalized = _normalize_sample_mode(sample_mode) or DEFAULT_SAMPLE_MODE
    return "词库强化" if normalized == "local_vocab" else "标准模式"


def _normalize_topic_hints(topic_hint: str | None) -> list[str]:
    raw = (topic_hint or "").strip()
    if not raw:
        return []
    lowered = raw.lower()
    hits = []
    for alias, canonical in TOPIC_ALIASES.items():
        haystack = raw if any(ord(ch) > 127 for ch in alias) else lowered
        needle = alias if any(ord(ch) > 127 for ch in alias) else alias.lower()
        if needle in haystack and canonical not in hits:
            hits.append(canonical)
    return hits


def _topic_hint_display(topic_hint: str | None) -> str:
    hints = _normalize_topic_hints(topic_hint)
    if hints:
        return " / ".join(TOPIC_LABELS.get(item, item) for item in hints)
    return (topic_hint or "").strip()


def _coerce_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)?", text or ""))


def _clip(text: str, limit: int = 3000) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + " ... [truncated]"


def _strip_model_artifacts(text: str) -> str:
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text or "", flags=re.IGNORECASE)
    cleaned = re.sub(r"^```(?:json|text)?\s*", "", cleaned.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    return cleaned.strip()


def _split_sentences(text: str) -> list[str]:
    if not text.strip():
        return []
    sentences = []
    for block in re.split(r"\n+", text.strip()):
        piece = block.strip()
        if not piece:
            continue
        for chunk in re.split(r"(?<=[.!?])\s+", piece):
            normalized = chunk.strip()
            if normalized:
                sentences.append(normalized)
    return sentences


def _parse_sample_request(raw_text: str) -> tuple[str | None, str | None, str | None]:
    text = (raw_text or "").strip()
    if not text:
        return None, None, None
    tokens = text.split()
    task = _normalize_task_type(text)
    style = _normalize_sample_style(text)
    mode = _normalize_sample_mode(text)
    if task or style or mode:
        return task, style, mode
    for token in tokens:
        if not task:
            task = _normalize_task_type(token)
        if not style:
            style = _normalize_sample_style(token)
        if not mode:
            mode = _normalize_sample_mode(token)

    compact_text = text.replace(" ", "").replace("_", "").replace("-", "").lower()
    if not task:
        for alias, canonical in sorted(SPEAKING_TASK_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
            alias_key = alias.replace("_", "").replace("-", "").lower()
            if alias_key and alias_key in compact_text:
                task = canonical
                break
    if not style:
        for alias, canonical in sorted(SAMPLE_STYLE_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
            alias_key = alias.replace(" ", "").lower()
            if alias_key and alias_key in compact_text:
                style = canonical
                break
    if not mode:
        for alias, canonical in sorted(SAMPLE_MODE_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
            alias_key = alias.replace(" ", "").lower()
            if alias_key and alias_key in compact_text:
                mode = canonical
                break
    return task, style, mode


def _parse_prompt_request(raw_text: str) -> tuple[str | None, str]:
    text = (raw_text or "").strip()
    if not text:
        return None, ""
    first, *rest = text.split(None, 1)
    normalized = _normalize_task_type(first)
    if normalized:
        return normalized, rest[0].strip() if rest else ""
    compact_text = text.replace(" ", "").replace("_", "").replace("-", "")
    lowered = compact_text.lower()
    for alias, canonical in sorted(SPEAKING_TASK_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        alias_key = alias.replace("_", "").replace("-", "").lower()
        if alias_key and lowered.startswith(alias_key):
            return canonical, compact_text[len(alias_key):].strip()
    return None, text


def _load_prompt_history(state: dict) -> dict:
    raw = state.get("prompt_history")
    return raw if isinstance(raw, dict) else {}


def _task_prompt_history(state: dict, task_type: str) -> list[dict]:
    history = _load_prompt_history(state)
    records = history.get(task_type)
    return records if isinstance(records, list) else []


def _used_prompt_ids(state: dict, task_type: str) -> set[str]:
    return {
        item.get("prompt_id")
        for item in _task_prompt_history(state, task_type)
        if isinstance(item, dict) and isinstance(item.get("prompt_id"), str)
    }


def _remember_prompt_history(state: dict, task_type: str, item: dict, prompt_text: str) -> None:
    history = _load_prompt_history(state)
    records = _task_prompt_history(state, task_type)
    records.append({
        "prompt_id": item.get("prompt_id"),
        "prompt_preview": _clip(prompt_text, 220),
        "topic_tags": item.get("topic_tags") or [],
        "used_at": datetime.now().isoformat(),
    })
    history[task_type] = records[-30:]
    state["prompt_history"] = history


def _recent_prompt_previews(state: dict, task_type: str, limit: int = 6) -> list[str]:
    previews = []
    for item in _task_prompt_history(state, task_type)[-limit:]:
        if isinstance(item, dict):
            preview = (item.get("prompt_preview") or "").strip()
            if preview:
                previews.append(preview)
    return previews


def _style_vocab_targets(task_type: str, sample_style: str | None = None, sample_mode: str | None = None) -> dict:
    normalized_style = _normalize_sample_style(sample_style) or DEFAULT_SAMPLE_STYLE
    normalized_mode = _normalize_sample_mode(sample_mode) or DEFAULT_SAMPLE_MODE
    profile = SPEAKING_STYLE_PROFILES.get(normalized_style) or SPEAKING_STYLE_PROFILES[DEFAULT_SAMPLE_STYLE]
    advanced_count = int(profile.get("recommended_advanced_count") or 3)
    local_vocab_count = int(profile.get("recommended_local_vocab_count") or 1)
    boosted_local_vocab_count = int(profile.get("boosted_local_vocab_count") or max(local_vocab_count, 2))
    if task_type == "take_interview":
        boosted_local_vocab_count = max(boosted_local_vocab_count, 3)
    return {
        "recommended_advanced_count": advanced_count,
        "recommended_local_vocab_count": local_vocab_count,
        "active_local_vocab_count": boosted_local_vocab_count if normalized_mode == "local_vocab" else local_vocab_count,
    }


def _local_vocab_mode_summary(task_type: str, sample_style: str | None, sample_mode: str | None, local_words: list[dict]) -> list[str]:
    normalized_mode = _normalize_sample_mode(sample_mode) or DEFAULT_SAMPLE_MODE
    targets = _style_vocab_targets(task_type, sample_style, normalized_mode)
    if normalized_mode != "local_vocab":
        return ["当前使用标准模式：以口语自然度和任务完成度为先，不强制植入本地词库。"]
    if not local_words:
        return ["当前使用词库强化模式，但本地词库里暂时没有足够合适的高阶词，本次已自动偏向标准写法。"]
    return [
        f"当前使用词库强化模式：会优先自然带入你本地词库里的高阶词，目标约 {targets['active_local_vocab_count']} 个。",
        "这些词会被放进真正的回答句里，而不是孤立展示，方便你直接学会怎么说。",
    ]


def _load_local_vocab_candidates(task_type: str, sample_style: str | None = None, sample_mode: str | None = None) -> list[dict]:
    normalized_mode = _normalize_sample_mode(sample_mode) or DEFAULT_SAMPLE_MODE
    if normalized_mode != "local_vocab":
        return []
    try:
        from word_manager import list_writing_vocab
    except Exception:
        return []
    targets = _style_vocab_targets(task_type, sample_style, normalized_mode)
    limit = max(6, targets["active_local_vocab_count"] * 3)
    candidates = list_writing_vocab(limit=limit)
    return [item for item in candidates if isinstance(item, dict)]


def _load_general_local_vocab_candidates(limit: int = 8) -> list[dict]:
    try:
        from word_manager import list_writing_vocab
    except Exception:
        return []
    candidates = list_writing_vocab(limit=limit)
    return [item for item in candidates if isinstance(item, dict)]


def _format_local_vocab_block(local_words: list[dict], target_count: int) -> str:
    if not local_words:
        return ""
    lines = [f"LOCAL LEARNER VOCABULARY TO PRIORITIZE (use around {target_count} items only when they sound natural in speech):"]
    for item in local_words[:8]:
        word = (item.get("word") or "").strip()
        definition = (item.get("definition") or "").strip()
        example = (item.get("example") or "").strip()
        if not word:
            continue
        line = f"- {word}"
        if definition:
            line += f": {definition}"
        if example:
            line += f" | Example: {example}"
        lines.append(line)
    return "\n".join(lines)


def _fallback_local_vocab_candidates(prompt_text: str, local_words: list[dict]) -> list[dict]:
    if not local_words:
        return []
    prompt_lower = (prompt_text or "").lower()
    suggestions = []
    for item in local_words[:8]:
        word = (item.get("word") or "").strip()
        if not word:
            continue
        if any(token in prompt_lower for token in ("improve", "support", "effective", "benefit", "concern", "question")):
            why_fit = f"{word} 适合放进理由句或评价句里，能把口语里的判断说得更精确。"
        else:
            why_fit = f"{word} 可以自然放进这道题的核心回答里，让表达更成熟。"
        suggestions.append({
            "word": word,
            "meaning": (item.get("definition") or "").strip(),
            "why_fit": why_fit,
        })
        if len(suggestions) >= 4:
            break
    return suggestions


def _find_word_sentence(text: str, word: str) -> str:
    pattern = re.compile(rf"\b{re.escape(word.lower())}\b")
    for sentence in _split_sentences(text):
        if pattern.search(sentence.lower()):
            return sentence
    return ""


def _fallback_local_vocab_usage_notes(model_answer: str, local_words: list[dict]) -> list[dict]:
    notes = []
    lowered = (model_answer or "").lower()
    for item in local_words[:8]:
        word = (item.get("word") or "").strip()
        if not word:
            continue
        if re.search(rf"\b{re.escape(word.lower())}\b", lowered):
            notes.append({
                "word": word,
                "meaning": (item.get("definition") or "").strip(),
                "how_used": _find_word_sentence(model_answer, word) or f"范例回答把 {word} 放进了真实回答句里。",
            })
    return notes


def _fallback_repetition_feedback(answer_text: str) -> list[str]:
    lowered = (answer_text or "").lower()
    feedback = []
    for phrase in ("i think", "because", "very", "really", "good", "important"):
        count = len(re.findall(rf"\b{re.escape(phrase)}\b", lowered))
        if count >= 3:
            feedback.append(f"{phrase} 重复偏多，建议至少替换掉其中 1-2 次，避免口语显得单薄。")
    if not feedback:
        feedback.append("明显机械重复不多，但还可以再增加连接和变换句型，让回答更像高分口语。")
    return feedback[:3]


def _tokenize_repeat_text(text: str) -> list[str]:
    return re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)?", text or "")


def _repeat_fixed_phrases(text: str) -> list[str]:
    lowered = (text or "").lower()
    phrases = []
    patterns = [
        r"\b[a-z]+ professor\b",
        r"\b[a-z]+ department\b",
        r"\b[a-z]+ lecturer\b",
        r"\bencouraged students to [a-z ]+?(?= before|,|\.|$)",
        r"\basked students to [a-z ]+?(?= before|,|\.|$)",
        r"\bcompare [a-z ]+? with [a-z ]+?(?= before|,|\.|$)",
        r"\balongside [a-z ]+?(?= before|,|\.|$)",
        r"\bbefore [a-z]+ing [a-z ]+?(?=,|\.|$)",
        r"\bplans to [a-z ]+?(?= so |,|\.|$)",
        r"\bto see whether [a-z ]+?(?=,|\.|$)",
        r"\bcould [a-z ]+?(?=,|\.|$)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, lowered):
            phrase = text[match.start():match.end()].strip(" ,.")
            if phrase and phrase not in phrases:
                phrases.append(phrase)
    if not phrases:
        clauses = [item.strip(" ,.") for item in re.split(r",|\.|;", text or "") if item.strip()]
        phrases.extend(clauses[:4])
    return phrases[:5]


def _repeat_chunking_tips(text: str) -> list[str]:
    source = " ".join((text or "").split())
    if not source:
        return []
    tips = []
    anchor_patterns = [" before ", " so ", " to see whether "]
    for anchor in anchor_patterns:
        lowered = source.lower()
        index = lowered.find(anchor)
        if index > 0:
            left = source[:index].strip(" ,")
            right = source[index + 1:].strip(" ,")
            if left:
                tips.append(left)
            if right:
                tips.append(anchor.strip() + " " + right[len(anchor.strip()):].strip() if right.lower().startswith(anchor.strip()) else right)
            break
    if not tips:
        comma_parts = [item.strip() for item in source.split(",") if item.strip()]
        tips = comma_parts[:3]
    if len(tips) < 3:
        words = source.split()
        if len(words) >= 9:
            third = max(3, len(words) // 3)
            tips = [
                " ".join(words[:third]),
                " ".join(words[third:third * 2]),
                " ".join(words[third * 2:]),
            ]
    return [item.strip() for item in tips if item.strip()][:3]


def _repeat_difficulty_notes(text: str) -> list[str]:
    notes = []
    fixed_phrases = _repeat_fixed_phrases(text)
    if fixed_phrases:
        notes.append(f"最容易丢的是固定搭配 {fixed_phrases[0]}，一旦换词，复述准确度就会下降。")
    if " with " in (text or "").lower():
        notes.append("带 with 的比较结构很容易被说成 compare A and B，但原句是 compare A with B。")
    if re.search(r"\bbefore\s+[a-z]+ing\b", (text or "").lower()):
        notes.append("before + 动名词 这一段很容易被截断，建议单独记成最后一个收尾块。")
    if len(_tokenize_repeat_text(text)) >= 14:
        notes.append("这句长度偏长，最好先记主干，再补修饰，不要从头硬背到尾。")
    return notes[:4]


def _repeat_alignment_notes(reference_text: str, learner_text: str) -> list[str]:
    ref_tokens = _tokenize_repeat_text(reference_text)
    learner_tokens = _tokenize_repeat_text(learner_text)
    matcher = SequenceMatcher(a=[token.lower() for token in ref_tokens], b=[token.lower() for token in learner_tokens])
    notes = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            missing = " ".join(ref_tokens[i1:i2])
            if missing:
                notes.append(f"漏掉了原句里的 {missing}，这会直接影响信息完整度。")
        elif tag == "insert":
            extra = " ".join(learner_tokens[j1:j2])
            if extra:
                notes.append(f"多加了 {extra}，复述题不建议擅自补充原句没有的信息。")
        elif tag == "replace":
            old = " ".join(ref_tokens[i1:i2])
            new = " ".join(learner_tokens[j1:j2])
            if old or new:
                notes.append(f"把 {old} 说成了 {new}，这里更像改写，不是准确复述。")
        if len(notes) >= 4:
            break
    if not notes:
        notes.append("和原句的词序与信息基本一致，当前最大任务是继续稳住节奏和完整度。")
    return notes[:4]


def _format_repeat_sections(lines: list[str], transcript: str, learner_text: str = "") -> None:
    collocations = _repeat_fixed_phrases(transcript)
    chunking = _repeat_chunking_tips(transcript)
    difficulty_notes = _repeat_difficulty_notes(transcript)
    if collocations:
        lines.append("")
        lines.append("原句里的固定搭配:")
        for idx, item in enumerate(collocations[:5], 1):
            lines.append(f"  {idx}. {item}")
    if difficulty_notes:
        lines.append("")
        lines.append("这道复述题的难点:")
        for idx, item in enumerate(difficulty_notes[:4], 1):
            lines.append(f"  {idx}. {item}")
    if chunking:
        lines.append("")
        lines.append("断句记忆建议:")
        for idx, item in enumerate(chunking[:3], 1):
            lines.append(f"  {idx}. {item}")
    if learner_text.strip():
        alignment_notes = _repeat_alignment_notes(transcript, learner_text)
        if alignment_notes:
            lines.append("")
            lines.append("你和原句的差异:")
            for idx, item in enumerate(alignment_notes[:4], 1):
                lines.append(f"  {idx}. {item}")


def _fallback_sentence_issues(answer_text: str) -> list[dict]:
    sentences = _split_sentences(answer_text)
    issues = []
    for sentence in sentences[:3]:
        stripped = sentence.strip()
        if len(stripped.split()) <= 5:
            issues.append({
                "quote": stripped,
                "problem": "这句过短，信息量不足，像碎片化回答。",
                "suggestion": "把原因、结果或例子补全成完整句，让观点真正站住。",
            })
        elif stripped.lower().startswith("and ") or stripped.lower().startswith("but "):
            issues.append({
                "quote": stripped,
                "problem": "句子起手较松散，像临时补充，不够稳。",
                "suggestion": "可以先给主张，再用 and / but 衔接补充信息。",
            })
    return issues[:3]


def _fallback_advanced_vocabulary_feedback(sample_style: str | None) -> list[str]:
    normalized, _ = _style_display_name(sample_style)
    if normalized == "aggressive":
        return [
            "高阶词还可以更集中地落在判断句里，而不是只放在背景描述里。",
            "尽量把 vague 的 good / bad / important 换成更精确的评价词。",
        ]
    return [
        "高级词汇要优先放在立场句和理由句里，这样更容易被阅卷人感知到。",
        "口语里高级词不需要很多，但每一个都要真地提升信息精度。",
    ]


def _fallback_advanced_structure_feedback(task_type: str) -> list[str]:
    if task_type == "listen_repeat":
        return [
            "听后复述更看重信息完整和节奏稳定，不需要强行拉长句子。",
            "真正的高分点在于结构不被打乱，修饰关系也不被改坏。",
        ]
    return [
        "高分口语通常至少有一处让步、对比或因果压缩，而不是全程短句平铺。",
        "如果第二句能自然解释第一句的理由，句式层次就会明显更好。",
    ]


def _fallback_high_score_keys(task_type: str) -> list[str]:
    if task_type == "listen_repeat":
        return [
            "完整保留了原句的核心信息，没有随意删减或改写关键细节。",
            "语序和修饰关系稳定，所以听起来像准确复现，而不是大意转述。",
            "节奏自然，没有为了追求速度破坏清晰度。",
        ]
    return [
        "开头第一句就直接回答问题，没有先说空话或拖延进入主题。",
        "理由句和解释句之间连接自然，所以回答不会像零散观点堆叠。",
        "结尾能把理由压回主张，让整段回答更完整、更像高分口语。",
    ]


def _fallback_advanced_expression_diff(answer_text: str, useful_phrases: list[str]) -> list[dict]:
    learner_lower = (answer_text or "").lower()
    replacements = [
        ("i think", useful_phrases[0] if useful_phrases else "from my perspective"),
        ("good", useful_phrases[1] if len(useful_phrases) > 1 else "far more effective"),
        ("important", useful_phrases[2] if len(useful_phrases) > 2 else "what matters most is"),
    ]
    diff = []
    for learner_expression, upgraded in replacements:
        if learner_expression in learner_lower or not answer_text.strip():
            diff.append({
                "learner_expression": learner_expression,
                "model_expression": upgraded,
                "why_better": f"把 {learner_expression} 升级成 {upgraded}，语气会更成熟，也更像高分口语里的自然搭配。",
            })
    return diff[:3]


def _looks_like_high_value_phrase(text: str) -> bool:
    tokens = re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)?", text or "")
    if len(tokens) < 2:
        return False
    if len(tokens) >= 3:
        return True
    return any(len(token) >= 7 for token in tokens)


def _is_low_value_useful_phrase(text: str) -> bool:
    lowered = " ".join((text or "").strip().lower().split())
    if not lowered:
        return True
    return any(phrase in lowered for phrase in LOW_VALUE_USEFUL_PHRASES)


def _curate_useful_phrases(sample_style: str | None, model_answer: str, candidates: list[str]) -> list[str]:
    curated = []

    def _push(phrase: str) -> None:
        cleaned = _strip_model_artifacts(phrase or "").strip()
        if not cleaned or _is_low_value_useful_phrase(cleaned):
            return
        if not _looks_like_high_value_phrase(cleaned):
            return
        lowered = cleaned.lower()
        if lowered in {item.lower() for item in curated}:
            return
        curated.append(cleaned)

    for item in candidates or []:
        _push(str(item))
    normalized, _ = _style_display_name(sample_style)
    profile = SPEAKING_STYLE_PROFILES.get(normalized) or SPEAKING_STYLE_PROFILES[DEFAULT_SAMPLE_STYLE]
    for item in profile.get("lexical_targets") or []:
        _push(str(item))
    for sentence in _split_sentences(model_answer):
        for clause in re.split(r"[,;:]", sentence):
            chunk = clause.strip()
            if 3 <= len(chunk.split()) <= 10:
                _push(chunk)
        if len(curated) >= 8:
            break
    return curated[:6]


def _select_prompt(task_type: str, state: dict, topic_hint: str | None = None) -> dict | None:
    items = PROMPT_BANK.get(task_type) or []
    if not items:
        return None
    requested_topics = set(_normalize_topic_hints(topic_hint))
    primary_candidates = []
    candidates = []
    for item in items:
        item_topics = set(item.get("topic_tags") or [])
        if requested_topics and not (requested_topics & item_topics):
            continue
        primary_topic = next(iter(item.get("topic_tags") or []), "")
        if requested_topics and primary_topic in requested_topics:
            primary_candidates.append(item)
        candidates.append(item)
    effective_candidates = primary_candidates or candidates
    if not effective_candidates:
        return None
    used_prompt_ids = _used_prompt_ids(state, task_type)
    unused = [item for item in effective_candidates if item.get("prompt_id") not in used_prompt_ids]
    pool = unused or effective_candidates
    return random.choice(pool)


def _all_matching_prompts_used(task_type: str, state: dict, topic_hint: str | None = None) -> bool:
    items = PROMPT_BANK.get(task_type) or []
    requested_topics = set(_normalize_topic_hints(topic_hint))
    matches = []
    for item in items:
        item_topics = set(item.get("topic_tags") or [])
        if requested_topics and not (requested_topics & item_topics):
            continue
        matches.append(item)
    if not matches:
        return True
    used_ids = _used_prompt_ids(state, task_type)
    return all((item.get("prompt_id") or "") in used_ids for item in matches)


def _build_dynamic_prompt_request(task_type: str, topic_hint: str | None, recent_prompts: list[str]) -> list[dict]:
    spec = SPEAKING_TASK_SPECS[task_type]
    topic_display = _topic_hint_display(topic_hint) or (topic_hint or "").strip() or "无"
    recent_block = "\n".join(f"- {item}" for item in recent_prompts) if recent_prompts else "- None"
    task_rule = {
        "listen_repeat": "Create one concise academic sentence that is worth repeating exactly. It should contain 1-2 collocations or structural traps, but still be fully natural.",
        "take_interview": "Create one campus or academic interview setup with one interviewer line, one direct question, and 1-2 follow-up angles.",
    }
    return [
        {"role": "system", "content": "You design TOEFL speaking prompts. Return strict JSON only."},
        {"role": "user", "content": f"""Task type: {task_type} ({spec['label']})
Topic hint: {topic_display}

Recent prompts that must not be repeated or lightly paraphrased:
{recent_block}

Return strict JSON only with keys:
{{
  "prompt_id": string,
  "topic_tags": [string, string],
  "topic_label_cn": string,
  "listening_transcript": string,
  "question": string,
  "follow_up": [string, string]
}}

Rules:
- {task_rule.get(task_type, 'Create one fresh TOEFL speaking prompt.')}
- If a topic hint is given, make it central.
- listening_transcript must be fully original and materially different from the recent prompts.
- prompt_id must be short, lowercase, and underscore-separated.
- topic_tags should contain 1-3 concise English tags.
- topic_label_cn should be a short Chinese label.
- For listen_repeat, follow_up should be an empty list.
- For take_interview, question must be directly answerable in 45-60 seconds.
"""},
    ]


def _generate_prompt_with_llm(task_type: str, topic_hint: str | None, state: dict) -> dict | None:
    if not is_configured():
        return None
    result = _run_json(
        _build_dynamic_prompt_request(task_type, topic_hint, _recent_prompt_previews(state, task_type)),
        temperature=0.6,
        max_tokens=1000,
    )
    if not isinstance(result, dict):
        return None
    transcript = _strip_model_artifacts(result.get("listening_transcript") or "")
    question = _strip_model_artifacts(result.get("question") or "")
    if not transcript or not question:
        return None
    follow_up = result.get("follow_up") or []
    if task_type == "listen_repeat":
        follow_up = []
    item = {
        "prompt_id": (_strip_model_artifacts(result.get("prompt_id") or "") or f"generated_{int(datetime.now().timestamp())}").strip().lower().replace("-", "_"),
        "topic_tags": [str(tag).strip().lower() for tag in (result.get("topic_tags") or []) if str(tag).strip()][:3],
        "topic_label_cn": _strip_model_artifacts(result.get("topic_label_cn") or "") or _topic_hint_display(topic_hint) or "扩展题",
        "listening_transcript": transcript,
        "question": question,
        "follow_up": [str(item).strip() for item in follow_up if str(item).strip()][:2],
    }
    return item


def _render_prompt(task_type: str, item: dict) -> str:
    spec = SPEAKING_TASK_SPECS[task_type]
    topic_label = (item.get("topic_label_cn") or "").strip()
    lines = [
        f"Task Type: {spec['label']} ({spec['label_cn']})",
        f"Preparation: {spec['prep_time']} | Suggested Length: {spec['target_words']}",
    ]
    if topic_label:
        lines.append(f"Topic: {topic_label}")
    lines.append("")
    lines.append("Listening Transcript:")
    lines.append(item.get("listening_transcript") or "")
    lines.append("")
    lines.append("Question Prompt:")
    lines.append(item.get("question") or "")
    follow_up = item.get("follow_up") or []
    if follow_up:
        lines.append("")
        lines.append("You may cover:")
        lines.extend([f"- {point}" for point in follow_up[:3]])
    return "\n".join(lines)


def _render_prompt_for_learner(task_type: str, item: dict) -> str:
    """学员视角的题面：listen_repeat 不暴露听力原文，而是引导使用在线 TTS 听音频。"""
    if task_type != "listen_repeat":
        return _render_prompt(task_type, item)

    spec = SPEAKING_TASK_SPECS[task_type]
    topic_label = (item.get("topic_label_cn") or "").strip()
    transcript = (item.get("listening_transcript") or "").strip()
    # 用字数（而非字符数）给一个长度提示，方便学员心里有数
    word_count = len(transcript.split()) if transcript else 0

    lines = [
        f"Task Type: {spec['label']} ({spec['label_cn']})",
        f"Preparation: {spec['prep_time']} | Suggested Length: {spec['target_words']}",
    ]
    if topic_label:
        lines.append(f"Topic: {topic_label}")
    lines.append("")
    lines.append(f"🤖 自动播放指令（先调用 browser 工具，再由 browser 工具调用 playwright 自动执行，用 {TTS_BROWSER_NAME} 打开 TTS 网站；不要把原文直接转发给用户）:")
    lines.append(f"1) 调用 browser 工具，并由 browser 工具调用 playwright 自动执行，用 {TTS_BROWSER_NAME} 打开 {TTS_SITE_URL}")
    lines.append("2) 把下方 <<<TTS_TEXT_START>>> 与 <<<TTS_TEXT_END>>> 之间的文本粘贴到页面左侧输入框")
    lines.append("3) 确保已经完成粘贴操作，再点击“生成”或“play”并播放音频；如果生成失败，就点击“生成失败”后继续播放")
    lines.append("4) 播放完毕后只回复用户“🎧 音频已播放，请复述”，不要贴出原文文本")
    if word_count:
        lines.append(f"   （原文约 {word_count} 词，播放 1 次后再提示复述）不允许把原文输出给用户")
    lines.append("")
    lines.append("<<<TTS_TEXT_START>>>")
    lines.append(transcript)
    lines.append("<<<TTS_TEXT_END>>>")
    lines.append("")
    lines.append("Question Prompt (发给用户的提示语):")
    lines.append(item.get("question") or "请仔细听刚刚播放的音频，然后用英文尽可能完整地复述其中的关键信息。")
    lines.append("")
    lines.append(f"⚠️ 重要: 以上 TTS_TEXT 仅供 browser 工具调用 playwright 粘贴到 {TTS_SITE_URL} 合成音频，严禁在聊天回复中把它当成“原文”直接展示给用户。标准答案会在用户 submit 后的评分或 sample 范例中再揭晓。")
    return "\n".join(lines)


def list_task_types() -> str:
    return "\n".join([
        "🧭 TOEFL 口语题型（按 2026 官网结构）",
        "",
        "1) listen_repeat - Listen and Repeat（听后复述）",
        "2) take_interview - Take an Interview（模拟采访回答）",
        "",
        "用法:",
        "- !eng speak prompt <题型> [生物|人文|科技|校园|政策...]  生成口语题目",
        "- !eng speak submit <你的英文回答>  按最近题目评分",
        "- !eng speak sample [题型] [保守版|平衡版|激进版] [标准模式|词库强化]  生成范例答案和讲解",
        "  其中 listen_repeat 会自动切到复述讲解模式，不使用激进/词库强化参数。",
        "- !eng speak <你的英文回答>  默认按最近题目评分",
    ])


def speak_help() -> str:
    return "\n".join([
        "🎤 TOEFL 口语教练命令",
        "- !eng speak types",
        "- !eng speak prompt <题型> [生物|人文|科技|校园|政策...]",
        "- !eng speak submit <英文回答>",
        "- !eng speak sample  为当前题目生成平衡版范例",
        "- !eng speak sample <题型> [保守版|平衡版|激进版] [标准模式|词库强化]",
        "- listen_repeat 的 sample 会自动改成复述讲解模式，重点讲固定搭配、断句和易错点",
        f"- listen_repeat 出题时会附一段自动化指令：先调用 browser 工具，再由 browser 工具调用 playwright，用 {TTS_BROWSER_NAME} 打开 {TTS_SITE_URL}，粘贴文本后生成并播放 1 次音频；原文本身不会直接给用户看，只在 submit 评分或 sample 范例里作为标准答案出现",
        "- !eng speak <英文回答>  默认按最近题目评分",
    ])


def start_prompt(task_type: str | None = None, topic_hint: str | None = None) -> str:
    state = _load_state()
    normalized = _normalize_task_type(task_type) or "take_interview"
    if normalized not in SPEAKING_TASK_SPECS:
        return "⚠️ 题型不支持。发送 !eng speak types 查看全部口语题型。"

    item = _select_prompt(normalized, state, topic_hint)
    if _all_matching_prompts_used(normalized, state, topic_hint):
        generated_item = _generate_prompt_with_llm(normalized, topic_hint, state)
        if generated_item:
            item = generated_item
    if not item:
        return "⚠️ 当前没有符合该题材的口语题目，请换一个题材提示词试试。"

    prompt_text = _render_prompt(normalized, item)
    learner_prompt_text = _render_prompt_for_learner(normalized, item)
    state.update({
        "task_type": normalized,
        "prompt_text": prompt_text,
        "prompt_payload": item,
        "last_prompt_id": item.get("prompt_id"),
        "last_prompt_hint": (topic_hint or "").strip(),
        "generated_at": datetime.now().isoformat(),
    })
    _remember_prompt_history(state, normalized, item, prompt_text)
    _save_state(state)

    spec = SPEAKING_TASK_SPECS[normalized]
    lines = [
        "🎤 TOEFL 口语题已生成（2026 口语结构）",
        f"题型: {normalized} ({spec['label_cn']})",
    ]
    topic_display = _topic_hint_display(topic_hint)
    if topic_display:
        lines.append(f"题材提示: {topic_display}")
    lines.extend([
        "",
        learner_prompt_text,
        "",
        "请直接回复:",
        "!eng speak submit <你的英文回答>",
        "看范例可发送: !eng speak sample [保守版|平衡版|激进版] [标准模式|词库强化]",
    ])
    if normalized == "listen_repeat":
        lines.append(f"（OpenClaw/龙虾请按上面的自动播放指令，先调用 browser 工具，再由 browser 工具调用 playwright，用 {TTS_BROWSER_NAME} 完成打开 {TTS_SITE_URL} → 粘贴 → 生成/播放 1 次；整个过程对用户保持原文保密，标准答案会在 submit 评分或 sample 范例里再展示。）")
    return "\n".join(lines)


def _build_scoring_prompt(task_type: str, prompt_text: str, answer_text: str, sample_style: str | None = None, sample_mode: str | None = None, local_words: list[dict] | None = None) -> tuple[str, str]:
    spec = SPEAKING_TASK_SPECS[task_type]
    normalized_style, style_cn = _style_display_name(sample_style)
    normalized_mode = _normalize_sample_mode(sample_mode) or DEFAULT_SAMPLE_MODE
    mode_cn = _sample_mode_label(normalized_mode)
    style_profile = SPEAKING_STYLE_PROFILES.get(normalized_style) or SPEAKING_STYLE_PROFILES[DEFAULT_SAMPLE_STYLE]
    vocab_targets = _style_vocab_targets(task_type, normalized_style, normalized_mode)
    local_vocab_block = _format_local_vocab_block(local_words or [], vocab_targets["active_local_vocab_count"])

    system_prompt = "You are a strict TOEFL speaking rater and speaking coach. Return strict JSON only."
    user_prompt = f"""Current official-style task type: {task_type} ({spec['label']})
Chinese label: {spec['label_cn']}
Selected sample style: {normalized_style} ({style_cn})
Selected sample mode: {normalized_mode} ({mode_cn})
Task objective: {spec['description']}

Prompt shown to learner:
{_clip(prompt_text, 2600)}

Learner spoken-response transcript:
{_clip(answer_text, 2400)}

{local_vocab_block}

Return strict JSON only with keys:
{{
  "overall_score": number,
  "subscores": {{
    "delivery": number,
    "language_use": number,
    "topic_development": number
  }},
  "strengths": [string, string],
  "weaknesses": [string, string],
  "sentence_issues": [
    {{"quote": string, "problem": string, "suggestion": string}}
  ],
  "repetition_feedback": [string, string],
  "naturalness_feedback": [string, string],
  "advanced_vocabulary_feedback": [string, string],
  "advanced_structure_feedback": [string, string],
  "high_score_keys": [string, string, string],
  "model_answer": string,
  "useful_phrases": [string, string, string, string],
  "local_vocab_candidates": [
    {{"word": string, "meaning": string, "why_fit": string}}
  ],
  "local_vocab_usage_notes": [
    {{"word": string, "meaning": string, "how_used": string}}
  ],
  "advanced_expression_diff": [
    {{"learner_expression": string, "model_expression": string, "why_better": string}}
  ],
  "upgrade_from_learner": [string, string],
  "next_drill": string
}}

Rules:
- Score on a 0-4 scale.
- delivery must reflect fluency, pacing, and intelligibility inferred from the transcript.
- language_use must reflect grammar range, vocabulary precision, and native-like phrasing.
- For listen_repeat, topic_development means fidelity and completeness of the repeated content, not opinion development.
- For take_interview, topic_development means direct answer quality, reasoning, relevance, and organization.
- strengths/weaknesses/sentence_issues/repetition_feedback/naturalness_feedback/advanced_vocabulary_feedback/advanced_structure_feedback/high_score_keys/upgrade_from_learner/next_drill must all be in Chinese.
- If task_type is listen_repeat, model_answer should be a near-perfect repeat of the listening transcript, keeping the original wording and meaning intact.
- If task_type is take_interview, model_answer should be 90-130 words, highly natural, and clearly answer the question with specific support.
- Use noticeably stronger syntax and diction in model_answer than in a typical student response, but keep it speakable.
- useful_phrases must be genuinely reusable advanced spoken chunks worth imitating, not low-value fillers like "I think" or "in my opinion".
- high_score_keys must contain at least 3 specific points.
- local_vocab_candidates should recommend 2-4 learner-bank words that would fit this task naturally.
- If sample_mode is local_vocab and learner vocabulary is provided, naturally use around {vocab_targets['active_local_vocab_count']} learner words in model_answer only when they genuinely fit.
- local_vocab_usage_notes must reference only words actually used in model_answer.
- advanced_expression_diff should compare the learner's weaker wording with stronger model wording. If the learner answer is already strong, still provide 2-3 upgrade contrasts.
- Keep style consistent with this style description: {style_profile.get('description')}
"""
    return system_prompt, user_prompt


def _build_model_prompt(task_type: str, prompt_text: str, learner_answer: str = "", sample_style: str | None = None, sample_mode: str | None = None, local_words: list[dict] | None = None) -> tuple[str, str]:
    spec = SPEAKING_TASK_SPECS[task_type]
    normalized_style, style_cn = _style_display_name(sample_style)
    normalized_mode = _normalize_sample_mode(sample_mode) or DEFAULT_SAMPLE_MODE
    mode_cn = _sample_mode_label(normalized_mode)
    style_profile = SPEAKING_STYLE_PROFILES.get(normalized_style) or SPEAKING_STYLE_PROFILES[DEFAULT_SAMPLE_STYLE]
    vocab_targets = _style_vocab_targets(task_type, normalized_style, normalized_mode)
    local_vocab_block = _format_local_vocab_block(local_words or [], vocab_targets["active_local_vocab_count"])
    learner_block = f"Learner response to improve upon stylistically:\n{_clip(learner_answer, 2200)}\n\n" if learner_answer.strip() else ""

    system_prompt = "You are an elite TOEFL speaking coach. Return strict JSON only."
    user_prompt = f"""Task type: {task_type} ({spec['label']})
Chinese label: {spec['label_cn']}
Selected sample style: {normalized_style} ({style_cn})
Selected sample mode: {normalized_mode} ({mode_cn})
Task objective: {spec['description']}

Prompt shown to learner:
{_clip(prompt_text, 2600)}

{local_vocab_block}
{learner_block}Return strict JSON only with keys:
{{
  "style_used": string,
  "sample_mode": string,
  "model_answer": string,
  "high_score_keys": [string, string, string],
  "naturalness_feedback": [string, string],
  "advanced_vocabulary_feedback": [string, string],
  "advanced_structure_feedback": [string, string],
  "useful_phrases": [string, string, string, string],
  "local_vocab_candidates": [
    {{"word": string, "meaning": string, "why_fit": string}}
  ],
  "local_vocab_usage_notes": [
    {{"word": string, "meaning": string, "how_used": string}}
  ],
  "advanced_expression_diff": [
    {{"learner_expression": string, "model_expression": string, "why_better": string}}
  ],
  "upgrade_from_learner": [string, string]
}}

Rules:
- model_answer must be original and directly answer THIS prompt.
- If task_type is listen_repeat, model_answer should closely mirror the listening transcript and sound accurate and natural.
- If task_type is take_interview, model_answer should be 90-130 words, sound like polished high-scoring speech, and remain easy to speak aloud.
- Use more advanced but still natural vocabulary and syntax than an average student response.
- useful_phrases must exclude low-value fillers such as "I think" or "in my opinion".
- high_score_keys must contain at least 3 concrete Chinese points.
- local_vocab_candidates must recommend 2-4 learner-bank words that fit this topic naturally.
- If sample_mode is local_vocab and learner vocabulary is provided, naturally use around {vocab_targets['active_local_vocab_count']} learner words when they fit speech.
- local_vocab_usage_notes must only mention words actually used in model_answer.
- advanced_expression_diff should provide 2-3 upgrade comparisons, even if learner_answer is missing.
- upgrade_from_learner must be in Chinese and only discuss what the learner can improve.
- Keep style consistent with this style description: {style_profile.get('description')}
"""
    return system_prompt, user_prompt


def _run_json(messages: list[dict], temperature: float, max_tokens: int, timeout: int = 90) -> dict | None:
    if not is_configured():
        return None
    result = chat_json(messages, temperature=temperature, max_tokens=max_tokens, timeout=timeout, retries=2)
    return result if isinstance(result, dict) else None


def _fallback_model_answer(task_type: str, state_item: dict | None = None) -> str:
    if task_type == "listen_repeat" and isinstance(state_item, dict):
        return (state_item.get("listening_transcript") or "").strip()
    return "From my perspective, this change would be worthwhile because it improves the learning experience in a practical way. Students usually benefit more when they can apply ideas instead of only hearing about them. At the same time, the university should introduce the policy carefully and give students clear support, because a good plan only works when it is realistic for everyone involved."


def _clean_feedback_list(values) -> list[str]:
    cleaned = []
    for item in values or []:
        text = _strip_model_artifacts(str(item))
        if text:
            cleaned.append(text)
    return cleaned


def _clean_dict_list(values, keys: list[str]) -> list[dict]:
    cleaned = []
    for item in values or []:
        if not isinstance(item, dict):
            continue
        row = {}
        for key in keys:
            row[key] = _strip_model_artifacts(item.get(key) or "")
        cleaned.append(row)
    return cleaned


def _format_common_sections(lines: list[str], high_score_keys: list[str], naturalness_feedback: list[str], advanced_vocabulary_feedback: list[str], advanced_structure_feedback: list[str], local_vocab_candidates: list[dict], local_vocab_usage_notes: list[dict], advanced_expression_diff: list[dict], useful_phrases: list[str], upgrade_from_learner: list[str]) -> None:
    if high_score_keys:
        lines.append("")
        lines.append("高分关键:")
        for idx, item in enumerate(high_score_keys[:4], 1):
            lines.append(f"  {idx}. {item}")

    if naturalness_feedback:
        lines.append("")
        lines.append("是否地道:")
        for idx, item in enumerate(naturalness_feedback[:3], 1):
            lines.append(f"  {idx}. {item}")

    if advanced_vocabulary_feedback:
        lines.append("")
        lines.append("用词是否高级:")
        for idx, item in enumerate(advanced_vocabulary_feedback[:3], 1):
            lines.append(f"  {idx}. {item}")

    if advanced_structure_feedback:
        lines.append("")
        lines.append("句式是否高级:")
        for idx, item in enumerate(advanced_structure_feedback[:3], 1):
            lines.append(f"  {idx}. {item}")

    if local_vocab_candidates:
        lines.append("")
        lines.append("本地能用词汇:")
        for idx, item in enumerate(local_vocab_candidates[:4], 1):
            word = (item.get("word") or "").strip()
            meaning = (item.get("meaning") or "").strip()
            why_fit = (item.get("why_fit") or "").strip()
            if not word:
                continue
            lines.append(f"  {idx}. {word}")
            if meaning:
                lines.append(f"     - 释义: {meaning}")
            if why_fit:
                lines.append(f"     - 适配原因: {why_fit}")

    if local_vocab_usage_notes:
        lines.append("")
        lines.append("本地词库词汇是怎么用的:")
        for idx, item in enumerate(local_vocab_usage_notes[:4], 1):
            word = (item.get("word") or "").strip()
            meaning = (item.get("meaning") or "").strip()
            how_used = (item.get("how_used") or "").strip()
            if not word:
                continue
            lines.append(f"  {idx}. {word}")
            if meaning:
                lines.append(f"     - 释义: {meaning}")
            if how_used:
                lines.append(f"     - 用法: {how_used}")

    if advanced_expression_diff:
        lines.append("")
        lines.append("高级表达 diff:")
        for idx, item in enumerate(advanced_expression_diff[:4], 1):
            learner_expression = (item.get("learner_expression") or "").strip()
            model_expression = (item.get("model_expression") or "").strip()
            why_better = (item.get("why_better") or "").strip()
            if learner_expression or model_expression:
                lines.append(f"  {idx}. {learner_expression} -> {model_expression}")
                if why_better:
                    lines.append(f"     - {why_better}")

    if useful_phrases:
        lines.append("")
        lines.append("可直接借鉴的高级表达:")
        for idx, item in enumerate(useful_phrases[:6], 1):
            lines.append(f"  {idx}. {item}")

    if upgrade_from_learner:
        lines.append("")
        lines.append("相对你当前回答的升级点:")
        for idx, item in enumerate(upgrade_from_learner[:4], 1):
            lines.append(f"  {idx}. {item}")


def generate_model_answer(task_type: str | None = None, sample_style: str | None = None, sample_mode: str | None = None) -> str:
    state = _load_state()
    explicit_task = _normalize_task_type(task_type)
    normalized = explicit_task or _normalize_task_type(state.get("task_type"))
    if not normalized:
        return "⚠️ 还没有当前口语题目。先发送 !eng speak prompt <题型>。"

    prompt_text = (state.get("prompt_text") or "").strip()
    prompt_item = state.get("prompt_payload") if isinstance(state.get("prompt_payload"), dict) else None
    if explicit_task and normalized != _normalize_task_type(state.get("task_type")):
        selected = _select_prompt(normalized, state, state.get("last_prompt_hint"))
        if not selected:
            return "⚠️ 当前没有可用口语题目，请先发送 !eng speak prompt <题型>。"
        prompt_item = selected
        prompt_text = _render_prompt(normalized, selected)
    if not prompt_text:
        selected = _select_prompt(normalized, state, state.get("last_prompt_hint"))
        if not selected:
            return "⚠️ 当前没有可用口语题目，请先发送 !eng speak prompt <题型>。"
        prompt_item = selected
        prompt_text = _render_prompt(normalized, selected)

    normalized_style, style_cn = _style_display_name(sample_style or state.get("last_sample_style"))
    normalized_mode = _normalize_sample_mode(sample_mode or state.get("last_sample_mode")) or DEFAULT_SAMPLE_MODE
    if normalized == "listen_repeat":
        normalized_style = DEFAULT_SAMPLE_STYLE
        normalized_mode = DEFAULT_SAMPLE_MODE
    mode_cn = _sample_mode_label(normalized_mode)
    learner_answer = ""
    if _normalize_task_type(state.get("last_answer_task_type")) == normalized and (state.get("last_answer_prompt_text") or "").strip() == prompt_text:
        learner_answer = (state.get("last_answer_text") or "").strip()
    local_words = [] if normalized == "listen_repeat" else _load_local_vocab_candidates(normalized, normalized_style, normalized_mode)
    suggestion_words = [] if normalized == "listen_repeat" else (local_words or _load_general_local_vocab_candidates(limit=8))

    result = None
    if is_configured():
        system_prompt, user_prompt = _build_model_prompt(normalized, prompt_text, learner_answer, normalized_style, normalized_mode, local_words)
        result = _run_json([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ], temperature=0.35, max_tokens=1800)

    model_answer = _strip_model_artifacts((result or {}).get("model_answer") or "") or _fallback_model_answer(normalized, prompt_item)
    high_score_keys = _clean_feedback_list((result or {}).get("high_score_keys")) or _fallback_high_score_keys(normalized)
    naturalness_feedback = _clean_feedback_list((result or {}).get("naturalness_feedback")) or [
        "这份范例的口语感更强，因为判断句、解释句和收束句之间过渡自然。",
        "它不像朗读作文，而是像高分考生在限时内给出的成熟回答。",
    ]
    advanced_vocabulary_feedback = _clean_feedback_list((result or {}).get("advanced_vocabulary_feedback")) or _fallback_advanced_vocabulary_feedback(normalized_style)
    advanced_structure_feedback = _clean_feedback_list((result or {}).get("advanced_structure_feedback")) or _fallback_advanced_structure_feedback(normalized)
    local_vocab_candidates = [] if normalized == "listen_repeat" else (_clean_dict_list((result or {}).get("local_vocab_candidates"), ["word", "meaning", "why_fit"]) or _fallback_local_vocab_candidates(prompt_text, suggestion_words))
    local_vocab_usage_notes = [] if normalized == "listen_repeat" else (_clean_dict_list((result or {}).get("local_vocab_usage_notes"), ["word", "meaning", "how_used"]) or _fallback_local_vocab_usage_notes(model_answer, local_words))
    useful_phrases = [] if normalized == "listen_repeat" else _curate_useful_phrases(normalized_style, model_answer, _clean_feedback_list((result or {}).get("useful_phrases")))
    advanced_expression_diff = [] if normalized == "listen_repeat" else (_clean_dict_list((result or {}).get("advanced_expression_diff"), ["learner_expression", "model_expression", "why_better"]) or _fallback_advanced_expression_diff(learner_answer, useful_phrases))
    upgrade_from_learner = [] if normalized == "listen_repeat" else (_clean_feedback_list((result or {}).get("upgrade_from_learner")) or [
        "先把第一句改得更直接，让阅卷人立刻听到你的立场或完成度。",
        "尽量把理由句说完整，不要只给结论不给解释。",
    ])

    state.update({
        "task_type": normalized,
        "prompt_text": prompt_text,
        "prompt_payload": prompt_item,
        "last_sample_style": normalized_style,
        "last_sample_mode": normalized_mode,
        "last_model_answer": model_answer,
        "last_model_generated_at": datetime.now().isoformat(),
    })
    _save_state(state)

    if normalized == "listen_repeat":
        lines = [
            "🎤 TOEFL 口语范例（复述讲解版）",
            f"题型: {normalized} ({SPEAKING_TASK_SPECS[normalized]['label_cn']})",
            "讲解模式: 听后复述题不区分保守/平衡/激进，也不做词库强化，已自动切到复述讲解模式。",
            "",
            "当前题目:",
            prompt_text,
            "",
            "标准答案:",
            model_answer,
        ]
        if high_score_keys:
            lines.append("")
            lines.append("高分关键:")
            for idx, item in enumerate(high_score_keys[:4], 1):
                lines.append(f"  {idx}. {item}")
        _format_repeat_sections(lines, model_answer, learner_answer)
        return "\n".join(lines)

    lines = [
        "🎤 TOEFL 口语范例（增强版）",
        f"题型: {normalized} ({SPEAKING_TASK_SPECS[normalized]['label_cn']})",
        f"风格: {style_cn}",
        f"模式: {mode_cn}",
        "",
        "当前题目:",
        prompt_text,
        "",
        "标准答案:",
        model_answer,
    ]

    local_vocab_summary = _local_vocab_mode_summary(normalized, normalized_style, normalized_mode, local_words)
    if local_vocab_summary:
        lines.append("")
        lines.append("本地词库模式:")
        for idx, item in enumerate(local_vocab_summary[:3], 1):
            lines.append(f"  {idx}. {item}")

    _format_common_sections(lines, high_score_keys, naturalness_feedback, advanced_vocabulary_feedback, advanced_structure_feedback, local_vocab_candidates, local_vocab_usage_notes, advanced_expression_diff, useful_phrases, upgrade_from_learner)
    return "\n".join(lines)


def score_answer(answer_text: str, task_type: str | None = None, sample_style: str | None = None, sample_mode: str | None = None) -> str:
    answer_text = (answer_text or "").strip()
    state = _load_state()
    normalized = _normalize_task_type(task_type) or _normalize_task_type(state.get("task_type")) or "take_interview"
    spec = SPEAKING_TASK_SPECS.get(normalized)
    if not spec:
        return "⚠️ 口语题型不支持。发送 !eng speak types 查看全部题型。"

    if _word_count(answer_text) < int(spec.get("min_words") or 20):
        if normalized == "listen_repeat":
            return "🎤 这道听后复述太短了。请尽量把原句完整复述出来，我再给你评分。"
        return "🎤 这道模拟采访回答太短了。建议至少说到 85 词左右，再发来我给你详细评分。"

    prompt_text = (state.get("prompt_text") or "").strip()
    if not prompt_text:
        return f"⚠️ 还没有当前口语题目。先发送 !eng speak prompt {normalized}。"
    if task_type and _normalize_task_type(state.get("task_type")) not in {None, normalized}:
        return f"⚠️ 当前最近题目不是 {normalized}。先发送 !eng speak prompt {normalized} 再作答。"

    normalized_style = _normalize_sample_style(sample_style or state.get("last_sample_style")) or DEFAULT_SAMPLE_STYLE
    normalized_mode = _normalize_sample_mode(sample_mode or state.get("last_sample_mode")) or DEFAULT_SAMPLE_MODE
    if normalized == "listen_repeat":
        normalized_style = DEFAULT_SAMPLE_STYLE
        normalized_mode = DEFAULT_SAMPLE_MODE
    local_words = [] if normalized == "listen_repeat" else _load_local_vocab_candidates(normalized, normalized_style, normalized_mode)
    suggestion_words = [] if normalized == "listen_repeat" else (local_words or _load_general_local_vocab_candidates(limit=8))

    if not is_configured():
        return "⚠️ 当前 LLM 未配置，无法进行口语评分。请检查 llm_config.json。"

    system_prompt, user_prompt = _build_scoring_prompt(normalized, prompt_text, answer_text, normalized_style, normalized_mode, local_words)
    result = _run_json([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], temperature=0.2, max_tokens=2200)
    if not result:
        return "⚠️ 口语评分暂时失败，请稍后再试。"

    subscores = result.get("subscores") or {}
    overall_score = _coerce_float(result.get("overall_score"), 0.0)
    delivery = _coerce_float(subscores.get("delivery"), 0.0)
    language_use = _coerce_float(subscores.get("language_use"), 0.0)
    topic_development = _coerce_float(subscores.get("topic_development"), 0.0)

    strengths = _clean_feedback_list(result.get("strengths"))
    weaknesses = _clean_feedback_list(result.get("weaknesses"))
    sentence_issues = _clean_dict_list(result.get("sentence_issues"), ["quote", "problem", "suggestion"]) or _fallback_sentence_issues(answer_text)
    repetition_feedback = _clean_feedback_list(result.get("repetition_feedback")) or _fallback_repetition_feedback(answer_text)
    naturalness_feedback = _clean_feedback_list(result.get("naturalness_feedback")) or [
        "这份回答意思基本清楚，但还可以再增加更自然的口语连接。",
        "一些句子像翻译腔，建议多用更顺手的英语表达方式。",
    ]
    advanced_vocabulary_feedback = _clean_feedback_list(result.get("advanced_vocabulary_feedback")) or _fallback_advanced_vocabulary_feedback(normalized_style)
    advanced_structure_feedback = _clean_feedback_list(result.get("advanced_structure_feedback")) or _fallback_advanced_structure_feedback(normalized)
    high_score_keys = _clean_feedback_list(result.get("high_score_keys")) or _fallback_high_score_keys(normalized)
    model_answer = _strip_model_artifacts(result.get("model_answer") or "") or _fallback_model_answer(normalized, state.get("prompt_payload") if isinstance(state.get("prompt_payload"), dict) else None)
    local_vocab_candidates = [] if normalized == "listen_repeat" else (_clean_dict_list(result.get("local_vocab_candidates"), ["word", "meaning", "why_fit"]) or _fallback_local_vocab_candidates(prompt_text, suggestion_words))
    local_vocab_usage_notes = [] if normalized == "listen_repeat" else (_clean_dict_list(result.get("local_vocab_usage_notes"), ["word", "meaning", "how_used"]) or _fallback_local_vocab_usage_notes(model_answer, local_words))
    useful_phrases = [] if normalized == "listen_repeat" else _curate_useful_phrases(normalized_style, model_answer, _clean_feedback_list(result.get("useful_phrases")))
    advanced_expression_diff = [] if normalized == "listen_repeat" else (_clean_dict_list(result.get("advanced_expression_diff"), ["learner_expression", "model_expression", "why_better"]) or _fallback_advanced_expression_diff(answer_text, useful_phrases))
    upgrade_from_learner = [] if normalized == "listen_repeat" else (_clean_feedback_list(result.get("upgrade_from_learner")) or [
        "先把核心立场说得更直接，再补理由。",
        "尽量把普通词替换成更精确的评价词，让语言层次更明显。",
    ])
    next_drill = _strip_model_artifacts(result.get("next_drill") or "")

    state.update({
        "task_type": normalized,
        "prompt_text": prompt_text,
        "last_answer_text": answer_text,
        "last_answer_task_type": normalized,
        "last_answer_prompt_text": prompt_text,
        "last_feedback": result,
        "last_sample_style": normalized_style,
        "last_sample_mode": normalized_mode,
    })
    _save_state(state)

    try:
        db = SessionLocal()
        try:
            db.add(SpeakingScore(
                prompt_text=prompt_text,
                answer_text=answer_text,
                overall_score=overall_score,
                delivery=delivery,
                language_use=language_use,
                topic_development=topic_development,
                feedback_json=json.dumps(result, ensure_ascii=False),
                created_at=datetime.now(),
            ))
            db.add(StudyEvent(
                event_type="speaking_scored",
                payload_json=json.dumps({
                    "task_type": normalized,
                    "overall": overall_score,
                    "subscores": {
                        "delivery": delivery,
                        "language_use": language_use,
                        "topic_development": topic_development,
                    },
                    "word_count": _word_count(answer_text),
                }, ensure_ascii=False),
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        pass

    lines = [
        "🎤 TOEFL 口语评分（2026 口语结构）",
        f"题型: {normalized} ({spec['label_cn']})",
        "",
        "当前题目:",
        prompt_text,
        "",
        f"总分: {overall_score}/4",
        "分项:",
        f"  • Delivery: {delivery}/4",
        f"  • Language Use: {language_use}/4",
        f"  • Topic Development: {topic_development}/4",
    ]

    if strengths:
        lines.append("")
        lines.append("亮点:")
        for item in strengths[:4]:
            lines.append(f"  ✓ {item}")
    if weaknesses:
        lines.append("")
        lines.append("待改进:")
        for item in weaknesses[:4]:
            lines.append(f"  ✗ {item}")

    if sentence_issues:
        lines.append("")
        lines.append("句子问题:")
        for idx, item in enumerate(sentence_issues[:4], 1):
            quote = (item.get("quote") or "").strip()
            problem = (item.get("problem") or "").strip()
            suggestion = (item.get("suggestion") or "").strip()
            if quote:
                lines.append(f"  {idx}. {quote}")
            if problem:
                lines.append(f"     - 问题: {problem}")
            if suggestion:
                lines.append(f"     - 建议: {suggestion}")

    if repetition_feedback:
        lines.append("")
        lines.append("重复性:")
        for idx, item in enumerate(repetition_feedback[:3], 1):
            lines.append(f"  {idx}. {item}")

    if normalized == "listen_repeat":
        _format_repeat_sections(lines, model_answer, answer_text)
        lines.append("")
        lines.append("标准答案:")
        lines.append(model_answer)
        if next_drill:
            lines.append("")
            lines.append(f"下一步练习: {next_drill}")
        return "\n".join(lines)

    local_vocab_summary = _local_vocab_mode_summary(normalized, normalized_style, normalized_mode, local_words)
    if local_vocab_summary:
        lines.append("")
        lines.append("本地词库模式:")
        for idx, item in enumerate(local_vocab_summary[:3], 1):
            lines.append(f"  {idx}. {item}")

    _format_common_sections(lines, high_score_keys, naturalness_feedback, advanced_vocabulary_feedback, advanced_structure_feedback, local_vocab_candidates, local_vocab_usage_notes, advanced_expression_diff, useful_phrases, upgrade_from_learner)

    lines.append("")
    lines.append("标准答案:")
    lines.append(model_answer)
    if next_drill:
        lines.append("")
        lines.append(f"下一步练习: {next_drill}")
    return "\n".join(lines)


def handle_speaking_command(args: str) -> str:
    text = (args or "").strip()
    if not text:
        return start_prompt()

    lowered = text.lower()
    if lowered in {"help", "?", "说明", "用法"}:
        return speak_help()
    if lowered in {"types", "type", "题型"}:
        return list_task_types()

    if lowered.startswith("sample") or lowered.startswith("model") or lowered in {"sample", "model", "范例", "范文"}:
        task = ""
        if lowered.startswith("sample"):
            task = text[6:].strip()
        elif lowered.startswith("model"):
            task = text[5:].strip()
        parsed_task, parsed_style, parsed_mode = _parse_sample_request(task)
        return generate_model_answer(parsed_task if parsed_task else None, parsed_style, parsed_mode)

    if lowered.startswith("prompt") or lowered in {"new", "题", "题目"}:
        task = "" if lowered in {"new", "题", "题目"} else text[6:].strip()
        parsed_task, topic_hint = _parse_prompt_request(task)
        return start_prompt(parsed_task if parsed_task else None, topic_hint)

    if lowered.startswith("submit"):
        content = text[6:].strip()
        if not content:
            return "⚠️ 用法: !eng speak submit <你的英文回答>"
        return score_answer(content)

    if lowered.startswith("answer"):
        content = text[6:].strip()
        if not content:
            return "⚠️ 用法: !eng speak answer <你的英文回答>"
        return score_answer(content)

    first, *rest = text.split(None, 1)
    normalized = _normalize_task_type(first)
    if normalized and not rest:
        return start_prompt(normalized)
    if normalized and rest:
        remainder = rest[0].strip()
        if remainder.lower() in {"prompt", "new", "题目", "题"}:
            return start_prompt(normalized)
        if remainder.lower().startswith("prompt"):
            _, topic_hint = _parse_prompt_request(remainder[6:].strip())
            return start_prompt(normalized, topic_hint)
        if remainder.lower() in {"sample", "model", "范例", "范文"}:
            return generate_model_answer(normalized)
        if remainder.lower().startswith("sample") or remainder.lower().startswith("model"):
            inner = remainder.split(None, 1)
            sample_args = inner[1] if len(inner) > 1 else ""
            _, parsed_style, parsed_mode = _parse_sample_request(sample_args)
            return generate_model_answer(normalized, parsed_style, parsed_mode)
        if remainder.lower().startswith("submit"):
            return score_answer(remainder[6:].strip(), task_type=normalized)
        if remainder.lower().startswith("answer"):
            return score_answer(remainder[6:].strip(), task_type=normalized)
        return score_answer(remainder, task_type=normalized)

    return score_answer(text)
