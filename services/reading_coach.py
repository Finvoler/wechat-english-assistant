#!/usr/bin/env python3
"""TOEFL 阅读教练：文章与阅读题分离，支持整卷作答评分与按题号解析。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from database import SessionLocal
from models import Article, StudyEvent
from services.llm_client import chat_json, is_configured


project_root = Path(__file__).resolve().parent.parent
reading_state_file = project_root / "data" / "reading_state.json"


TOPIC_ALIASES = {
    "生物": "biology",
    "biology": "biology",
    "bio": "biology",
    "科技": "technology",
    "技术": "technology",
    "technology": "technology",
    "tech": "technology",
    "天文": "astronomy",
    "astronomy": "astronomy",
    "地质": "geology",
    "geology": "geology",
    "环境": "environment",
    "环保": "environment",
    "environment": "environment",
    "心理": "psychology",
    "psychology": "psychology",
    "考古": "archaeology",
    "archaeology": "archaeology",
    "人文": "humanities",
    "humanities": "humanities",
    "城市": "urban",
    "urban": "urban",
}

TOPIC_LABELS = {
    "biology": "生物",
    "technology": "科技",
    "astronomy": "天文",
    "geology": "地质",
    "environment": "环境",
    "psychology": "心理",
    "archaeology": "考古",
    "humanities": "人文",
    "urban": "城市",
}

QUESTION_TYPE_ORDER = [
    "factual_information",
    "negative_factual_information",
    "inference",
    "rhetorical_purpose",
    "vocabulary_in_context",
    "sentence_simplification",
    "insert_text",
    "summary",
]

QUESTION_TYPE_CN = {
    "factual_information": "事实信息题",
    "negative_factual_information": "否定事实题",
    "inference": "推断题",
    "rhetorical_purpose": "修辞目的题",
    "vocabulary_in_context": "词汇语境题",
    "sentence_simplification": "句子简化题",
    "insert_text": "句子插入题",
    "summary": "段落总结题",
}


def _clip(text: str, limit: int = 420) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1].rstrip() + "…"


def _ensure_state_dir() -> None:
    reading_state_file.parent.mkdir(parents=True, exist_ok=True)


def _load_state() -> dict:
    if not reading_state_file.exists():
        return {}
    try:
        with open(reading_state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    _ensure_state_dir()
    with open(reading_state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def normalize_topic_hint(raw: str | None) -> str | None:
    if not raw:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    return TOPIC_ALIASES.get(text)


def topic_hint_display(topic_hint: str | None) -> str:
    if not topic_hint:
        return ""
    return TOPIC_LABELS.get(topic_hint, topic_hint)


def _safe_json_loads(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _latest_article() -> Article | None:
    db = SessionLocal()
    try:
        return db.query(Article).order_by(Article.created_at.desc(), Article.id.desc()).first()
    finally:
        db.close()


def _article_by_id(article_id: int) -> Article | None:
    db = SessionLocal()
    try:
        return db.query(Article).filter(Article.id == int(article_id)).first()
    finally:
        db.close()


def _record_event(event_type: str, payload: dict) -> None:
    db = SessionLocal()
    try:
        db.add(StudyEvent(event_type=event_type, payload_json=json.dumps(payload, ensure_ascii=False)))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _build_type_list(count: int) -> list[str]:
    if count <= len(QUESTION_TYPE_ORDER):
        return QUESTION_TYPE_ORDER[:count]
    out = QUESTION_TYPE_ORDER[:]
    while len(out) < count:
        out.extend(QUESTION_TYPE_ORDER)
    return out[:count]


def _normalize_question_item(item: dict, index: int) -> dict | None:
    if not isinstance(item, dict):
        return None

    question = (item.get("question") or item.get("question_text") or "").strip()
    if not question:
        return None

    options = item.get("options") if isinstance(item.get("options"), dict) else {}
    normalized_options = {}
    for key in ("A", "B", "C", "D"):
        value = options.get(key) if isinstance(options, dict) else None
        text = str(value or "").strip()
        normalized_options[key] = text or f"Option {key}"

    answer = str(item.get("answer") or "").strip().upper()
    if answer not in {"A", "B", "C", "D"}:
        answer = "A"

    q_type = str(item.get("type") or "factual_information").strip().lower()
    if q_type not in QUESTION_TYPE_CN:
        q_type = "factual_information"

    # 本系统的答题流程是一次性 A-D 单选提交，避免出现多选/拖拽式措辞。
    if q_type == "summary" and re.search(r"select\s+the\s+three", question, flags=re.IGNORECASE):
        question = "Which option best summarizes the passage?"

    source_paragraph = item.get("source_paragraph")
    try:
        source_paragraph = max(1, int(source_paragraph))
    except Exception:
        source_paragraph = 1

    return {
        "id": index,
        "type": q_type,
        "question": question,
        "options": normalized_options,
        "answer": answer,
        "source_paragraph": source_paragraph,
        "source_quote": _clip(str(item.get("source_quote") or "").strip(), 180),
        "explanation": _clip(str(item.get("explanation") or "").strip(), 300),
    }


def _generate_questions_with_llm(article: Article, count: int) -> list[dict]:
    if not is_configured():
        return []

    passage = (article.content or "").strip()
    if not passage:
        return []

    q_types = _build_type_list(count)

    system_prompt = """You are a TOEFL iBT Reading (2026 style) question writer.
Return valid JSON only, no markdown, no commentary.
"""

    user_prompt = f"""
Generate {count} TOEFL Reading multiple-choice questions from the passage.

Required type order:
{json.dumps(q_types, ensure_ascii=False)}

Output JSON schema:
{{
  "questions": [
    {{
      "type": "factual_information|negative_factual_information|inference|rhetorical_purpose|vocabulary_in_context|sentence_simplification|insert_text|summary",
      "question": "string",
      "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
      "answer": "A|B|C|D",
      "source_paragraph": 1,
      "source_quote": "exact supporting text from passage, <= 140 chars",
      "explanation": "why this answer is best, and why others are weaker"
    }}
  ]
}}

Rules:
- All questions must be answerable from the passage only.
- Keep wording TOEFL-like and academic, not conversational.
- Ensure exactly one correct answer per question.
- source_paragraph must be 1-based paragraph index from the passage.
- source_quote must be copied from the passage, not invented.
- Language: questions/options/explanations should be English.

Passage title: {article.title}
Passage:
{_clip(passage, 7500)}
"""

    payload = chat_json(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.35,
        max_tokens=2600,
        timeout=90,
        retries=1,
    )
    if not payload:
        return []

    raw_questions = payload.get("questions") if isinstance(payload.get("questions"), list) else []
    normalized: list[dict] = []
    for idx, item in enumerate(raw_questions, 1):
        normalized_item = _normalize_question_item(item, idx)
        if normalized_item:
            normalized.append(normalized_item)

    return normalized[:count]


def _generate_questions_fallback(article: Article, count: int) -> list[dict]:
    paragraphs = [p.strip() for p in (article.content or "").split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = ["The passage discusses how researchers evaluate evidence and refine explanations over time."]

    q_types = _build_type_list(count)
    base_questions = []
    for idx, q_type in enumerate(q_types, 1):
        para_index = min(idx, len(paragraphs))
        para = paragraphs[para_index - 1]
        quote = _clip(para.split(". ")[0].strip() or para, 120)

        question = f"According to paragraph {para_index}, which statement best matches the author's point?"
        if q_type == "negative_factual_information":
            question = f"According to paragraph {para_index}, which of the following is NOT stated as supporting evidence?"
        elif q_type == "inference":
            question = f"What can be inferred from paragraph {para_index} about the author's view of evidence?"
        elif q_type == "rhetorical_purpose":
            question = f"Why does the author include the example in paragraph {para_index}?"
        elif q_type == "vocabulary_in_context":
            question = f"As used in paragraph {para_index}, the word closest in meaning to the highlighted idea is:"
        elif q_type == "sentence_simplification":
            question = f"Which option best expresses the essential information of a key sentence in paragraph {para_index}?"
        elif q_type == "insert_text":
            question = f"Where would the following sentence best fit in paragraph {para_index}?"
        elif q_type == "summary":
            question = "Which option best summarizes the overall argument of the passage?"

        options = {
            "A": "It emphasizes cumulative, evidence-based reasoning over quick conclusions.",
            "B": "It argues that a single experiment is usually enough to settle a debate.",
            "C": "It claims rhetorical style matters more than factual support.",
            "D": "It suggests disagreement makes academic inquiry impossible.",
        }
        answer = "A"
        if q_type == "negative_factual_information":
            answer = "B"
        elif q_type == "insert_text":
            answer = "A"

        base_questions.append({
            "id": idx,
            "type": q_type,
            "question": question,
            "options": options,
            "answer": answer,
            "source_paragraph": para_index,
            "source_quote": quote,
            "explanation": "The passage repeatedly supports careful, multi-source reasoning; the other options overstate or contradict that logic.",
        })

    return base_questions


def _render_quiz(quiz: dict) -> str:
    questions = quiz.get("questions", [])
    lines = [
        "📘 TOEFL 阅读题（2026 风格）",
        f"文章: {quiz.get('article_title') or 'Untitled'}",
        f"题量: {len(questions)}",
        "",
        "请一次性提交全部答案:",
        "!eng read answer 1A 2B 3C 4D ...",
        "或: !eng read answer ABCD...",
        "",
    ]

    for q in questions:
        qid = q.get("id")
        q_type = q.get("type") or "factual_information"
        lines.append(f"{qid}. [{QUESTION_TYPE_CN.get(q_type, q_type)}] {q.get('question')}")
        options = q.get("options") or {}
        for key in ("A", "B", "C", "D"):
            lines.append(f"   {key}. {options.get(key, '')}")
        lines.append("")

    lines.append("作答后可用: !eng read explain <题号> 查看定位与解析")
    return "\n".join(lines).strip()


def _parse_full_answers(raw: str, total: int) -> dict[int, str] | None:
    if total <= 0:
        return None
    text = (raw or "").strip().upper()
    if not text:
        return None

    # 形式1: 1A 2C 3D ...
    indexed = re.findall(r"(\d{1,2})\s*[:：.\-]?\s*([ABCD])", text)
    if indexed:
        answer_map: dict[int, str] = {}
        for idx_str, choice in indexed:
            idx = int(idx_str)
            if 1 <= idx <= total:
                answer_map[idx] = choice
        if len(answer_map) == total:
            return answer_map

    # 形式2: ABCD...（长度必须等于题量）
    letters = re.sub(r"[^ABCD]", "", text)
    if len(letters) == total:
        return {i + 1: letters[i] for i in range(total)}

    # 形式3: A B C D ...
    tokens = re.findall(r"\b([ABCD])\b", text)
    if len(tokens) == total:
        return {i + 1: tokens[i] for i in range(total)}

    return None


def _split_paragraphs(article_text: str) -> list[str]:
    return [p.strip() for p in (article_text or "").split("\n\n") if p.strip()]


def generate_reading_quiz(question_count: int = 8) -> str:
    count = max(5, min(12, int(question_count or 8)))
    article = _latest_article()
    if not article:
        return "⚠️ 还没有可用文章。先发送“文章”或“文章 生物/科技”生成一篇。"

    questions = _generate_questions_with_llm(article, count)
    if len(questions) < count:
        questions = _generate_questions_fallback(article, count)

    state = _load_state()
    quiz = {
        "article_id": article.id,
        "article_title": article.title,
        "generated_at": datetime.now().isoformat(),
        "questions": questions,
        "user_answers": {},
        "score": None,
        "correct_count": None,
    }
    state["latest_quiz"] = quiz
    _save_state(state)

    _record_event("reading_quiz_generated", {
        "article_id": article.id,
        "question_count": len(questions),
    })

    return _render_quiz(quiz)


def submit_reading_answers(answer_text: str) -> str:
    state = _load_state()
    quiz = state.get("latest_quiz") if isinstance(state.get("latest_quiz"), dict) else None
    if not quiz:
        return "⚠️ 还没有阅读题。先发送 !eng read quiz。"

    questions = quiz.get("questions") if isinstance(quiz.get("questions"), list) else []
    if not questions:
        return "⚠️ 当前阅读题为空，请重新发送 !eng read quiz。"

    answer_map = _parse_full_answers(answer_text, len(questions))
    if not answer_map:
        return "⚠️ 答案格式不对。请一次性作答，例如: !eng read answer 1A 2C 3B 4D 或 !eng read answer ACBD..."

    details = []
    correct_count = 0
    user_answers = {}
    for q in questions:
        idx = int(q.get("id") or 0)
        correct = str(q.get("answer") or "A").upper()
        user = answer_map.get(idx, "")
        user_answers[str(idx)] = user
        is_correct = user == correct
        if is_correct:
            correct_count += 1
        details.append({
            "id": idx,
            "user": user,
            "correct": correct,
            "is_correct": is_correct,
            "type": q.get("type") or "factual_information",
        })

    total = len(questions)
    percent = round((correct_count / total) * 100, 1) if total else 0.0
    toefl_scaled = round((correct_count / total) * 30, 1) if total else 0.0

    quiz["submitted_at"] = datetime.now().isoformat()
    quiz["user_answers"] = user_answers
    quiz["correct_count"] = correct_count
    quiz["score"] = {
        "percent": percent,
        "toefl_scaled": toefl_scaled,
        "total": total,
    }
    state["latest_quiz"] = quiz
    _save_state(state)

    _record_event("reading_quiz_scored", {
        "article_id": quiz.get("article_id"),
        "total": total,
        "correct": correct_count,
        "toefl_scaled": toefl_scaled,
    })

    lines = [
        "📊 阅读题评分完成",
        f"总分: {correct_count}/{total} ({percent}%)",
        f"TOEFL 估算分: {toefl_scaled}/30",
        "",
    ]
    for item in details:
        marker = "✅" if item["is_correct"] else "❌"
        lines.append(f"{item['id']}. {marker} 你的答案 {item['user']} | 正确答案 {item['correct']} ({QUESTION_TYPE_CN.get(item['type'], item['type'])})")

    lines.extend([
        "",
        "查看单题解析:",
        "!eng read explain <题号>",
    ])
    return "\n".join(lines)


def explain_question(question_no: int) -> str:
    state = _load_state()
    quiz = state.get("latest_quiz") if isinstance(state.get("latest_quiz"), dict) else None
    if not quiz:
        return "⚠️ 还没有阅读题记录。先发送 !eng read quiz。"

    questions = quiz.get("questions") if isinstance(quiz.get("questions"), list) else []
    if not questions:
        return "⚠️ 当前没有可解析的题目。"

    try:
        idx = int(question_no)
    except Exception:
        return "⚠️ 题号格式不对。用法: !eng read explain <题号>"

    if idx < 1 or idx > len(questions):
        return f"⚠️ 题号超出范围。当前题号范围: 1-{len(questions)}"

    q = questions[idx - 1]
    article_id = quiz.get("article_id")
    article = _article_by_id(article_id) if article_id else None
    paragraphs = _split_paragraphs(article.content if article else "")

    source_para = int(q.get("source_paragraph") or 1)
    if source_para < 1:
        source_para = 1
    para_text = ""
    if 1 <= source_para <= len(paragraphs):
        para_text = paragraphs[source_para - 1]

    user_answers = quiz.get("user_answers") if isinstance(quiz.get("user_answers"), dict) else {}
    user_answer = str(user_answers.get(str(idx)) or "未作答")

    lines = [
        f"🧩 第 {idx} 题解析（{QUESTION_TYPE_CN.get(q.get('type'), q.get('type'))}）",
        f"题目: {q.get('question')}",
        f"正确答案: {q.get('answer')}",
        f"你的答案: {user_answer}",
        "",
        "为什么选这个:",
        q.get("explanation") or "该选项最完整地匹配原文证据，其他选项存在信息缺失或过度推断。",
        "",
        f"对应原文段落: P{source_para}",
    ]
    if q.get("source_quote"):
        lines.append(f"命中证据句: {q.get('source_quote')}")
    if para_text:
        lines.append("")
        lines.append("原文定位片段:")
        lines.append(_clip(para_text, 900))

    return "\n".join(lines)


def read_help() -> str:
    return "\n".join([
        "📘 阅读题命令（文章与题目分离）",
        "- !eng read quiz [题量]  基于最近一篇文章生成 TOEFL 阅读题（默认 8 题）",
        "- !eng read answer <全部答案>  一次性提交整卷（如 1A 2B 3C... 或 ABCD...）",
        "- !eng read explain <题号>  查看该题对应原文定位与解析",
        "- !eng read help",
    ])


def handle_read_command(rest: str) -> str:
    text = (rest or "").strip()
    if not text or text.lower() in {"help", "h", "?"}:
        return read_help()

    parts = text.split(None, 1)
    action = parts[0].lower()
    payload = parts[1].strip() if len(parts) > 1 else ""

    if action in {"quiz", "q", "new"}:
        count = 8
        if payload.isdigit():
            count = int(payload)
        return generate_reading_quiz(count)

    if action in {"answer", "submit", "a"}:
        if not payload:
            return "⚠️ 请一次性提交全部答案，例如: !eng read answer 1A 2C 3B 4D"
        return submit_reading_answers(payload)

    if action in {"explain", "exp", "e"}:
        if not payload:
            return "⚠️ 用法: !eng read explain <题号>"
        return explain_question(payload)

    # 兜底：如果用户直接贴答案串，也按 answer 处理。
    state = _load_state()
    latest_quiz = state.get("latest_quiz") if isinstance(state.get("latest_quiz"), dict) else None
    total = len(latest_quiz.get("questions", [])) if isinstance(latest_quiz, dict) else 8
    parsed = _parse_full_answers(text, total)
    if parsed:
        return submit_reading_answers(text)

    return "⚠️ 用法: !eng read quiz [题量] / !eng read answer <全部答案> / !eng read explain <题号>"
