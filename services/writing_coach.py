#!/usr/bin/env python3
"""TOEFL 写作教练（行业增强版）。

功能目标：
1. 支持 2026 新写作题型（Academic Discussion / Email / Build a Sentence）。
2. 兼容备考常用传统题型（Integrated / Independent）。
3. 提供“可选题型出题 + 复现实战风格 + 双通道评分校准 + 改写建议”。
"""
from __future__ import annotations

import json
import random
import re
from datetime import datetime
from pathlib import Path
from statistics import mean

from database import SessionLocal
from models import EssayScore, StudyEvent
from services.llm_client import chat_completion, chat_json, is_configured


project_root = Path(__file__).resolve().parent.parent
essay_state_file = project_root / "data" / "essay_state.json"
sample_bank_file = project_root / "data" / "writing_sample_bank.json"


TASK_SPECS = {
    "academic_discussion": {
        "label": "Write for an Academic Discussion",
        "min_words": 90,
        "target_words": "120-180",
        "time_limit": "10 min",
        "description": "回应教授问题并与两位同学观点互动，表达并论证自己的立场。",
    },
    "email": {
        "label": "Write an Email",
        "min_words": 80,
        "target_words": "100-150",
        "time_limit": "10 min",
        "description": "在学术/校园场景中写功能型邮件，要求目的明确、语气得体、结构清晰。",
    },
    "build_sentence": {
        "label": "Build a Sentence",
        "min_words": 6,
        "target_words": "1 sentence",
        "time_limit": "2-3 min",
        "description": "将给定词块重组为语法正确、语义自然的一句话。",
    },
    "integrated": {
        "label": "Integrated Essay (Legacy)",
        "min_words": 180,
        "target_words": "220-320",
        "time_limit": "20 min",
        "description": "概括听力并解释其如何反驳阅读三点观点。",
    },
    "independent": {
        "label": "Independent Essay (Legacy)",
        "min_words": 260,
        "target_words": "320-420",
        "time_limit": "30 min",
        "description": "表达个人立场并给出具体理由与例子。",
    },
}


TASK_ALIASES = {
    "academic": "academic_discussion",
    "discussion": "academic_discussion",
    "academic_discussion": "academic_discussion",
    "ad": "academic_discussion",
    "email": "email",
    "mail": "email",
    "build": "build_sentence",
    "sentence": "build_sentence",
    "build_sentence": "build_sentence",
    "integrated": "integrated",
    "int": "integrated",
    "independent": "independent",
    "ind": "independent",
    "opinion": "independent",
}

DEFAULT_SAMPLE_STYLE = "balanced"

STYLE_ALIASES = {
    "conservative": "conservative",
    "safe": "conservative",
    "保守": "conservative",
    "保守版": "conservative",
    "balanced": "balanced",
    "default": "balanced",
    "平衡": "balanced",
    "平衡版": "balanced",
    "aggressive": "aggressive",
    "bold": "aggressive",
    "激进": "aggressive",
    "激进版": "aggressive",
}

STYLE_VOCAB_POLICIES = {
    "conservative": {
        "target_density_en": "Use mostly clear common academic vocabulary. Limit advanced wording to 1-2 transparent phrases in the whole response.",
        "target_density_cn": "保守版会刻意控制生僻词比例：以清楚稳妥为主，整篇只保留 1-2 个容易理解的进阶短语。",
        "ceiling_en": "Avoid obscure or showy words. Precision matters more than sophistication.",
        "ceiling_cn": "不要堆冷词，不靠炫词汇拿分，重点是准确和得体。",
        "level_cn": "稳健学术表达",
    },
    "balanced": {
        "target_density_en": "Use a moderate number of polished academic collocations, usually 2-4 notable phrases across the response.",
        "target_density_cn": "平衡版会放入适量中高阶搭配，通常全篇 2-4 处，既显得成熟又不显堆砌。",
        "ceiling_en": "Keep wording polished but immediately understandable to a TOEFL reader.",
        "ceiling_cn": "词汇要正式但一读就懂，不能为了高级感牺牲自然度。",
        "level_cn": "中高阶学术搭配",
    },
    "aggressive": {
        "target_density_en": "Allow more forceful advanced phrasing, usually 4-6 strong expressions, but keep the response readable and natural.",
        "target_density_cn": "激进版允许更多高阶强势表达，通常全篇 4-6 处，但仍要保持可读，不能变成 GRE 式冷词堆砌。",
        "ceiling_en": "Prefer sharp, useful academic phrasing over obscure GRE-like vocabulary.",
        "ceiling_cn": "优先使用有论证力度的高级短语，而不是生硬冷僻的大词。",
        "level_cn": "高阶强观点表达",
    },
}

DEFAULT_SAMPLE_MODE = "standard"

SAMPLE_MODE_ALIASES = {
    "standard": "standard",
    "normal": "standard",
    "default": "standard",
    "标准模式": "standard",
    "普通": "standard",
    "普通模式": "standard",
    "默认": "standard",
    "默认模式": "standard",
    "词库": "local_vocab",
    "词库模式": "local_vocab",
    "词库强化": "local_vocab",
    "本地词库": "local_vocab",
    "本地词库模式": "local_vocab",
    "词汇库": "local_vocab",
    "单词库": "local_vocab",
    "vocab": "local_vocab",
    "local": "local_vocab",
    "local_vocab": "local_vocab",
    "boost": "local_vocab",
}


PROMPT_BANK = {
    "academic_discussion": [
        {
            "professor": "Professor: Should universities replace large final exams with project-based assessments in most courses?",
            "student_a": "Student A: Yes. Projects measure real-world skills better than one-time exams.",
            "student_b": "Student B: No. Exams are fairer because every student answers under the same conditions.",
            "task": "Write a post for the discussion board. State your position and explain why.",
        },
        {
            "professor": "Professor: Should first-year students be required to live on campus?",
            "student_a": "Student A: Yes. It helps students build community quickly and avoid loneliness.",
            "student_b": "Student B: No. Off-campus housing can be cheaper and teaches independence.",
            "task": "Contribute your opinion in 120-180 words with specific support.",
        },
        {
            "professor": "Professor: Should AI writing assistants be allowed in university writing classes?",
            "student_a": "Student A: Yes, if used responsibly. They can help students revise and learn.",
            "student_b": "Student B: No. Students may rely on them too much and stop thinking critically.",
            "task": "Write your response and address at least one peer idea directly.",
        },
    ],
    "email": [
        {
            "context": "You are taking History 201. Your group presentation partner dropped the class.",
            "recipient": "Email your professor.",
            "goals": [
                "Explain the problem clearly.",
                "Request one practical solution.",
                "Show a polite and professional tone.",
            ],
        },
        {
            "context": "Your campus library will reduce opening hours during finals week.",
            "recipient": "Write an email to the student services office.",
            "goals": [
                "Describe why this policy is difficult for students.",
                "Suggest an alternative plan.",
                "Close with a respectful call to action.",
            ],
        },
        {
            "context": "You missed a lab class because of a medical appointment.",
            "recipient": "Write an email to your lab instructor.",
            "goals": [
                "State the reason briefly and honestly.",
                "Ask how to make up the missed work.",
                "Offer a concrete timeline.",
            ],
        },
    ],
    "build_sentence": [
        {
            "fragments": [
                "although online courses are flexible",
                "many students",
                "still prefer",
                "face-to-face classes",
                "for immediate feedback",
            ],
            "instruction": "Reorder the fragments into one natural and grammatically correct sentence.",
        },
        {
            "fragments": [
                "the committee",
                "decided to postpone",
                "the new policy",
                "until more data",
                "became available",
            ],
            "instruction": "Build one complete sentence using all fragments.",
        },
        {
            "fragments": [
                "if universities want",
                "to reduce student stress",
                "they should",
                "spread assessments",
                "across the semester",
            ],
            "instruction": "Arrange all pieces to make one clear sentence.",
        },
    ],
    "integrated": [
        {
            "reading_topic": "The reading argues that replacing city buses with self-driving electric shuttles will improve urban transport.",
            "reading_points": [
                "The shuttles lower long-term operating costs.",
                "Automation reduces human error and accidents.",
                "Smaller vehicles provide more flexible routes.",
            ],
            "lecture_points": [
                "Maintenance and software updates create new expenses.",
                "Sensors perform poorly in heavy rain and snow.",
                "Too many small vehicles may increase congestion.",
            ],
            "task": "Summarize the lecture and explain how it challenges the reading.",
        },
        {
            "reading_topic": "The reading claims that remote work should become the default model for universities' administrative staff.",
            "reading_points": [
                "Productivity rises because employees face fewer interruptions.",
                "Universities save money on office space.",
                "Remote work improves staff satisfaction and retention.",
            ],
            "lecture_points": [
                "Cross-team coordination often slows down online.",
                "Hidden costs appear in cybersecurity and home-office support.",
                "Junior staff lose mentoring opportunities when offices are empty.",
            ],
            "task": "Summarize the lecture and show how each point responds to the reading.",
        },
    ],
    "independent": [
        {
            "prompt": "Do you agree or disagree with the following statement? Universities should require all students to take at least one course in public speaking.",
        },
        {
            "prompt": "Some people think it is better to spend money improving public transportation, while others think building more roads is better. Which do you prefer and why?",
        },
        {
            "prompt": "Do you agree or disagree: students learn more effectively when they work in groups than when they study alone.",
        },
    ],
}

PROMPT_ITEM_METADATA = {
    "academic_discussion": [
        {"prompt_id": "ad_project_assessment", "topic_tags": ["education", "assessment"], "topic_label_cn": "教育评估"},
        {"prompt_id": "ad_campus_housing", "topic_tags": ["campus", "humanities"], "topic_label_cn": "校园生活"},
        {"prompt_id": "ad_ai_writing", "topic_tags": ["technology", "education"], "topic_label_cn": "教育科技"},
    ],
    "email": [
        {"prompt_id": "email_history_partner", "topic_tags": ["humanities", "campus"], "topic_label_cn": "人文课程"},
        {"prompt_id": "email_library_hours", "topic_tags": ["campus", "study_support"], "topic_label_cn": "学习支持"},
        {"prompt_id": "email_lab_makeup", "topic_tags": ["biology", "science", "campus"], "topic_label_cn": "实验课程"},
    ],
    "build_sentence": [
        {"prompt_id": "bs_online_courses", "topic_tags": ["technology", "education"], "topic_label_cn": "在线学习"},
        {"prompt_id": "bs_policy_delay", "topic_tags": ["policy", "humanities"], "topic_label_cn": "政策决策"},
        {"prompt_id": "bs_reduce_stress", "topic_tags": ["education", "campus"], "topic_label_cn": "校园压力"},
    ],
    "integrated": [
        {"prompt_id": "int_shuttle_transport", "topic_tags": ["technology", "transportation"], "topic_label_cn": "交通科技"},
        {"prompt_id": "int_remote_work", "topic_tags": ["technology", "workplace"], "topic_label_cn": "办公模式"},
    ],
    "independent": [
        {"prompt_id": "ind_public_speaking", "topic_tags": ["education", "humanities"], "topic_label_cn": "表达能力"},
        {"prompt_id": "ind_transportation", "topic_tags": ["technology", "policy"], "topic_label_cn": "公共政策"},
        {"prompt_id": "ind_group_learning", "topic_tags": ["education", "campus"], "topic_label_cn": "学习方式"},
    ],
}

PROMPT_TOPIC_ALIASES = {
    "生物": "biology",
    "biology": "biology",
    "bio": "biology",
    "医学": "biology",
    "medical": "biology",
    "人文": "humanities",
    "humanities": "humanities",
    "history": "humanities",
    "历史": "humanities",
    "文学": "humanities",
    "科技": "technology",
    "technology": "technology",
    "tech": "technology",
    "ai": "technology",
    "人工智能": "technology",
    "教育": "education",
    "education": "education",
    "校园": "campus",
    "campus": "campus",
    "政策": "policy",
    "policy": "policy",
    "交通": "transportation",
    "transportation": "transportation",
    "工作": "workplace",
    "职场": "workplace",
    "workplace": "workplace",
}

PROMPT_TOPIC_LABELS = {
    "biology": "生物",
    "humanities": "人文",
    "technology": "科技",
    "education": "教育",
    "campus": "校园",
    "policy": "政策",
    "transportation": "交通",
    "workplace": "职场",
    "assessment": "评估",
    "study_support": "学习支持",
}

LOW_VALUE_USEFUL_PHRASES = {
    "would it be possible",
    "please let me know",
    "i would appreciate",
    "could you please",
    "i am writing to",
    "thank you for your time",
    "due to",
    "fall behind",
    "i would like to suggest",
}


RUBRIC_ANCHORS = """
Realistic anchor guidance (ETS-style descriptors + public prep examples):
- 5: Fully addresses task purpose, strong organization, precise language control, very few non-systematic errors.
- 4: Addresses task effectively with minor lapses, generally clear progression, occasional language/grammar issues.
- 3: Partially developed response, uneven support or organization, noticeable errors but meaning mostly understandable.
- 2: Limited development, weak cohesion, frequent lexical/grammatical problems that reduce clarity.
- 1-0: Minimal or off-task response, severe language control issues, meaning often unclear.
"""


def _ensure_state_dir() -> None:
    essay_state_file.parent.mkdir(parents=True, exist_ok=True)


def _load_state() -> dict:
    if not essay_state_file.exists():
        return {}
    try:
        with open(essay_state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    _ensure_state_dir()
    with open(essay_state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _load_prompt_history(state: dict) -> dict:
    raw = state.get("prompt_history")
    return raw if isinstance(raw, dict) else {}


def _task_prompt_history(state: dict, task_type: str) -> list[dict]:
    history = _load_prompt_history(state)
    records = history.get(task_type)
    return records if isinstance(records, list) else []


def _remember_prompt_history(state: dict, task_type: str, prompt_id: str, prompt_preview: str, topic_tags: list[str] | None = None) -> None:
    history = _load_prompt_history(state)
    records = _task_prompt_history(state, task_type)
    topic_tags = [item for item in (topic_tags or []) if isinstance(item, str) and item.strip()]
    records.append({
        "prompt_id": prompt_id,
        "prompt_preview": prompt_preview,
        "topic_tags": topic_tags,
        "used_at": datetime.now().isoformat(),
    })
    history[task_type] = records[-30:]
    state["prompt_history"] = history


def _used_prompt_ids(state: dict, task_type: str) -> set[str]:
    return {
        item.get("prompt_id")
        for item in _task_prompt_history(state, task_type)
        if isinstance(item, dict) and isinstance(item.get("prompt_id"), str)
    }


def _recent_prompt_previews(state: dict, task_type: str, limit: int = 6) -> list[str]:
    previews = []
    for item in _task_prompt_history(state, task_type)[-limit:]:
        if isinstance(item, dict):
            preview = (item.get("prompt_preview") or "").strip()
            if preview:
                previews.append(preview)
    return previews


def _normalize_topic_hints(topic_hint: str | None) -> list[str]:
    raw = (topic_hint or "").strip()
    if not raw:
        return []
    lowered = raw.lower()
    hits = []
    for alias, canonical in PROMPT_TOPIC_ALIASES.items():
        haystack = raw if any(ord(ch) > 127 for ch in alias) else lowered
        needle = alias if any(ord(ch) > 127 for ch in alias) else alias.lower()
        if needle in haystack and canonical not in hits:
            hits.append(canonical)
    return hits


def _prompt_meta(task_type: str, index: int) -> dict:
    items = PROMPT_ITEM_METADATA.get(task_type) or []
    if 0 <= index < len(items) and isinstance(items[index], dict):
        return items[index]
    return {}


def _prompt_item_id(task_type: str, index: int, item: dict) -> str:
    meta = _prompt_meta(task_type, index)
    prompt_id = meta.get("prompt_id") or item.get("prompt_id")
    if isinstance(prompt_id, str) and prompt_id.strip():
        return prompt_id
    return f"{task_type}_{index}"


def _prompt_item_topics(task_type: str, index: int, item: dict) -> list[str]:
    meta = _prompt_meta(task_type, index)
    topics = meta.get("topic_tags") or item.get("topic_tags") or []
    return [str(topic).strip().lower() for topic in topics if str(topic).strip()]


def _prompt_topic_label(task_type: str, index: int, item: dict) -> str:
    meta = _prompt_meta(task_type, index)
    label = meta.get("topic_label_cn") or item.get("topic_label_cn") or ""
    return str(label).strip()


def _normalize_fragment_list(fragments: list[str]) -> list[str]:
    normalized = []
    for fragment in fragments or []:
        text = " ".join(str(fragment).strip().split())
        if text:
            normalized.append(text)
    return normalized


def _shuffle_fragments_nontrivial(fragments: list[str]) -> list[str]:
    ordered = _normalize_fragment_list(fragments)
    if len(ordered) <= 1:
        return ordered

    for _ in range(12):
        shuffled = ordered[:]
        random.shuffle(shuffled)
        if shuffled != ordered:
            return shuffled

    # 极端情况下 random 连续命中原序，强制旋转一次，确保题目不是有序展示。
    return ordered[1:] + ordered[:1]


def _compose_build_sentence_answer(ordered_fragments: list[str]) -> str:
    sentence = " ".join(_normalize_fragment_list(ordered_fragments)).strip()
    sentence = re.sub(r"\s+([,.;!?])", r"\1", sentence)
    if sentence:
        sentence = sentence[0].upper() + sentence[1:]
        if sentence[-1] not in ".!?":
            sentence += "."
    return sentence


def _build_sentence_rationale(ordered_fragments: list[str]) -> list[str]:
    ordered = _normalize_fragment_list(ordered_fragments)
    if not ordered:
        return []

    notes = []
    first_lower = ordered[0].lower()
    if re.match(r"^(although|if|when|while|because|since|unless)\b", first_lower):
        notes.append(f"先放“{ordered[0]}”，它是从属结构开头，先交代让步/条件背景。")
    else:
        notes.append(f"先放“{ordered[0]}”，它负责搭起句子开头的信息框架。")

    if len(ordered) >= 2:
        if first_lower.startswith("if ") and ordered[1].lower().startswith("to "):
            notes.append(f"再接“{ordered[1]}”补全 if 从句里的目的/结果成分，让条件从句语义完整。")
        else:
            notes.append(f"再接“{ordered[1]}”让句子主干继续展开，读者能更快抓住核心信息。")
    if len(ordered) >= 3:
        if first_lower.startswith("if "):
            notes.append(f"随后切回主句，用“{ordered[2]}”搭起主句骨架。")
        else:
            notes.append(f"随后放“{ordered[2]}”补全主干信息，句子逻辑衔接更顺。")
    if len(ordered) >= 4:
        tail = " / ".join(ordered[3:])
        notes.append(f"最后补上“{tail}”完成宾语与补充成分，句意才完整自然。")
    if len(ordered) >= 5 and re.match(r"^(for|to|until|because|with|in|on|at)\b", ordered[-1].lower()):
        notes.append(f"“{ordered[-1]}”放句尾最自然，它一般提供目的、原因或条件等补充信息。")

    return notes[:5]


def _build_sentence_payload(item: dict) -> dict:
    ordered_fragments = _normalize_fragment_list(item.get("ordered_fragments") or item.get("fragments") or [])
    if not ordered_fragments:
        return {}

    return {
        "instruction": str(item.get("instruction") or "Reorder the fragments into one natural and grammatically correct sentence.").strip(),
        "ordered_fragments": ordered_fragments,
        "scrambled_fragments": _shuffle_fragments_nontrivial(ordered_fragments),
        "answer_sentence": _compose_build_sentence_answer(ordered_fragments),
        "rationale": _build_sentence_rationale(ordered_fragments),
    }


def _render_build_sentence_prompt(item: dict) -> tuple[str, dict]:
    payload = _build_sentence_payload(item)
    if not payload:
        return "", {}

    fragments_block = "\n".join(
        f"{idx}. {fragment}" for idx, fragment in enumerate(payload["scrambled_fragments"], 1)
    )
    prompt_text = "\n".join([
        f"Task Type: {TASK_SPECS['build_sentence']['label']}",
        f"Time: {TASK_SPECS['build_sentence']['time_limit']} | Output: {TASK_SPECS['build_sentence']['target_words']}",
        "",
        "Fragments (shuffled):",
        fragments_block,
        f"Instruction: {payload['instruction']}",
    ])
    return prompt_text, payload


def _topic_hint_display(topic_hint: str | None) -> str:
    hints = _normalize_topic_hints(topic_hint)
    if hints:
        return " / ".join(PROMPT_TOPIC_LABELS.get(item, item) for item in hints)
    return (topic_hint or "").strip()


def _is_low_value_useful_phrase(text: str) -> bool:
    lowered = " ".join((text or "").strip().lower().split())
    if not lowered:
        return True
    return any(phrase in lowered for phrase in LOW_VALUE_USEFUL_PHRASES)


def _load_sample_bank() -> dict:
    if not sample_bank_file.exists():
        return {}
    try:
        with open(sample_bank_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _sample_profile(task_type: str) -> dict:
    bank = _load_sample_bank()
    data = bank.get(task_type)
    return data if isinstance(data, dict) else {}


def _normalize_sample_style(sample_style: str | None) -> str | None:
    if not sample_style:
        return None
    key = sample_style.strip().lower().replace("-", "_").replace(" ", "_")
    return STYLE_ALIASES.get(key)


def _normalize_sample_mode(sample_mode: str | None) -> str | None:
    if not sample_mode:
        return None
    key = sample_mode.strip().lower().replace("-", "_").replace(" ", "_")
    return SAMPLE_MODE_ALIASES.get(key)


def _sample_style_profile(task_type: str, sample_style: str | None = None) -> dict:
    profile = _sample_profile(task_type)
    variants = profile.get("style_variants") or {}
    if not isinstance(variants, dict):
        return {}
    normalized_style = _normalize_sample_style(sample_style) or DEFAULT_SAMPLE_STYLE
    data = variants.get(normalized_style)
    return data if isinstance(data, dict) else {}


def _sample_mode_label(sample_mode: str | None) -> str:
    normalized = _normalize_sample_mode(sample_mode) or DEFAULT_SAMPLE_MODE
    return "词库强化" if normalized == "local_vocab" else "标准模式"


def _style_lexical_control(task_type: str, sample_style: str | None = None) -> dict:
    style_profile = _sample_style_profile(task_type, sample_style)
    control = style_profile.get("lexical_control")
    return control if isinstance(control, dict) else {}


def _coerce_int(value, fallback: int) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return fallback


def _argument_move_specs(task_type: str) -> list[dict]:
    profile = _sample_profile(task_type)
    raw_moves = profile.get("argument_moves") or []
    return [item for item in raw_moves if isinstance(item, dict)]


def _style_display_name(task_type: str, sample_style: str | None = None) -> tuple[str, str]:
    normalized_style = _normalize_sample_style(sample_style) or DEFAULT_SAMPLE_STYLE
    style_profile = _sample_style_profile(task_type, normalized_style)
    style_cn = style_profile.get("label_cn") or normalized_style
    return normalized_style, style_cn


def _style_vocab_policy(task_type: str, sample_style: str | None = None) -> dict:
    normalized_style = _normalize_sample_style(sample_style) or DEFAULT_SAMPLE_STYLE
    base = dict(STYLE_VOCAB_POLICIES.get(normalized_style, STYLE_VOCAB_POLICIES[DEFAULT_SAMPLE_STYLE]))
    override = _style_lexical_control(task_type, normalized_style)
    if isinstance(override, dict):
        base.update({key: value for key, value in override.items() if value})
    return base


def _style_vocab_targets(task_type: str, sample_style: str | None = None, sample_mode: str | None = None) -> dict:
    control = _style_lexical_control(task_type, sample_style)
    normalized_mode = _normalize_sample_mode(sample_mode) or DEFAULT_SAMPLE_MODE
    advanced_count = _coerce_int(control.get("recommended_advanced_count"), 3)
    local_vocab_count = _coerce_int(control.get("recommended_local_vocab_count"), 1)
    boosted_local_vocab_count = _coerce_int(control.get("boosted_local_vocab_count"), max(local_vocab_count, 2))
    minimum_boosted_targets = {
        "academic_discussion": 3,
        "email": 3,
        "integrated": 4,
        "independent": 4,
        "build_sentence": 1,
    }
    boosted_local_vocab_count = max(boosted_local_vocab_count, minimum_boosted_targets.get(task_type, 3))
    return {
        "recommended_advanced_count": advanced_count,
        "recommended_local_vocab_count": local_vocab_count,
        "active_local_vocab_count": boosted_local_vocab_count if normalized_mode == "local_vocab" else local_vocab_count,
        "advanced_range": (control.get("recommended_advanced_range") or str(advanced_count)).strip(),
        "local_vocab_range": (control.get("recommended_local_vocab_range") or str(local_vocab_count)).strip(),
        "boosted_local_vocab_range": (control.get("boosted_local_vocab_range") or str(boosted_local_vocab_count)).strip(),
    }


def _format_sample_profile(task_type: str, sample_style: str | None = None) -> str:
    profile = _sample_profile(task_type)
    if not profile:
        return "No local sample profile available."

    normalized_style, style_cn = _style_display_name(task_type, sample_style)
    style_profile = _sample_style_profile(task_type, normalized_style)
    vocab_policy = _style_vocab_policy(task_type, normalized_style)
    vocab_targets = _style_vocab_targets(task_type, normalized_style)

    lines = []
    voice = profile.get("voice")
    if voice:
        lines.append(f"Voice: {voice}")

    structure = profile.get("structure") or []
    if structure:
        lines.append("Structure:")
        lines.extend([f"- {item}" for item in structure[:6]])

    lines.append(f"Selected style: {normalized_style} ({style_cn})")
    description = style_profile.get("description")
    if description:
        lines.append(f"Style focus: {description}")

    lines.append("Vocabulary control:")
    lines.append(f"- {vocab_policy['target_density_en']}")
    lines.append(f"- {vocab_policy['ceiling_en']}")
    lines.append(f"- Recommended advanced expressions: about {vocab_targets['advanced_range']}")
    lines.append(f"- Recommended learner-vocabulary insertions when vocab mode is enabled: about {vocab_targets['boosted_local_vocab_range']}")

    style_lexis = style_profile.get("lexical_targets") or []
    if style_lexis:
        lines.append("Style expressions:")
        lines.extend([f"- {item}" for item in style_lexis[:6]])

    lexis = profile.get("lexical_targets") or []
    if lexis:
        lines.append("Useful expressions:")
        lines.extend([f"- {item}" for item in lexis[:8]])

    moves = _argument_move_specs(task_type)
    if moves:
        lines.append("Expected argument moves:")
        for item in moves[:6]:
            label = item.get("label") or "move"
            desc = item.get("description") or ""
            lines.append(f"- {label}: {desc}")

    style_sample = (style_profile.get("sample") or "").strip()
    if style_sample:
        lines.append("Selected style anchor:")
        lines.append(style_sample)

    exemplars = profile.get("exemplars") or []
    if exemplars:
        lines.append("Curated local exemplar style:")
        for item in exemplars[:2]:
            title = item.get("title") or "sample"
            essay = (item.get("essay") or "").strip()
            if essay:
                lines.append(f"[{title}] {essay}")

    return "\n".join(lines) if lines else "No local sample profile available."


def _split_sentences(text: str) -> list[str]:
    if not text.strip():
        return []

    sentences: list[str] = []
    for block in re.split(r"\n+", text.strip()):
        piece = block.strip()
        if not piece:
            continue
        chunks = re.split(r"(?<=[.!?])\s+", piece)
        for chunk in chunks:
            normalized = chunk.strip()
            if normalized:
                sentences.append(normalized)
    return sentences


def _style_vocab_summary(task_type: str, sample_style: str | None = None) -> list[str]:
    _, style_cn = _style_display_name(task_type, sample_style)
    policy = _style_vocab_policy(task_type, sample_style)
    targets = _style_vocab_targets(task_type, sample_style)
    return [
        f"{style_cn}词汇策略：{policy['target_density_cn']}",
        f"建议高级表达数量：约 {targets['advanced_range']} 处，可在本地配置里单独调。",
        f"控制上限：{policy['ceiling_cn']}",
    ]


def _local_vocab_mode_summary(task_type: str, sample_style: str | None = None, sample_mode: str | None = None, local_words: list[dict] | None = None) -> list[str]:
    normalized_mode = _normalize_sample_mode(sample_mode) or DEFAULT_SAMPLE_MODE
    targets = _style_vocab_targets(task_type, sample_style, normalized_mode)
    if normalized_mode != "local_vocab":
        return ["当前使用标准模式：按题型与风格生成，不强制植入本地词库。"]
    if not local_words:
        return ["当前使用词库强化模式，但本地词库里暂时没有合适高阶词，本次会自动回退为标准写法。"]
    return [
        f"当前使用词库强化模式：会优先自然植入你本地词库中的高阶词，目标约 {targets['boosted_local_vocab_range']} 个。",
        "这些词会被放进真实论证或邮件功能句里，而不是单独硬塞，方便你顺手学会怎么用。",
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
    lines = [f"LOCAL LEARNER VOCABULARY TO PRIORITIZE (use around {target_count} items if they fit naturally):"]
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


def _find_word_sentence(text: str, word: str) -> str:
    pattern = re.compile(rf"\b{re.escape(word.lower())}\b")
    for sentence in _split_sentences(text):
        if pattern.search(sentence.lower()):
            return sentence
    return ""


def _fallback_local_vocab_usage_notes(model_essay: str, local_words: list[dict]) -> list[dict]:
    notes = []
    essay_lower = (model_essay or "").lower()
    for item in local_words[:8]:
        word = (item.get("word") or "").strip()
        if not word:
            continue
        if re.search(rf"\b{re.escape(word.lower())}\b", essay_lower):
            sentence = _find_word_sentence(model_essay, word)
            notes.append({
                "word": word,
                "meaning": (item.get("definition") or "").strip(),
                "how_used": sentence or f"范文把 {word} 放进了真实句子里，而不是孤立展示。",
            })
    return notes


def _fallback_standard_mode_vocab_suggestions(essay_text: str, local_words: list[dict]) -> list[dict]:
    essay_lower = (essay_text or "").lower()
    suggestions = []
    for item in local_words[:8]:
        word = (item.get("word") or "").strip()
        if not word:
            continue
        if re.search(rf"\b{re.escape(word.lower())}\b", essay_lower):
            continue
        meaning = (item.get("definition") or "").strip()
        example = (item.get("example") or "").strip()
        if example:
            how = f"可以参考这个词在例句里的搭配方式，再把它放进你原文的理由句或解释句里：{example}"
        elif meaning:
            how = f"这个词适合放进你现在的支持句里，用来把意思写得更学术、更精确：{meaning}"
        else:
            how = f"这个词适合放进你原文的支持理由里，让表达更正式。"
        suggestions.append({
            "word": word,
            "meaning": meaning,
            "why_fit": "它和你当前题目的论证方向兼容，放进正文不会显得硬塞。",
            "how_to_use": how,
        })
        if len(suggestions) >= 3:
            break
    return suggestions


def _fallback_local_vocab_candidates(prompt_text: str, local_words: list[dict]) -> list[dict]:
    if not local_words:
        return []
    prompt_lower = (prompt_text or "").lower()
    suggestions = []
    for item in local_words[:8]:
        word = (item.get("word") or "").strip()
        if not word:
            continue
        meaning = (item.get("definition") or "").strip()
        if any(token in prompt_lower for token in ("policy", "debate", "argue", "reason", "impact", "request", "explain")):
            why_fit = f"{word} 适合放进这道题的理由句或解释句里，让论证比 good / important 这类词更具体。"
        else:
            why_fit = f"{word} 可以自然放进正文关键句里，帮助你把意思写得更正式、更精确。"
        suggestions.append({
            "word": word,
            "meaning": meaning,
            "why_fit": why_fit,
        })
        if len(suggestions) >= 4:
            break
    return suggestions


def _fallback_vocab_replacement_suggestions(learner_essay: str, local_vocab_candidates: list[dict]) -> list[dict]:
    if not local_vocab_candidates:
        return []

    learner_lower = (learner_essay or "").lower()
    generic_pool = []
    for candidate in ["important", "good", "bad", "helpful", "big problem", "clear", "common"]:
        if candidate in learner_lower:
            generic_pool.append(candidate)
    if not generic_pool:
        generic_pool = ["important", "good result", "big problem"]

    suggestions = []
    for index, item in enumerate(local_vocab_candidates[:3]):
        word = (item.get("word") or "").strip()
        if not word:
            continue
        generic_expression = generic_pool[min(index, len(generic_pool) - 1)]
        suggestions.append({
            "generic_expression": generic_expression,
            "upgraded_expression": word,
            "reason": f"如果你把 {generic_expression} 这类基础表达换成 {word}，句子会更精确，也更像高分作文里的自然升级。",
        })
    return suggestions


def _looks_like_high_value_phrase(text: str) -> bool:
    tokens = re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)?", text or "")
    if len(tokens) < 2:
        return False
    if len(tokens) >= 3:
        return True
    return any(len(token) >= 7 for token in tokens)


def _curate_useful_phrases(task_type: str, sample_style: str | None, model_essay: str, candidates: list[str], advanced_vocabulary_notes: list[dict]) -> list[str]:
    curated = []

    def _push(phrase: str) -> None:
        cleaned = _strip_model_artifacts(phrase or "").strip()
        if not cleaned:
            return
        lowered = cleaned.lower()
        if _is_low_value_useful_phrase(lowered):
            return
        if not _looks_like_high_value_phrase(cleaned):
            return
        if lowered in {item.lower() for item in curated}:
            return
        curated.append(cleaned)

    for item in candidates or []:
        _push(str(item))
    for note in advanced_vocabulary_notes or []:
        if isinstance(note, dict):
            _push(note.get("expression") or "")

    style_profile = _sample_style_profile(task_type, sample_style)
    for item in style_profile.get("lexical_targets") or []:
        _push(str(item))
    profile = _sample_profile(task_type)
    for item in profile.get("lexical_targets") or []:
        _push(str(item))

    if model_essay:
        for sentence in _split_sentences(model_essay):
            clauses = re.split(r"[,;:]", sentence)
            for clause in clauses:
                chunk = clause.strip()
                if 3 <= len(chunk.split()) <= 10:
                    _push(chunk)
            if len(curated) >= 8:
                break
    return curated[:6]


def _suggest_standard_mode_local_vocab(task_type: str, prompt_text: str, essay_text: str, local_words: list[dict]) -> list[dict]:
    if not local_words:
        return []

    if not is_configured():
        return _fallback_standard_mode_vocab_suggestions(essay_text, local_words)

    lines = []
    for item in local_words[:8]:
        word = (item.get("word") or "").strip()
        if not word:
            continue
        definition = (item.get("definition") or "").strip()
        example = (item.get("example") or "").strip()
        line = f"- {word}"
        if definition:
            line += f": {definition}"
        if example:
            line += f" | Example: {example}"
        lines.append(line)

    messages = [
        {
            "role": "system",
            "content": "You are a TOEFL writing coach. Suggest which words from the learner's local vocabulary bank could have been used naturally in the learner's current essay while staying in standard mode. Return strict JSON only.",
        },
        {
            "role": "user",
            "content": (
                f"Task type: {task_type}\n"
                f"Prompt:\n{_clip(prompt_text, 2200)}\n\n"
                f"Learner essay:\n{_clip(essay_text, 2200)}\n\n"
                "Candidate learner vocabulary:\n"
                + "\n".join(lines)
                + "\n\nReturn strict JSON with one key:\n"
                + "{\n"
                + '  "standard_mode_local_vocab_suggestions": [\n'
                + '    {"word": string, "meaning": string, "why_fit": string, "how_to_use": string}\n'
                + "  ]\n"
                + "}\n\n"
                + "Rules:\n"
                + "- Choose 0-3 words only.\n"
                + "- Do not suggest words already used in the learner essay.\n"
                + "- Keep suggestions realistic for STANDARD mode, so do not overload the essay with difficult vocabulary.\n"
                + "- why_fit and how_to_use must be in Chinese.\n"
                + "- how_to_use should explain where or how the learner could naturally insert the word into the existing essay.\n"
            ),
        },
    ]
    result = chat_json(messages, temperature=0.2, max_tokens=900, timeout=60, retries=1)
    if not result:
        return _fallback_standard_mode_vocab_suggestions(essay_text, local_words)

    suggestions = result.get("standard_mode_local_vocab_suggestions") or []
    cleaned = []
    essay_lower = (essay_text or "").lower()
    for item in suggestions:
        if not isinstance(item, dict):
            continue
        word = _strip_model_artifacts(item.get("word") or "").strip()
        if not word or re.search(rf"\b{re.escape(word.lower())}\b", essay_lower):
            continue
        cleaned.append({
            "word": word,
            "meaning": _strip_model_artifacts(item.get("meaning") or "").strip(),
            "why_fit": _strip_model_artifacts(item.get("why_fit") or "").strip(),
            "how_to_use": _strip_model_artifacts(item.get("how_to_use") or "").strip(),
        })
    return cleaned[:3] or _fallback_standard_mode_vocab_suggestions(essay_text, local_words)


def _fallback_vocabulary_notes(task_type: str, model_essay: str, sample_style: str | None = None) -> list[dict]:
    profile = _sample_profile(task_type)
    style_profile = _sample_style_profile(task_type, sample_style)
    policy = _style_vocab_policy(task_type, sample_style)
    essay_lower = (model_essay or "").lower()

    candidates = []
    for group in (style_profile.get("lexical_targets") or [], profile.get("lexical_targets") or []):
        for item in group:
            if isinstance(item, str) and item not in candidates:
                candidates.append(item)

    selected = [item for item in candidates if item.lower() in essay_lower]
    if not selected:
        selected = candidates[:4]

    notes = []
    for phrase in selected[:4]:
        if _normalize_sample_style(sample_style) == "conservative":
            why = "这个表达比普通口语更正式，但仍然足够透明，适合稳妥拿分。"
        elif _normalize_sample_style(sample_style) == "aggressive":
            why = "这个表达带有更强的判断或执行意味，能拉高语气力度，但还没有生硬到影响可读性。"
        else:
            why = "这个搭配属于常见高分学术短语，正式、自然，而且不会显得刻意堆词。"
        notes.append({
            "expression": phrase,
            "level": policy.get("level_cn") or "高分表达",
            "why_effective": why,
        })
    return notes


def _fallback_sentence_explanations(task_type: str, model_essay: str, sample_style: str | None = None) -> list[dict]:
    sentences = _split_sentences(model_essay)
    if not sentences:
        return []

    _, style_cn = _style_display_name(task_type, sample_style)
    explanations = []
    total = len(sentences)

    for idx, sentence in enumerate(sentences[:8]):
        if idx == 0:
            why = "第一句先完成任务回应，让阅卷人立刻知道立场或写信目的，这属于高分答案最重要的开局动作。"
        elif idx == total - 1:
            why = "最后一句负责收束，把前面的信息压回到主结论上，避免答案突然停止。"
        elif task_type == "academic_discussion":
            why = "这句在回应同学观点和补充个人理由之间切换，既贴题又体现讨论型写作的互动性。"
        elif task_type == "email":
            why = "这句把背景、请求或可执行方案说清楚，能让邮件显得专业且容易被处理。"
        elif task_type == "integrated":
            why = "这句承担 reading 与 lecture 的点对点对应，是 integrated 高分最关键的组织动作。"
        elif task_type == "independent":
            why = "这句不是空泛表态，而是在推进理由或例子，所以能把论证写实。"
        else:
            why = "这句在保证语法自然的同时维持了清楚的信息重心，符合高分答案的稳定表达。"

        explanations.append({
            "sentence": sentence,
            "why_high_score": f"{why} 这里采用{style_cn}写法，所以语气更集中。",
        })

    return explanations


def _detect_argument_moves(text: str, task_type: str) -> tuple[list[str], list[str]]:
    lowered = " ".join((text or "").lower().split())
    present = []
    missing = []

    for item in _argument_move_specs(task_type):
        label = item.get("label") or "move"
        signals = [signal.lower() for signal in item.get("signals") or [] if isinstance(signal, str) and signal.strip()]
        if signals and any(signal in lowered for signal in signals):
            present.append(label)
        else:
            missing.append(label)

    return present, missing


def _fallback_argument_diff(task_type: str, learner_essay: str, model_essay: str) -> dict:
    move_specs = _argument_move_specs(task_type)
    default_model_moves = [item.get("label") or "move" for item in move_specs[:5]]
    model_present, _ = _detect_argument_moves(model_essay, task_type)
    learner_present, learner_missing = _detect_argument_moves(learner_essay, task_type)

    model_moves = model_present or default_model_moves
    missing_from_learner = [item for item in model_moves if item not in learner_present]
    if not missing_from_learner:
        missing_from_learner = learner_missing[:3]

    upgrades = []
    for item in move_specs:
        label = item.get("label") or "move"
        if label in missing_from_learner:
            upgrades.append(f"补上“{label}”：{item.get('description') or '让这一动作更明确。'}")
    if learner_essay.strip() and _sentence_count(learner_essay) < max(2, _sentence_count(model_essay) - 1):
        upgrades.append("把核心理由再展开一层，不要只有结论，至少补一个解释句或例子句。")
    if not learner_essay.strip():
        upgrades = [
            "先按“开头回应任务 - 中间推进理由 - 结尾收束”搭好骨架，再填内容。",
            "写完后对照“缺少的论证动作”，逐项检查自己有没有真的做出来。",
        ]

    return {
        "argument_moves_in_model": model_moves,
        "argument_moves_present_in_learner": learner_present,
        "argument_moves_missing_from_learner": missing_from_learner,
        "upgrade_from_learner": upgrades[:4],
    }


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

    compact = text.lower().replace("-", "_").replace(" ", "")
    if compact:
        if not task:
            task_aliases = sorted(TASK_ALIASES.items(), key=lambda item: len(item[0]), reverse=True)
            for alias, canonical in task_aliases:
                alias_compact = alias.replace("_", "")
                if alias_compact and alias_compact in compact:
                    task = canonical
                    break
        if not style:
            style_aliases = sorted(STYLE_ALIASES.items(), key=lambda item: len(item[0]), reverse=True)
            for alias, canonical in style_aliases:
                alias_compact = alias.replace("_", "")
                if alias_compact and alias_compact in compact:
                    style = canonical
                    break
        if not mode:
            mode_aliases = sorted(SAMPLE_MODE_ALIASES.items(), key=lambda item: len(item[0]), reverse=True)
            for alias, canonical in mode_aliases:
                alias_compact = alias.replace("_", "")
                if alias_compact and alias_compact in compact:
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
    return None, text


def _normalize_task_type(task_type: str | None) -> str | None:
    if not task_type:
        return None
    key = task_type.strip().lower().replace("-", "_").replace(" ", "_")
    if key in TASK_SPECS:
        return key
    return TASK_ALIASES.get(key)


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)?", text or ""))


def _sentence_count(text: str) -> int:
    parts = re.split(r"[.!?]+", text or "")
    return len([x for x in parts if x.strip()])


def _clip(text: str, limit: int = 4000) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + " ... [truncated]"


def _strip_model_artifacts(text: str) -> str:
    cleaned = text or ""
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"```[\s\S]*?```", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _round_half(value: float) -> float:
    return round(value * 2) / 2.0


def list_task_types() -> str:
    lines = [
        "🧭 TOEFL 写作题型（可自主选择）",
        "",
        "1) academic_discussion - Write for an Academic Discussion (2026)",
        "2) email - Write an Email (2026)",
        "3) build_sentence - Build a Sentence (2026)",
        "4) integrated - Integrated Essay (legacy, 备考常用)",
        "5) independent - Independent Essay (legacy, 备考常用)",
        "",
        "用法:",
        "- !eng essay prompt <题型> [生物|人文|科技|教育...]  生成该题型题目，可附加题材提示",
        "- !eng essay submit <你的英文答案>  按最近题目评分",
        "- !eng essay sample [题型] [保守版|平衡版|激进版] [标准模式|词库强化]  生成带讲解和diff的范文",
        "- !eng essay <题型> <你的英文答案>  直接按指定题型评分",
    ]
    return "\n".join(lines)


def _render_prompt(task_type: str, item: dict) -> str:
    spec = TASK_SPECS[task_type]

    if task_type == "academic_discussion":
        return "\n".join([
            f"Task Type: {spec['label']}",
            f"Time: {spec['time_limit']} | Recommended Length: {spec['target_words']} words",
            "",
            item["professor"],
            item["student_a"],
            item["student_b"],
            "",
            f"Instruction: {item['task']}",
        ])

    if task_type == "email":
        goals = "\n".join([f"- {g}" for g in item.get("goals", [])])
        return "\n".join([
            f"Task Type: {spec['label']}",
            f"Time: {spec['time_limit']} | Recommended Length: {spec['target_words']} words",
            "",
            f"Situation: {item['context']}",
            f"Task: {item['recipient']}",
            "Your email should:",
            goals,
        ])

    if task_type == "build_sentence":
        prompt_text, _ = _render_build_sentence_prompt(item)
        return prompt_text

    if task_type == "integrated":
        reading = "\n".join([f"- {x}" for x in item.get("reading_points", [])])
        lecture = "\n".join([f"- {x}" for x in item.get("lecture_points", [])])
        return "\n".join([
            f"Task Type: {spec['label']}",
            f"Time: {spec['time_limit']} | Recommended Length: {spec['target_words']} words",
            "",
            f"Reading Summary: {item['reading_topic']}",
            "Reading Main Points:",
            reading,
            "",
            "Lecture Main Points:",
            lecture,
            "",
            f"Instruction: {item['task']}",
        ])

    return "\n".join([
        f"Task Type: {spec['label']}",
        f"Time: {spec['time_limit']} | Recommended Length: {spec['target_words']} words",
        "",
        "Instruction:",
        item.get("prompt", "Write a well-organized essay and support your ideas with specific reasons and examples."),
        "Use your own words. Avoid memorized examples.",
    ])


def _build_dynamic_prompt_request(task_type: str, topic_hint: str | None, recent_prompts: list[str]) -> list[dict]:
    spec = TASK_SPECS[task_type]
    hint_display = _topic_hint_display(topic_hint) or (topic_hint or "").strip() or "无"
    recent_block = "\n".join(f"- {item}" for item in recent_prompts) if recent_prompts else "- None"
    task_rules = {
        "academic_discussion": "Create a professor post plus two clearly different student replies. The issue should be debatable and realistic.",
        "email": "Create a realistic campus or academic email situation with one recipient and three concrete goals.",
        "build_sentence": "Create a 5-fragment sentence-building task that leads to exactly one natural answer.",
        "integrated": "Create one reading-vs-lecture conflict with 3 reading points and 3 lecture counters.",
        "independent": "Create one sharp argumentative statement that invites reasons and examples.",
    }
    return [
        {
            "role": "system",
            "content": "You design elite TOEFL-style writing prompts. Return strict JSON only.",
        },
        {
            "role": "user",
            "content": f"""Task type: {task_type} ({spec['label']})
Topic hint: {hint_display}

Recent prompts that must not be repeated or lightly paraphrased:
{recent_block}

Return strict JSON only with keys:
{{
  "prompt_text": string,
  "prompt_id": string,
  "topic_tags": [string, string],
  "topic_label_cn": string
}}

Rules:
- prompt_text must be a complete TOEFL-style prompt ready to show to the learner.
- The topic must be materially different from every recent prompt above.
- If a topic hint is provided, make it central rather than incidental.
- Avoid generic recycled campus-policy scenarios unless the hint explicitly calls for them.
- {task_rules.get(task_type, 'Create a fresh TOEFL-style prompt.')}
- prompt_id must be lowercase, short, and underscore-separated.
- topic_tags should contain 1-3 concise English tags.
- topic_label_cn should be a short Chinese label such as 科技, 人文, 生物, 校园政策.
""",
        },
    ]


def _generate_prompt_with_llm(task_type: str, topic_hint: str | None, state: dict) -> tuple[str, str, list[str], str] | None:
    if not is_configured():
        return None

    result = chat_json(
        _build_dynamic_prompt_request(task_type, topic_hint, _recent_prompt_previews(state, task_type)),
        temperature=0.6,
        max_tokens=900,
        timeout=90,
        retries=1,
    )
    if not isinstance(result, dict):
        return None

    prompt_text = _strip_model_artifacts(result.get("prompt_text") or "")
    if not prompt_text:
        return None

    prompt_id = (_strip_model_artifacts(result.get("prompt_id") or "") or f"generated_{int(datetime.now().timestamp())}").strip().lower().replace("-", "_")
    topic_tags = []
    for item in result.get("topic_tags") or []:
        normalized = str(item).strip().lower()
        if normalized and normalized not in topic_tags:
            topic_tags.append(normalized)
    topic_label = _strip_model_artifacts(result.get("topic_label_cn") or "")
    return prompt_text, prompt_id, topic_tags[:3], topic_label


def _select_bank_prompt(task_type: str, state: dict, topic_hint: str | None) -> tuple[dict, str, list[str], str] | None:
    items = PROMPT_BANK.get(task_type) or []
    if not items:
        return None

    requested_topics = set(_normalize_topic_hints(topic_hint))
    candidates = []
    for index, item in enumerate(items):
        item_topics = set(_prompt_item_topics(task_type, index, item))
        if requested_topics and not (requested_topics & item_topics):
            continue
        candidates.append((index, item))
    if not candidates:
        return None

    used_prompt_ids = _used_prompt_ids(state, task_type)
    unused = []
    for index, item in candidates:
        prompt_id = _prompt_item_id(task_type, index, item)
        if prompt_id not in used_prompt_ids:
            unused.append((index, item))

    pool = unused or candidates
    index, item = random.choice(pool)
    return item, _prompt_item_id(task_type, index, item), _prompt_item_topics(task_type, index, item), _prompt_topic_label(task_type, index, item)


def generate_prompt(task_type: str | None = None, topic_hint: str | None = None) -> str:
    normalized = _normalize_task_type(task_type) or "academic_discussion"
    if normalized not in TASK_SPECS:
        return "⚠️ 题型不支持。发送 !eng essay types 查看全部题型。"

    state = _load_state()
    bank_choice = _select_bank_prompt(normalized, state, topic_hint)
    used_prompt_ids = _used_prompt_ids(state, normalized)
    should_generate_fresh = bank_choice is None or bank_choice[1] in used_prompt_ids
    if normalized == "build_sentence" and bank_choice:
        # Build a Sentence 需要可控打乱与唯一排序讲解，优先走本地结构化题库。
        should_generate_fresh = False

    build_sentence_payload = {}

    if should_generate_fresh:
        generated = _generate_prompt_with_llm(normalized, topic_hint, state)
        if generated:
            prompt_text, prompt_id, topic_tags, topic_label = generated
        elif bank_choice:
            item, prompt_id, topic_tags, topic_label = bank_choice
            if normalized == "build_sentence":
                prompt_text, build_sentence_payload = _render_build_sentence_prompt(item)
            else:
                prompt_text = _render_prompt(normalized, item)
        else:
            return "⚠️ 当前没有可用题目，请稍后再试。"
    else:
        item, prompt_id, topic_tags, topic_label = bank_choice
        if normalized == "build_sentence":
            prompt_text, build_sentence_payload = _render_build_sentence_prompt(item)
        else:
            prompt_text = _render_prompt(normalized, item)

    state.update({
        "task_type": normalized,
        "prompt_text": prompt_text,
        "generated_at": datetime.now().isoformat(),
        "last_prompt_id": prompt_id,
        "last_prompt_topic_tags": topic_tags,
        "last_prompt_topic_label": topic_label,
        "last_prompt_hint": (topic_hint or "").strip(),
    })
    if normalized == "build_sentence" and build_sentence_payload:
        state["build_sentence_solution"] = build_sentence_payload.get("answer_sentence") or ""
        state["build_sentence_ordered_fragments"] = build_sentence_payload.get("ordered_fragments") or []
        state["build_sentence_scrambled_fragments"] = build_sentence_payload.get("scrambled_fragments") or []
        state["build_sentence_rationale"] = build_sentence_payload.get("rationale") or []
    else:
        state.pop("build_sentence_solution", None)
        state.pop("build_sentence_ordered_fragments", None)
        state.pop("build_sentence_scrambled_fragments", None)
        state.pop("build_sentence_rationale", None)

    _remember_prompt_history(state, normalized, prompt_id, _clip(prompt_text, 260), topic_tags)
    _save_state(state)

    spec = TASK_SPECS[normalized]
    lines = [
        "🧪 TOEFL 写作题已生成（例卷风格）",
        f"题型: {normalized} ({spec['label']})",
    ]
    topic_display = _topic_hint_display(topic_hint)
    if topic_display:
        lines.append(f"题材提示: {topic_display}")
    elif topic_label:
        lines.append(f"题材: {topic_label}")
    lines.extend([
        "",
        prompt_text,
        "",
        "请直接回复:",
        "!eng essay submit <你的英文答案>",
    ])
    if normalized == "build_sentence":
        lines.append("看排序讲解可发送: !eng essay sample")
    else:
        lines.append("看范文可发送: !eng essay sample [保守版|平衡版|激进版] [标准模式|词库强化]")
    return "\n".join(lines)


def _extract_build_sentence_fragments(prompt_text: str) -> list[str]:
    lines = (prompt_text or "").splitlines()
    fragments = []
    in_fragment_block = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if in_fragment_block and fragments:
                break
            continue

        lowered = line.lower()
        if lowered.startswith("fragments"):
            in_fragment_block = True
            tail = line.split(":", 1)[1].strip() if ":" in line else ""
            if tail and "|" in tail:
                return _normalize_fragment_list([item.strip() for item in tail.split("|")])
            continue

        if in_fragment_block:
            if lowered.startswith("instruction:"):
                break
            numbered = re.match(r"^\d+\.\s*(.+)$", line)
            if numbered:
                fragments.append(numbered.group(1).strip())
                continue
            if "|" in line:
                fragments.extend(item.strip() for item in line.split("|"))
                continue
            fragments.append(line)

    if fragments:
        return _normalize_fragment_list(fragments)

    inline = re.search(r"Fragments\s*:\s*(.+)", prompt_text or "", flags=re.IGNORECASE)
    if inline:
        return _normalize_fragment_list([item.strip() for item in inline.group(1).split("|")])
    return []


def _build_sentence_fragment_signature(fragments: list[str]) -> tuple[str, ...]:
    return tuple(sorted(item.lower() for item in _normalize_fragment_list(fragments)))


def _match_build_sentence_bank_item(fragments: list[str]) -> dict | None:
    signature = _build_sentence_fragment_signature(fragments)
    if not signature:
        return None

    for item in PROMPT_BANK.get("build_sentence", []):
        ordered = _normalize_fragment_list(item.get("ordered_fragments") or item.get("fragments") or [])
        if _build_sentence_fragment_signature(ordered) == signature:
            return item
    return None


def _resolve_build_sentence_payload(state: dict, prompt_text: str) -> dict:
    stored_prompt = (state.get("prompt_text") or "").strip()
    if stored_prompt == (prompt_text or "").strip():
        ordered = _normalize_fragment_list(state.get("build_sentence_ordered_fragments") or [])
        scrambled = _normalize_fragment_list(state.get("build_sentence_scrambled_fragments") or [])
        answer = str(state.get("build_sentence_solution") or "").strip()
        rationale = [str(item).strip() for item in (state.get("build_sentence_rationale") or []) if str(item).strip()]
        if ordered and answer:
            return {
                "instruction": "Reorder the fragments into one natural and grammatically correct sentence.",
                "ordered_fragments": ordered,
                "scrambled_fragments": scrambled or ordered,
                "answer_sentence": answer,
                "rationale": rationale or _build_sentence_rationale(ordered),
            }

    displayed_fragments = _extract_build_sentence_fragments(prompt_text)
    matched = _match_build_sentence_bank_item(displayed_fragments)
    if matched:
        payload = _build_sentence_payload(matched)
        if displayed_fragments and _build_sentence_fragment_signature(displayed_fragments) == _build_sentence_fragment_signature(payload.get("ordered_fragments") or []):
            payload["scrambled_fragments"] = displayed_fragments
        return payload

    if displayed_fragments:
        return {
            "instruction": "Reorder the fragments into one natural and grammatically correct sentence.",
            "ordered_fragments": displayed_fragments,
            "scrambled_fragments": displayed_fragments,
            "answer_sentence": _compose_build_sentence_answer(displayed_fragments),
            "rationale": _build_sentence_rationale(displayed_fragments),
        }
    return {}


def _normalize_sentence_for_compare(text: str) -> str:
    tokens = re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)?", (text or "").lower())
    return " ".join(tokens)


def _build_sentence_sample_message(prompt_text: str, state: dict, learner_essay: str = "", sample_style: str | None = None, sample_mode: str | None = None) -> str:
    payload = _resolve_build_sentence_payload(state, prompt_text)
    if not payload:
        return "⚠️ 当前 Build a Sentence 题目信息不完整，请先发送 !eng essay prompt build_sentence 重新出题。"

    ordered = payload.get("ordered_fragments") or []
    scrambled = payload.get("scrambled_fragments") or []
    answer = payload.get("answer_sentence") or _compose_build_sentence_answer(ordered)
    rationale = payload.get("rationale") or _build_sentence_rationale(ordered)

    lines = [
        "📚 Build a Sentence 排序讲解",
        "题型: build_sentence (Build a Sentence)",
        "说明: 该题型是句子排序，不区分保守/平衡/激进，也不启用词库强化模式。",
    ]
    if _normalize_sample_style(sample_style) or _normalize_sample_mode(sample_mode):
        lines.append("提示: 你传入的风格/模式参数已忽略，系统已自动切换到排序讲解模式。")

    lines.extend([
        "",
        "当前题目:",
        prompt_text,
        "",
        "标准排序:",
    ])
    for idx, fragment in enumerate(ordered, 1):
        lines.append(f"  {idx}. {fragment}")

    lines.extend([
        "",
        "标准答案:",
        answer,
    ])

    if rationale:
        lines.append("")
        lines.append("为什么这样排:")
        for idx, note in enumerate(rationale, 1):
            lines.append(f"  {idx}. {note}")

    if learner_essay.strip():
        learner_norm = _normalize_sentence_for_compare(learner_essay)
        answer_norm = _normalize_sentence_for_compare(answer)
        learner_tokens = learner_norm.split()
        answer_tokens = answer_norm.split()

        lines.append("")
        lines.append("你最近一次提交诊断:")
        if learner_norm == answer_norm:
            lines.append("  ✅ 顺序和语法都正确，已经是标准答案结构。")
        elif sorted(learner_tokens) == sorted(answer_tokens) and learner_tokens != answer_tokens:
            lines.append("  ⚠️ 词块基本都用到了，但顺序还不自然。重点检查从句位置和主谓搭配顺序。")
        else:
            lines.append("  ⚠️ 当前句子与标准结构差异较大，建议先按“从句/主语/谓语/补充成分”四步重排。")

    if scrambled and ordered and scrambled == ordered:
        lines.append("")
        lines.append("⚠️ 检测到词块未打乱，建议重新出题: !eng essay prompt build_sentence")

    lines.extend([
        "",
        "答题步骤建议:",
        "  1. 先找 although/if 等从句引导词，确定从句边界。",
        "  2. 锁定主语与谓语核心，先搭出主干。",
        "  3. 再把宾语、目的状语、时间状语放到自然位置。",
    ])
    return "\n".join(lines)


def _build_model_essay_prompt(task_type: str, prompt_text: str, learner_essay: str = "", learner_feedback: dict | None = None, sample_style: str | None = None, sample_mode: str | None = None, local_words: list[dict] | None = None) -> tuple[str, str]:
    spec = TASK_SPECS[task_type]
    normalized_style, style_cn = _style_display_name(task_type, sample_style)
    normalized_mode = _normalize_sample_mode(sample_mode) or DEFAULT_SAMPLE_MODE
    mode_cn = _sample_mode_label(normalized_mode)
    profile_text = _format_sample_profile(task_type, normalized_style)
    vocab_policy = _style_vocab_policy(task_type, normalized_style)
    vocab_targets = _style_vocab_targets(task_type, normalized_style, normalized_mode)
    suggestion_words = local_words or _load_general_local_vocab_candidates(limit=max(6, vocab_targets["active_local_vocab_count"] * 2))
    weaknesses = learner_feedback.get("weaknesses") if isinstance(learner_feedback, dict) else []
    strengths = learner_feedback.get("strengths") if isinstance(learner_feedback, dict) else []

    feedback_lines = []
    if strengths:
        feedback_lines.append("Learner strengths:")
        feedback_lines.extend([f"- {item}" for item in strengths[:4]])
    if weaknesses:
        feedback_lines.append("Learner weaknesses:")
        feedback_lines.extend([f"- {item}" for item in weaknesses[:4]])

    learner_block = ""
    if learner_essay.strip():
        learner_block = (
            "Learner essay to improve upon stylistically (do not copy whole sentences; only preserve useful ideas):\n"
            f"{_clip(learner_essay, 2200)}\n\n"
        )

    feedback_block = "\n".join(feedback_lines).strip()
    if feedback_block:
        feedback_block += "\n\n"

    local_vocab_block = ""
    if local_words:
        local_vocab_block = _format_local_vocab_block(local_words, vocab_targets["active_local_vocab_count"]) + "\n\n"

    suggestion_vocab_block = ""
    if suggestion_words:
        suggestion_lines = ["LEARNER VOCABULARY THAT COULD BE SUGGESTED FOR THIS TOPIC (do not force all of them into the essay):"]
        for item in suggestion_words[:8]:
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
            suggestion_lines.append(line)
        suggestion_vocab_block = "\n".join(suggestion_lines) + "\n\n"

    system_prompt = """You are an elite TOEFL writing coach.

Return strict JSON only. No markdown fences, no commentary outside the JSON.
"""

    user_prompt = f"""Task type: {task_type} ({spec['label']})
Selected sample style: {normalized_style} ({style_cn})
Selected sample mode: {normalized_mode} ({mode_cn})

Recommended length: {spec['target_words']}
Task objective: {spec['description']}

Use the LOCAL sample bank below as your primary style reference. These are curated local exemplars inspired by public TOEFL sample patterns, not templates to copy verbatim.

LOCAL SAMPLE BANK:
{profile_text}

Prompt:
{_clip(prompt_text, 2600)}

{local_vocab_block}{suggestion_vocab_block}{learner_block}{feedback_block}Return strict JSON only with keys:
{{
    "style_used": string,
    "sample_mode": string,
    "model_essay": string,
    "vocabulary_control_summary": [string, string],
    "advanced_vocabulary_notes": [
        {{"expression": string, "level": string, "why_effective": string}}
    ],
    "local_vocab_summary": [string, string],
    "local_vocab_candidates": [
        {{"word": string, "meaning": string, "why_fit": string}}
    ],
    "learner_vocab_usage_notes": [
        {{"word": string, "meaning": string, "how_used": string}}
    ],
    "vocab_replacement_suggestions": [
        {{"generic_expression": string, "upgraded_expression": string, "reason": string}}
    ],
    "sentence_explanations": [
        {{"sentence": string, "why_high_score": string}}
    ],
    "why_it_works": [string, string, string],
    "argument_moves_in_model": [string, string, string],
    "argument_moves_present_in_learner": [string, string],
    "argument_moves_missing_from_learner": [string, string],
    "useful_phrases": [string, string, string, string],
    "upgrade_from_learner": [string, string]
}}

Rules:
- Write an original model answer for THIS prompt, not a generic template.
- Use natural, high-scoring TOEFL phrasing with noticeably stronger syntax and diction than a typical student answer.
- When the task allows multi-sentence writing, include at least 2 structurally advanced sentences with subordination, contrast, concession, or compression.
- Do not pad the essay with vague phrases. Every sentence should either push the argument, clarify a request, or sharpen source comparison.
- Lexical control for this style: {vocab_policy['target_density_en']}
- Lexical ceiling: {vocab_policy['ceiling_en']}
- Recommended advanced-expression count: about {vocab_targets['advanced_range']}.
- For academic_discussion, directly engage at least one peer view.
- For email, include greeting and closing.
- For integrated, stay source-based and do not insert personal opinion.
- For build_sentence, model_essay must be exactly one sentence using the target structure naturally.
- If learner essay exists, keep the best idea direction but upgrade diction, precision, and structure.
- why_it_works must contain at least 3 concrete Chinese points about structure, sentence quality, and task execution. Do not compare with the learner there.
- vocabulary_control_summary must be in Chinese and must explain how the selected style intentionally controls advanced-word density.
- advanced_vocabulary_notes must list 3-5 expressions actually used in model_essay, label their level in Chinese, and explain why they are advanced but still natural.
- If sample_mode is local_vocab and local vocabulary is provided, naturally use around {vocab_targets['active_local_vocab_count']} learner vocabulary items when they genuinely fit the task.
- local_vocab_summary must be in Chinese and explain whether this answer is standard mode or local-vocab-enhanced mode.
- local_vocab_candidates must recommend 2-4 learner-bank words that would fit this topic naturally even if they are not used in the essay.
- learner_vocab_usage_notes must explain how each used learner word is applied in context, in Chinese, and must reference only words actually used in model_essay.
- vocab_replacement_suggestions must provide 2-4 specific low-to-high upgrades in Chinese. If learner essay exists, prefer replacing weak wording from the learner. Otherwise provide task-relevant upgrade pairs.
- sentence_explanations must cover the main sentences in order, and each why_high_score must be written in Chinese.
- why_it_works should explain the structure briefly in Chinese.
- argument_moves_missing_from_learner should directly state which argument actions the learner omitted compared with the model answer.
- useful_phrases should be genuinely reusable high-level English chunks that appear in model_essay and are worth imitating. Exclude low-value functional phrases such as "would it be possible", "please let me know", "I would appreciate", or anything similarly generic.
- upgrade_from_learner must be in Chinese, must only compare learner vs model, and must not repeat vocabulary showcase or generic model praise.
- if no learner essay exists, return two generic suggestions.
"""
    return system_prompt, user_prompt


def _fallback_model_message(task_type: str, prompt_text: str, learner_essay: str = "", sample_style: str | None = None, sample_mode: str | None = None, local_words: list[dict] | None = None) -> str:
    profile = _sample_profile(task_type)
    normalized_style, style_cn = _style_display_name(task_type, sample_style)
    mode_cn = _sample_mode_label(sample_mode)
    style_profile = _sample_style_profile(task_type, normalized_style)
    exemplars = profile.get("exemplars") or []
    chosen = (style_profile.get("sample") or "").strip()
    if not chosen and exemplars:
        chosen = exemplars[0].get("essay", "")
    phrases = (style_profile.get("lexical_targets") or []) or (profile.get("lexical_targets") or [])
    diff = _fallback_argument_diff(task_type, learner_essay, chosen)
    vocab_summary = _style_vocab_summary(task_type, sample_style)
    vocab_notes = _fallback_vocabulary_notes(task_type, chosen, sample_style)
    local_vocab_summary = _local_vocab_mode_summary(task_type, sample_style, sample_mode, local_words)
    local_vocab_notes = _fallback_local_vocab_usage_notes(chosen, local_words or [])

    lines = [
        "📚 TOEFL 范文（本地范文库回退）",
        f"题型: {task_type}",
        f"风格: {style_cn}",
        f"模式: {mode_cn}",
        "",
        "当前题目:",
        prompt_text,
        "",
        "参考范文:",
        chosen or "当前没有可用范文，请先检查本地范文库。",
    ]
    if phrases:
        lines.append("")
        lines.append("可借鉴表达:")
        for idx, item in enumerate(phrases[:4], 1):
            lines.append(f"  {idx}. {item}")
    if vocab_summary:
        lines.append("")
        lines.append("词汇控制:")
        for idx, item in enumerate(vocab_summary[:2], 1):
            lines.append(f"  {idx}. {item}")
    if vocab_notes:
        lines.append("")
        lines.append("高分词汇亮点:")
        for idx, item in enumerate(vocab_notes[:4], 1):
            lines.append(f"  {idx}. {item['expression']} ({item['level']})")
            lines.append(f"     - {item['why_effective']}")
    if local_vocab_summary:
        lines.append("")
        lines.append("本地词库模式:")
        for idx, item in enumerate(local_vocab_summary[:2], 1):
            lines.append(f"  {idx}. {item}")
    if local_vocab_notes:
        lines.append("")
        lines.append("本地词库词汇是怎么用的:")
        for idx, item in enumerate(local_vocab_notes[:4], 1):
            lines.append(f"  {idx}. {item['word']}")
            if item.get("meaning"):
                lines.append(f"     - 释义: {item['meaning']}")
            if item.get("how_used"):
                lines.append(f"     - 用法: {item['how_used']}")
    if diff.get("argument_moves_missing_from_learner") and learner_essay.strip():
        lines.append("")
        lines.append("你当前缺少的论证动作:")
        for idx, item in enumerate(diff["argument_moves_missing_from_learner"][:4], 1):
            lines.append(f"  {idx}. {item}")
    if learner_essay.strip():
        lines.append("")
        lines.append("说明: 当前未调用模型生成定制范文，上面展示的是同题型本地参考风格。")
    return "\n".join(lines)


def _generate_model_essay_plaintext(task_type: str, prompt_text: str, learner_essay: str = "", learner_feedback: dict | None = None, sample_style: str | None = None, sample_mode: str | None = None, local_words: list[dict] | None = None) -> str | None:
    spec = TASK_SPECS[task_type]
    normalized_style, style_cn = _style_display_name(task_type, sample_style)
    normalized_mode = _normalize_sample_mode(sample_mode) or DEFAULT_SAMPLE_MODE
    mode_cn = _sample_mode_label(normalized_mode)
    profile_text = _format_sample_profile(task_type, normalized_style)
    vocab_targets = _style_vocab_targets(task_type, normalized_style, normalized_mode)

    feedback_lines = []
    if isinstance(learner_feedback, dict):
        for key in ("strengths", "weaknesses"):
            items = learner_feedback.get(key) or []
            if items:
                feedback_lines.append(f"{key}:")
                feedback_lines.extend([f"- {item}" for item in items[:4]])

    messages = [
        {
            "role": "system",
            "content": "You are an elite TOEFL writing coach. Write an original high-scoring model response for the exact prompt. Return only the model response text, with no explanation or markdown.",
        },
        {
            "role": "user",
            "content": (
                f"Task type: {task_type} ({spec['label']})\n"
                f"Selected style: {normalized_style} ({style_cn})\n"
                f"Selected mode: {normalized_mode} ({mode_cn})\n"
                f"Recommended length: {spec['target_words']}\n"
                f"Task objective: {spec['description']}\n\n"
                f"Use this local style bank as reference:\n{profile_text}\n\n"
                f"Prompt:\n{_clip(prompt_text, 2600)}\n\n"
                + (_format_local_vocab_block(local_words or [], vocab_targets["active_local_vocab_count"]) + "\n\n" if local_words else "")
                + (f"Learner essay:\n{_clip(learner_essay, 1800)}\n\n" if learner_essay.strip() else "")
                + ("\n".join(feedback_lines) + "\n\n" if feedback_lines else "")
                + f"Write one polished model response for this exact prompt. For academic discussion, engage at least one peer directly. For email, include greeting and closing. For integrated, stay source-based and objective. For build_sentence, output exactly one sentence. The tone must match the selected style. If local learner vocabulary is provided and mode is local_vocab, naturally use around {vocab_targets['active_local_vocab_count']} of those words only when they truly fit."
            ),
        },
    ]
    raw = chat_completion(messages, temperature=0.35, max_tokens=1100, timeout=90)
    cleaned = _strip_model_artifacts(raw or "")
    return cleaned or None


def generate_model_essay(task_type: str | None = None, sample_style: str | None = None, sample_mode: str | None = None) -> str:
    state = _load_state()
    explicit_task = _normalize_task_type(task_type)
    normalized_style = _normalize_sample_style(sample_style) or _normalize_sample_style(state.get("last_sample_style")) or DEFAULT_SAMPLE_STYLE
    normalized_mode = _normalize_sample_mode(sample_mode) or _normalize_sample_mode(state.get("last_sample_mode")) or DEFAULT_SAMPLE_MODE
    normalized = explicit_task or _normalize_task_type(state.get("task_type"))
    if not normalized:
        return "⚠️ 还没有当前写作题目。先发送 !eng essay prompt <题型>。"

    effective_prompt = (state.get("prompt_text") or "").strip()
    if explicit_task and _normalize_task_type(state.get("task_type")) != explicit_task:
        effective_prompt = _render_prompt(explicit_task, random.choice(PROMPT_BANK[explicit_task]))
        normalized = explicit_task
    elif not effective_prompt:
        effective_prompt = _render_prompt(normalized, random.choice(PROMPT_BANK[normalized]))

    saved_essay_task = _normalize_task_type(state.get("last_essay_task_type"))
    saved_essay_prompt = (state.get("last_essay_prompt_text") or "").strip()
    same_submission = saved_essay_task == normalized and saved_essay_prompt == effective_prompt

    learner_essay = (state.get("last_essay_text") or "").strip() if same_submission else ""
    learner_feedback = state.get("last_feedback") if same_submission and isinstance(state.get("last_feedback"), dict) else {}

    if normalized == "build_sentence":
        return _build_sentence_sample_message(
            effective_prompt,
            state,
            learner_essay,
            sample_style=sample_style,
            sample_mode=sample_mode,
        )

    local_words = _load_local_vocab_candidates(normalized, normalized_style, normalized_mode)
    suggestion_words = local_words or _load_general_local_vocab_candidates(limit=max(8, _style_vocab_targets(normalized, normalized_style, normalized_mode)["active_local_vocab_count"] * 2))

    if not is_configured():
        return _fallback_model_message(normalized, effective_prompt, learner_essay, normalized_style, normalized_mode, local_words)

    system_prompt, user_prompt = _build_model_essay_prompt(normalized, effective_prompt, learner_essay, learner_feedback, normalized_style, normalized_mode, local_words)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    result = chat_json(messages, temperature=0.35, max_tokens=1600, timeout=90, retries=2)
    model_essay = ""
    why_it_works = []
    useful_phrases = []
    upgrade_from_learner = []
    vocabulary_control_summary = []
    advanced_vocabulary_notes = []
    local_vocab_summary = []
    local_vocab_candidates = []
    learner_vocab_usage_notes = []
    vocab_replacement_suggestions = []
    sentence_explanations = []
    argument_moves_in_model = []
    argument_moves_present_in_learner = []
    argument_moves_missing_from_learner = []
    style_used = normalized_style

    if result and (result.get("model_essay") or "").strip():
        model_essay = _strip_model_artifacts(result.get("model_essay") or "")
        style_used = _normalize_sample_style(result.get("style_used")) or normalized_style
        normalized_mode = _normalize_sample_mode(result.get("sample_mode")) or normalized_mode
        vocabulary_control_summary = result.get("vocabulary_control_summary") or []
        advanced_vocabulary_notes = result.get("advanced_vocabulary_notes") or []
        local_vocab_summary = result.get("local_vocab_summary") or []
        local_vocab_candidates = result.get("local_vocab_candidates") or []
        learner_vocab_usage_notes = result.get("learner_vocab_usage_notes") or []
        vocab_replacement_suggestions = result.get("vocab_replacement_suggestions") or []
        sentence_explanations = result.get("sentence_explanations") or []
        why_it_works = result.get("why_it_works") or []
        argument_moves_in_model = result.get("argument_moves_in_model") or []
        argument_moves_present_in_learner = result.get("argument_moves_present_in_learner") or []
        argument_moves_missing_from_learner = result.get("argument_moves_missing_from_learner") or []
        useful_phrases = result.get("useful_phrases") or []
        upgrade_from_learner = result.get("upgrade_from_learner") or []
    else:
        model_essay = _generate_model_essay_plaintext(normalized, effective_prompt, learner_essay, learner_feedback, normalized_style, normalized_mode, local_words) or ""
        profile = _sample_profile(normalized)
        why_it_works = [
            f"这篇范文遵循了 {normalized} 题型的标准组织方式。",
            "开头先明确任务目标，再用更集中的支持信息推进论证。",
            "措辞会比普通答案更紧凑、更学术，也更贴近高分答卷。",
        ]
        vocabulary_control_summary = _style_vocab_summary(normalized, normalized_style)
        advanced_vocabulary_notes = _fallback_vocabulary_notes(normalized, model_essay, normalized_style)
        local_vocab_summary = _local_vocab_mode_summary(normalized, normalized_style, normalized_mode, local_words)
        local_vocab_candidates = _fallback_local_vocab_candidates(effective_prompt, suggestion_words)
        learner_vocab_usage_notes = _fallback_local_vocab_usage_notes(model_essay, local_words)
        vocab_replacement_suggestions = _fallback_vocab_replacement_suggestions(learner_essay, local_vocab_candidates)
        useful_phrases = (_sample_style_profile(normalized, normalized_style).get("lexical_targets") or []) or (profile.get("lexical_targets") or [])

    if not model_essay:
        return _fallback_model_message(normalized, effective_prompt, learner_essay, normalized_style, normalized_mode, local_words)

    sentence_explanations = [item for item in sentence_explanations if isinstance(item, dict)]
    for item in sentence_explanations:
        item["sentence"] = _strip_model_artifacts(item.get("sentence") or "")
        item["why_high_score"] = _strip_model_artifacts(item.get("why_high_score") or "")
    vocabulary_control_summary = [_strip_model_artifacts(str(item)) for item in vocabulary_control_summary if str(item).strip()]
    advanced_vocabulary_notes = [item for item in advanced_vocabulary_notes if isinstance(item, dict)]
    for item in advanced_vocabulary_notes:
        item["expression"] = _strip_model_artifacts(item.get("expression") or "")
        item["level"] = _strip_model_artifacts(item.get("level") or "")
        item["why_effective"] = _strip_model_artifacts(item.get("why_effective") or "")
    local_vocab_summary = [_strip_model_artifacts(str(item)) for item in local_vocab_summary if str(item).strip()]
    local_vocab_candidates = [item for item in local_vocab_candidates if isinstance(item, dict)]
    for item in local_vocab_candidates:
        item["word"] = _strip_model_artifacts(item.get("word") or "")
        item["meaning"] = _strip_model_artifacts(item.get("meaning") or "")
        item["why_fit"] = _strip_model_artifacts(item.get("why_fit") or "")
    learner_vocab_usage_notes = [item for item in learner_vocab_usage_notes if isinstance(item, dict)]
    for item in learner_vocab_usage_notes:
        item["word"] = _strip_model_artifacts(item.get("word") or "")
        item["meaning"] = _strip_model_artifacts(item.get("meaning") or "")
        item["how_used"] = _strip_model_artifacts(item.get("how_used") or "")
    vocab_replacement_suggestions = [item for item in vocab_replacement_suggestions if isinstance(item, dict)]
    for item in vocab_replacement_suggestions:
        item["generic_expression"] = _strip_model_artifacts(item.get("generic_expression") or "")
        item["upgraded_expression"] = _strip_model_artifacts(item.get("upgraded_expression") or "")
        item["reason"] = _strip_model_artifacts(item.get("reason") or "")

    fallback_diff = _fallback_argument_diff(normalized, learner_essay, model_essay)
    fallback_high_score_keys = [
        f"开头第一时间完成了 {normalized} 题型的核心任务，没有先写空泛铺垫。",
        "中段不是机械堆理由，而是通过让步、转折或因果把论证推进得更紧。",
        "句子更像高分答案：信息密度更高，连接更自然，收尾也更有落点。",
    ]
    if not vocabulary_control_summary:
        vocabulary_control_summary = _style_vocab_summary(normalized, style_used)
    if not advanced_vocabulary_notes:
        advanced_vocabulary_notes = _fallback_vocabulary_notes(normalized, model_essay, style_used)
    if not local_vocab_summary:
        local_vocab_summary = _local_vocab_mode_summary(normalized, style_used, normalized_mode, local_words)
    if not local_vocab_candidates:
        local_vocab_candidates = _fallback_local_vocab_candidates(effective_prompt, suggestion_words)
    if not learner_vocab_usage_notes:
        learner_vocab_usage_notes = _fallback_local_vocab_usage_notes(model_essay, local_words)
    if not vocab_replacement_suggestions:
        vocab_replacement_suggestions = _fallback_vocab_replacement_suggestions(learner_essay, local_vocab_candidates)
    if not sentence_explanations:
        sentence_explanations = _fallback_sentence_explanations(normalized, model_essay, style_used)
    if not argument_moves_in_model:
        argument_moves_in_model = fallback_diff.get("argument_moves_in_model") or []
    if not argument_moves_present_in_learner:
        argument_moves_present_in_learner = fallback_diff.get("argument_moves_present_in_learner") or []
    if not argument_moves_missing_from_learner:
        argument_moves_missing_from_learner = fallback_diff.get("argument_moves_missing_from_learner") or []
    if not upgrade_from_learner:
        upgrade_from_learner = fallback_diff.get("upgrade_from_learner") or []
    why_it_works = [_strip_model_artifacts(str(item)) for item in why_it_works if str(item).strip()]
    if len(why_it_works) < 3:
        for item in fallback_high_score_keys:
            if item not in why_it_works:
                why_it_works.append(item)
            if len(why_it_works) >= 4:
                break
    useful_phrases = _curate_useful_phrases(normalized, style_used, model_essay, useful_phrases, advanced_vocabulary_notes)

    style_used, style_cn = _style_display_name(normalized, style_used)
    mode_cn = _sample_mode_label(normalized_mode)

    state.update({
        "task_type": normalized,
        "prompt_text": effective_prompt,
        "last_sample_style": style_used,
        "last_sample_mode": normalized_mode,
        "last_model_essay": model_essay,
        "last_model_generated_at": datetime.now().isoformat(),
    })
    _save_state(state)

    lines = [
        "📚 TOEFL 范文（增强版）",
        f"题型: {normalized} ({TASK_SPECS[normalized]['label']})",
        f"风格: {style_cn}",
        f"模式: {mode_cn}",
        "",
        "当前题目:",
        effective_prompt,
        "",
        "参考范文:",
        model_essay,
    ]

    if why_it_works:
        lines.append("")
        lines.append("高分关键:")
        for idx, item in enumerate(why_it_works[:4], 1):
            lines.append(f"  {idx}. {item}")

    if vocabulary_control_summary:
        lines.append("")
        lines.append("词汇控制:")
        for idx, item in enumerate(vocabulary_control_summary[:3], 1):
            lines.append(f"  {idx}. {item}")

    if advanced_vocabulary_notes:
        lines.append("")
        lines.append("高分词汇亮点:")
        for idx, item in enumerate(advanced_vocabulary_notes[:4], 1):
            expression = (item.get("expression") or "").strip() if isinstance(item, dict) else ""
            level = (item.get("level") or "").strip() if isinstance(item, dict) else ""
            why = (item.get("why_effective") or "").strip() if isinstance(item, dict) else ""
            if expression:
                title = f"{expression} ({level})" if level else expression
                lines.append(f"  {idx}. {title}")
                if why:
                    lines.append(f"     - {why}")

    if local_vocab_summary:
        lines.append("")
        lines.append("本地词库模式:")
        for idx, item in enumerate(local_vocab_summary[:3], 1):
            lines.append(f"  {idx}. {item}")

    if local_vocab_candidates:
        lines.append("")
        lines.append("本地能用词汇:")
        for idx, item in enumerate(local_vocab_candidates[:4], 1):
            word = (item.get("word") or "").strip() if isinstance(item, dict) else ""
            meaning = (item.get("meaning") or "").strip() if isinstance(item, dict) else ""
            why_fit = (item.get("why_fit") or "").strip() if isinstance(item, dict) else ""
            if word:
                lines.append(f"  {idx}. {word}")
                if meaning:
                    lines.append(f"     - 释义: {meaning}")
                if why_fit:
                    lines.append(f"     - 适配原因: {why_fit}")

    if learner_vocab_usage_notes:
        lines.append("")
        lines.append("本地词库词汇是怎么用的:")
        for idx, item in enumerate(learner_vocab_usage_notes[:4], 1):
            word = (item.get("word") or "").strip() if isinstance(item, dict) else ""
            meaning = (item.get("meaning") or "").strip() if isinstance(item, dict) else ""
            how_used = (item.get("how_used") or "").strip() if isinstance(item, dict) else ""
            if word:
                lines.append(f"  {idx}. {word}")
                if meaning:
                    lines.append(f"     - 释义: {meaning}")
                if how_used:
                    lines.append(f"     - 用法: {how_used}")

    if vocab_replacement_suggestions:
        lines.append("")
        lines.append("词库替换建议:")
        for idx, item in enumerate(vocab_replacement_suggestions[:4], 1):
            generic_expression = (item.get("generic_expression") or "").strip() if isinstance(item, dict) else ""
            upgraded_expression = (item.get("upgraded_expression") or "").strip() if isinstance(item, dict) else ""
            reason = (item.get("reason") or "").strip() if isinstance(item, dict) else ""
            if generic_expression or upgraded_expression:
                lines.append(f"  {idx}. {generic_expression} -> {upgraded_expression}")
                if reason:
                    lines.append(f"     - {reason}")

    if sentence_explanations:
        lines.append("")
        lines.append("逐句高分讲解:")
        for idx, item in enumerate(sentence_explanations[:8], 1):
            sentence = (item.get("sentence") or "").strip() if isinstance(item, dict) else ""
            why = (item.get("why_high_score") or "").strip() if isinstance(item, dict) else ""
            if sentence:
                lines.append(f"  {idx}. {sentence}")
                if why:
                    lines.append(f"     - {why}")

    if argument_moves_in_model:
        lines.append("")
        lines.append("范文里的论证动作:")
        for idx, item in enumerate(argument_moves_in_model[:6], 1):
            lines.append(f"  {idx}. {item}")

    if learner_essay.strip() and argument_moves_missing_from_learner:
        lines.append("")
        lines.append("你当前缺少的论证动作:")
        for idx, item in enumerate(argument_moves_missing_from_learner[:6], 1):
            lines.append(f"  {idx}. {item}")

    if learner_essay.strip() and argument_moves_present_in_learner:
        lines.append("")
        lines.append("你已经做出来的动作:")
        for idx, item in enumerate(argument_moves_present_in_learner[:4], 1):
            lines.append(f"  {idx}. {item}")

    if useful_phrases:
        lines.append("")
        lines.append("可直接借鉴的高级表达:")
        for idx, item in enumerate(useful_phrases[:6], 1):
            lines.append(f"  {idx}. {item}")

    if upgrade_from_learner and learner_essay:
        lines.append("")
        lines.append("相对你刚才答案的升级点:")
        for idx, item in enumerate(upgrade_from_learner[:4], 1):
            lines.append(f"  {idx}. {item}")

    return "\n".join(lines)


def _normalize_subscore(raw_value) -> float:
    try:
        value = float(raw_value)
    except Exception:
        return 0.0
    if value > 5.0 and value <= 6.0:
        value = value * 5.0 / 6.0
    return _clamp(value, 0.0, 5.0)


def _build_eval_prompt(task_type: str) -> str:
    spec = TASK_SPECS[task_type]
    return f"""You are a strict TOEFL writing rater.

Target task type: {task_type} ({spec['label']})
Task objective: {spec['description']}

Use two channels:
1) holistic_score_0_5 (overall judgement)
2) analytic subscores_0_5 for task_response/coherence/language_use/grammar

{RUBRIC_ANCHORS}

Return strict JSON with EXACT keys:
{{
  "holistic_score_0_5": number,
  "subscores_0_5": {{
    "task_response": number,
    "coherence": number,
    "language_use": number,
    "grammar": number
  }},
  "off_topic": boolean,
  "score_rationale": string,
  "strengths": [string, string],
  "weaknesses": [string, string],
  "sentence_fixes": [
    {{"original": string, "improved": string, "reason": string}},
    {{"original": string, "improved": string, "reason": string}},
    {{"original": string, "improved": string, "reason": string}}
  ],
  "rewrite_sample_paragraph": string,
  "next_drill": string
}}

Rules:
- sentence_fixes must contain 3 high-impact fixes.
- rewrite_sample_paragraph should be one polished paragraph (<=100 words).
- next_drill should be specific and actionable (<=30 words).
- Be conservative: avoid inflated scoring.
"""


def _merge_ratings(primary: dict, secondary: dict | None) -> dict:
    if not secondary:
        return primary

    p_sub = primary.get("subscores_0_5") or primary.get("subscores") or {}
    s_sub = secondary.get("subscores_0_5") or secondary.get("subscores") or {}

    merged_sub = {
        "task_response": round((_normalize_subscore(p_sub.get("task_response")) + _normalize_subscore(s_sub.get("task_response"))) / 2.0, 2),
        "coherence": round((_normalize_subscore(p_sub.get("coherence")) + _normalize_subscore(s_sub.get("coherence"))) / 2.0, 2),
        "language_use": round((_normalize_subscore(p_sub.get("language_use")) + _normalize_subscore(s_sub.get("language_use"))) / 2.0, 2),
        "grammar": round((_normalize_subscore(p_sub.get("grammar")) + _normalize_subscore(s_sub.get("grammar"))) / 2.0, 2),
    }

    p_h = _normalize_subscore(primary.get("holistic_score_0_5"))
    s_h = _normalize_subscore(secondary.get("holistic_score_0_5"))
    merged_h = round((p_h + s_h) / 2.0, 2)

    merged = dict(primary)
    merged["subscores_0_5"] = merged_sub
    merged["holistic_score_0_5"] = merged_h
    merged["off_topic"] = bool(primary.get("off_topic")) and bool(secondary.get("off_topic"))
    merged["rater_gap"] = round(abs(p_h - s_h), 2)

    rationale_a = (primary.get("score_rationale") or "").strip()
    rationale_b = (secondary.get("score_rationale") or "").strip()
    if rationale_a and rationale_b:
        merged["score_rationale"] = f"Rater A: {rationale_a}\nRater B: {rationale_b}"

    return merged


def _evaluate_with_llm(task_type: str, prompt_text: str, essay_text: str) -> dict | None:
    wc = _word_count(essay_text)
    sc = _sentence_count(essay_text)
    messages = [
        {"role": "system", "content": _build_eval_prompt(task_type)},
        {
            "role": "user",
            "content": (
                f"Task Type: {task_type}\n"
                f"Prompt:\n{_clip(prompt_text, 2600)}\n\n"
                f"Essay (word_count={wc}, sentence_count={sc}):\n{_clip(essay_text, 4000)}"
            ),
        },
    ]
    first = chat_json(messages, temperature=0.25, max_tokens=1800, timeout=90, retries=2)
    if not first:
        return None

    second_messages = list(messages) + [
        {
            "role": "system",
            "content": "Act as an independent second rater. Re-score from scratch and keep strict standards.",
        }
    ]
    second = chat_json(second_messages, temperature=0.1, max_tokens=1500, timeout=90, retries=1)
    return _merge_ratings(first, second)


def _target_lower_bound(task_type: str) -> int:
    target = TASK_SPECS[task_type]["target_words"]
    nums = re.findall(r"\d+", target)
    if nums:
        return int(nums[0])
    return TASK_SPECS[task_type]["min_words"]


def _length_penalty(task_type: str, word_count: int) -> float:
    hard_min = TASK_SPECS[task_type]["min_words"]
    soft_min = max(hard_min, _target_lower_bound(task_type))

    if word_count >= soft_min:
        return 0.0

    # 低于建议下限但高于硬阈值，给轻惩罚。
    if word_count >= hard_min:
        gap_ratio = (soft_min - word_count) / max(1, soft_min)
        return round(min(1.8, gap_ratio * 2.0), 2)

    gap_ratio = (hard_min - word_count) / max(1, hard_min)
    return round(min(6.0, 1.0 + gap_ratio * 6.0), 2)


def _style_penalty(essay_text: str) -> float:
    wc = _word_count(essay_text)
    sc = _sentence_count(essay_text)
    if wc == 0:
        return 6.0

    tokens = re.findall(r"[A-Za-z]+", essay_text.lower())
    diversity = len(set(tokens)) / max(1, len(tokens))
    avg_sentence_len = wc / max(1, sc)

    penalty = 0.0
    if sc <= 1 and wc >= 60:
        penalty += 1.2
    if avg_sentence_len > 42 or avg_sentence_len < 5:
        penalty += 0.8
    if diversity < 0.22 and wc >= 100:
        penalty += 0.8
    return round(min(3.0, penalty), 2)


def _prepare_scores(task_type: str, essay_text: str, raw_result: dict) -> dict:
    sub_raw = raw_result.get("subscores_0_5") or raw_result.get("subscores") or {}
    subscores = {
        "task_response": _normalize_subscore(sub_raw.get("task_response")),
        "coherence": _normalize_subscore(sub_raw.get("coherence")),
        "language_use": _normalize_subscore(sub_raw.get("language_use")),
        "grammar": _normalize_subscore(sub_raw.get("grammar")),
    }
    holistic = _normalize_subscore(raw_result.get("holistic_score_0_5"))
    if holistic == 0.0:
        holistic = round(mean(subscores.values()), 2)

    analytic_30 = mean(subscores.values()) * 6.0
    holistic_30 = holistic * 6.0
    blended_30 = analytic_30 * 0.65 + holistic_30 * 0.35

    wc = _word_count(essay_text)
    penalties = {
        "length": _length_penalty(task_type, wc),
        "style": _style_penalty(essay_text),
        "off_topic": 1.5 if bool(raw_result.get("off_topic")) else 0.0,
    }
    total_penalty = penalties["length"] + penalties["style"] + penalties["off_topic"]

    final_30 = _round_half(_clamp(blended_30 - total_penalty, 0.0, 30.0))
    return {
        "subscores_0_5": subscores,
        "holistic_0_5": round(holistic, 2),
        "analytic_30": round(analytic_30, 2),
        "blended_30": round(blended_30, 2),
        "final_30": final_30,
        "penalties": penalties,
    }


def _format_score_message(task_type: str, prompt_text: str, essay_text: str, model_result: dict, scores: dict, standard_mode_vocab_suggestions: list[dict] | None = None) -> str:
    spec = TASK_SPECS[task_type]
    sub = scores["subscores_0_5"]

    strengths = model_result.get("strengths") or []
    weaknesses = model_result.get("weaknesses") or []
    fixes = model_result.get("sentence_fixes") or []
    sample = (model_result.get("rewrite_sample_paragraph") or "").strip()
    drill = (model_result.get("next_drill") or "").strip()
    rationale = (model_result.get("score_rationale") or "").strip()

    wc = _word_count(essay_text)
    penalties = scores["penalties"]
    prompt_preview = _clip(prompt_text.replace("\n", " "), 160)

    lines = [
        "📝 TOEFL 写作评分（增强版）",
        f"题型: {task_type} ({spec['label']})",
        f"字数: {wc}（建议 {spec['target_words']}）",
        f"总分: {scores['final_30']}/30",
        f"Holistic: {scores['holistic_0_5']}/5",
        "分项(0-5):",
        f"  • Task Response: {sub['task_response']}",
        f"  • Coherence: {sub['coherence']}",
        f"  • Language Use: {sub['language_use']}",
        f"  • Grammar: {sub['grammar']}",
        "",
        "评分校准:",
        f"  • Analytic->30: {scores['analytic_30']}",
        f"  • Blended->30: {scores['blended_30']}",
        f"  • Length Penalty: -{penalties['length']}",
        f"  • Style Penalty: -{penalties['style']}",
        f"  • Off-topic Penalty: -{penalties['off_topic']}",
    ]

    if rationale:
        lines.append("")
        lines.append("评分依据:")
        lines.append(rationale)

    if strengths:
        lines.append("")
        lines.append("亮点:")
        for item in strengths[:3]:
            lines.append(f"  ✓ {item}")

    if weaknesses:
        lines.append("")
        lines.append("待改进:")
        for item in weaknesses[:3]:
            lines.append(f"  ✗ {item}")

    if fixes:
        lines.append("")
        lines.append("关键句改写(3条):")
        for idx, fix in enumerate(fixes[:3], 1):
            original = (fix.get("original") or "").strip()
            improved = (fix.get("improved") or "").strip()
            reason = (fix.get("reason") or "").strip()
            if not original or not improved:
                continue
            lines.append(f"{idx}. 原句: {original}")
            lines.append(f"   优化: {improved}")
            if reason:
                lines.append(f"   原因: {reason}")

    if sample:
        lines.append("")
        lines.append("示范段落改写:")
        lines.append(sample)

    if standard_mode_vocab_suggestions:
        lines.append("")
        lines.append("标准模式下可自然带入的本地词:")
        for idx, item in enumerate(standard_mode_vocab_suggestions[:3], 1):
            word = (item.get("word") or "").strip() if isinstance(item, dict) else ""
            meaning = (item.get("meaning") or "").strip() if isinstance(item, dict) else ""
            why_fit = (item.get("why_fit") or "").strip() if isinstance(item, dict) else ""
            how_to_use = (item.get("how_to_use") or "").strip() if isinstance(item, dict) else ""
            if not word:
                continue
            lines.append(f"  {idx}. {word}")
            if meaning:
                lines.append(f"     - 释义: {meaning}")
            if why_fit:
                lines.append(f"     - 为什么适合: {why_fit}")
            if how_to_use:
                lines.append(f"     - 怎么放进原文: {how_to_use}")

    if drill:
        lines.append("")
        lines.append(f"下一步练习: {drill}")

    lines.append("")
    lines.append(f"当前题目摘要: {prompt_preview}")
    return "\n".join(lines)


def _build_sentence_order_ratio(learner_norm: str, ordered_fragments: list[str]) -> float:
    normalized_fragments = [_normalize_sentence_for_compare(item) for item in ordered_fragments if _normalize_sentence_for_compare(item)]
    if len(normalized_fragments) <= 1:
        return 1.0 if normalized_fragments else 0.0

    positions = []
    for fragment in normalized_fragments:
        positions.append(learner_norm.find(fragment))

    total_pairs = 0
    correct_pairs = 0
    for i in range(len(positions)):
        if positions[i] < 0:
            continue
        for j in range(i + 1, len(positions)):
            if positions[j] < 0:
                continue
            total_pairs += 1
            if positions[i] <= positions[j]:
                correct_pairs += 1

    if total_pairs <= 0:
        return 0.0
    return round(correct_pairs / total_pairs, 2)


def _score_build_sentence_submission(essay_text: str, state: dict, prompt_text: str) -> str:
    payload = _resolve_build_sentence_payload(state, prompt_text)
    if not payload:
        return "⚠️ 当前 Build a Sentence 题目信息缺失，请先发送 !eng essay prompt build_sentence 重新出题。"

    ordered = _normalize_fragment_list(payload.get("ordered_fragments") or [])
    answer_sentence = payload.get("answer_sentence") or _compose_build_sentence_answer(ordered)
    rationale = payload.get("rationale") or _build_sentence_rationale(ordered)

    learner_norm = _normalize_sentence_for_compare(essay_text)
    answer_norm = _normalize_sentence_for_compare(answer_sentence)
    fragment_norms = [_normalize_sentence_for_compare(item) for item in ordered]

    hit_fragments = []
    missing_fragments = []
    for fragment, fragment_norm in zip(ordered, fragment_norms):
        if fragment_norm and fragment_norm in learner_norm:
            hit_fragments.append(fragment)
        else:
            missing_fragments.append(fragment)

    coverage_ratio = len(hit_fragments) / max(1, len(ordered))
    order_ratio = _build_sentence_order_ratio(learner_norm, ordered)

    punctuation_score = 5.0
    if essay_text and essay_text[0].islower():
        punctuation_score -= 0.5
    if essay_text.strip() and essay_text.strip()[-1] not in ".!?":
        punctuation_score -= 0.5
    punctuation_score = _clamp(punctuation_score, 3.5, 5.0)

    is_exact = bool(answer_norm and learner_norm and answer_norm == learner_norm)
    if is_exact:
        final_30 = 30.0
        coverage_ratio = 1.0
        order_ratio = 1.0
    else:
        raw_score = coverage_ratio * 10.0 + order_ratio * 15.0 + punctuation_score
        final_30 = _round_half(_clamp(raw_score, 0.0, 30.0))

    strengths = []
    weaknesses = []
    if coverage_ratio >= 0.99:
        strengths.append("词块覆盖完整，所有核心信息都用到了。")
    else:
        weaknesses.append("有词块遗漏或替换，先确保每个给定片段都出现。")
    if order_ratio >= 0.99:
        strengths.append("词块顺序正确，主从句衔接自然。")
    else:
        weaknesses.append("词块顺序还不稳定，重点检查从句位置与主句骨架。")
    if is_exact:
        strengths.append("句子语法与表达已达到标准答案水平。")

    feedback = {
        "strengths": strengths,
        "weaknesses": weaknesses,
        "score_rationale": f"coverage={round(coverage_ratio, 2)}, order={round(order_ratio, 2)}, exact_match={is_exact}",
        "scores": {
            "final_30": final_30,
            "coverage_ratio": round(coverage_ratio, 2),
            "order_ratio": round(order_ratio, 2),
        },
    }

    state.update({
        "task_type": "build_sentence",
        "prompt_text": prompt_text,
        "generated_at": datetime.now().isoformat(),
        "last_essay_text": essay_text,
        "last_essay_task_type": "build_sentence",
        "last_essay_prompt_text": prompt_text,
        "last_feedback": feedback,
    })
    _save_state(state)

    lines = [
        "📝 Build a Sentence 评分（本地判分）",
        "题型: build_sentence (Build a Sentence)",
        f"总分: {final_30}/30",
        f"词块覆盖: {len(hit_fragments)}/{len(ordered)}",
        f"顺序准确率: {int(round(order_ratio * 100))}%",
    ]

    if is_exact:
        lines.append("✅ 你的答案与标准排序一致，提交正确。")
    else:
        if missing_fragments:
            lines.append("⚠️ 缺失词块: " + " | ".join(missing_fragments))
        if coverage_ratio >= 0.99 and order_ratio < 0.99:
            lines.append("⚠️ 词块基本都对，但顺序需要调整。")

    lines.extend([
        "",
        "标准答案:",
        answer_sentence,
        "",
        "标准排序:",
    ])
    for idx, fragment in enumerate(ordered, 1):
        lines.append(f"  {idx}. {fragment}")

    if rationale:
        lines.append("")
        lines.append("为什么这样排:")
        for idx, item in enumerate(rationale[:5], 1):
            lines.append(f"  {idx}. {item}")

    lines.append("")
    lines.append("继续练习: !eng essay prompt build_sentence")
    return "\n".join(lines)


def score_essay(essay_text: str, task_type: str | None = None, prompt_text: str | None = None) -> str:
    essay_text = (essay_text or "").strip()
    if not essay_text:
        return "📝 请发送英文答案文本。"

    state = _load_state()
    explicit_task = _normalize_task_type(task_type)
    normalized = explicit_task or _normalize_task_type(state.get("task_type")) or "academic_discussion"
    if normalized not in TASK_SPECS:
        normalized = "academic_discussion"

    effective_prompt = ""
    if prompt_text:
        effective_prompt = prompt_text.strip()
    elif explicit_task:
        effective_prompt = _render_prompt(explicit_task, random.choice(PROMPT_BANK[explicit_task]))
    else:
        effective_prompt = (state.get("prompt_text") or "").strip()

    if not effective_prompt:
        effective_prompt = _render_prompt(normalized, random.choice(PROMPT_BANK[normalized]))

    minimum = TASK_SPECS[normalized]["min_words"]
    wc = _word_count(essay_text)
    if wc < 20 and normalized != "build_sentence":
        return f"📝 当前题型建议至少写到 {minimum} 词。你这次只有 {wc} 词，信息不足以稳定评分。"

    if normalized == "build_sentence":
        return _score_build_sentence_submission(essay_text, state, effective_prompt)

    if not is_configured():
        return "⚠️ 当前 LLM 未配置，无法评分。请检查 llm_config.json。"

    result = _evaluate_with_llm(normalized, effective_prompt, essay_text)
    if not result:
        return "⚠️ 写作评分暂时失败，请稍后再试。"

    local_vocab_candidates = _load_general_local_vocab_candidates(limit=8)
    standard_mode_vocab_suggestions = _suggest_standard_mode_local_vocab(
        normalized,
        effective_prompt,
        essay_text,
        local_vocab_candidates,
    )

    scores = _prepare_scores(normalized, essay_text, result)

    try:
        db = SessionLocal()
        try:
            sub = scores["subscores_0_5"]
            db.add(EssayScore(
                prompt_type=normalized,
                task_type=normalized,
                prompt_text=effective_prompt,
                essay_text=essay_text,
                overall_score=float(scores["final_30"]),
                holistic_score=float(scores["holistic_0_5"]),
                task_response=float(sub["task_response"]),
                coherence=float(sub["coherence"]),
                language_use=float(sub["language_use"]),
                grammar=float(sub["grammar"]),
                feedback_json=json.dumps({
                    "llm": result,
                    "calibrated": scores,
                }, ensure_ascii=False),
                created_at=datetime.now(),
            ))
            db.add(StudyEvent(
                event_type="essay_scored",
                payload_json=json.dumps({
                    "task_type": normalized,
                    "overall": scores["final_30"],
                    "holistic": scores["holistic_0_5"],
                    "word_count": wc,
                }, ensure_ascii=False),
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        pass

    state.update({
        "task_type": normalized,
        "prompt_text": effective_prompt,
        "generated_at": datetime.now().isoformat(),
        "last_essay_text": essay_text,
        "last_essay_task_type": normalized,
        "last_essay_prompt_text": effective_prompt,
        "last_feedback": {
            "strengths": result.get("strengths") or [],
            "weaknesses": result.get("weaknesses") or [],
            "score_rationale": result.get("score_rationale") or "",
            "scores": scores,
        },
    })
    _save_state(state)

    return _format_score_message(normalized, effective_prompt, essay_text, result, scores, standard_mode_vocab_suggestions)


def essay_help() -> str:
    return "\n".join([
        "📝 TOEFL 写作教练命令",
        "- !eng essay types",
        "- !eng essay prompt <题型> [生物|人文|科技|教育...]",
        "- !eng essay submit <英文答案>",
        "- !eng essay sample  为当前题目生成平衡版范文",
        "- !eng essay sample <题型> [保守版|平衡版|激进版] [标准模式|词库强化]  按指定题型生成讲解版范文",
        "- !eng essay sample <保守版|平衡版|激进版> [标准模式|词库强化]  切换当前题目的写法风格/模式",
        "- !eng essay <题型> <英文答案>",
        "- !eng essay <英文答案>  (默认按最近题目评分)",
    ])


def handle_essay_command(args: str) -> str:
    text = (args or "").strip()
    if not text:
        return essay_help()

    lowered = text.lower()
    if lowered in {"help", "?", "说明", "用法"}:
        return essay_help()
    if lowered in {"types", "type", "题型"}:
        return list_task_types()
    if lowered.startswith("sample") or lowered.startswith("model") or lowered in {"范文", "sample", "model"}:
        task = ""
        if lowered.startswith("sample"):
            task = text[6:].strip()
        elif lowered.startswith("model"):
            task = text[5:].strip()
        parsed_task, parsed_style, parsed_mode = _parse_sample_request(task)
        return generate_model_essay(parsed_task if parsed_task else None, parsed_style, parsed_mode)
    if lowered.startswith("prompt"):
        task = text[6:].strip()
        parsed_task, topic_hint = _parse_prompt_request(task)
        return generate_prompt(parsed_task if parsed_task else None, topic_hint)
    if lowered.startswith("submit"):
        content = text[6:].strip()
        if not content:
            return "⚠️ 用法: !eng essay submit <你的英文答案>"
        return score_essay(content)

    first, *rest = text.split(None, 1)
    normalized = _normalize_task_type(first)
    if normalized and not rest:
        return generate_prompt(normalized)
    if normalized and rest:
        remainder = rest[0].strip()
        if remainder.lower() in {"prompt", "new", "题目"}:
            return generate_prompt(normalized)
        if remainder.lower().startswith("prompt"):
            prompt_args = remainder[6:].strip()
            _, topic_hint = _parse_prompt_request(prompt_args)
            return generate_prompt(normalized, topic_hint)
        lowered_remainder = remainder.lower()
        if lowered_remainder in {"sample", "model", "范文"}:
            return generate_model_essay(normalized)
        if lowered_remainder.startswith("sample") or lowered_remainder.startswith("model"):
            inner = remainder.split(None, 1)
            sample_args = inner[1] if len(inner) > 1 else ""
            _, parsed_style, parsed_mode = _parse_sample_request(sample_args)
            return generate_model_essay(normalized, parsed_style, parsed_mode)
        return score_essay(remainder, task_type=normalized)

    return score_essay(text)
