#!/usr/bin/env python3
"""自适应小测：挑弱项词，让 minimax 生成 TOEFL 风格的选择题，记录答题结果。"""
from __future__ import annotations

import json
import random
import re
from datetime import datetime
from pathlib import Path

from database import SessionLocal
from models import QuizAttempt, StudyEvent, Vocabulary
from services.llm_client import chat_json, is_configured


project_root = Path(__file__).resolve().parent.parent
quiz_state_file = project_root / "data" / "quiz_state.json"


QUIZ_SYSTEM_PROMPT = """You are a TOEFL vocabulary question writer.

Given a target word and its definition, generate ONE TOEFL-style vocabulary-in-context
multiple choice question with 4 options.

Output strict JSON only:
{
  "question_stem": string,   // A short academic sentence with the target word in context.
  "highlighted_word": string,// The target word exactly as used in the stem.
  "options": {"A": string, "B": string, "C": string, "D": string},
  "correct": "A"|"B"|"C"|"D",
  "explanation": string       // Short reason why the answer is correct, mentioning the other distractors.
}

Rules:
- The question_stem must embed the target word in a sentence that hints at its meaning.
- Options must all be plausible English words, but only one matches the meaning.
- Do not include Chinese.
"""


def _ensure_state_dir() -> None:
    quiz_state_file.parent.mkdir(parents=True, exist_ok=True)


def _load_state() -> dict:
    if not quiz_state_file.exists():
        return {}
    try:
        with open(quiz_state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    _ensure_state_dir()
    with open(quiz_state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _pick_weak_word() -> Vocabulary | None:
    db = SessionLocal()
    try:
        # 优先挑 lapses 高 + repetitions 低的词
        candidates = db.query(Vocabulary).order_by(
            Vocabulary.lapses.desc(),
            Vocabulary.repetitions.asc(),
            Vocabulary.review_count.asc(),
        ).limit(20).all()
        if not candidates:
            return None
        # 在前 20 个弱项里随机，避免每次都同一个
        top = candidates[: max(3, len(candidates) // 2)]
        return random.choice(top)
    finally:
        db.close()


def generate_quiz() -> str:
    vocab = _pick_weak_word()
    if not vocab:
        return "📭 词库里还没有词，先发几个托福词再来测试。"

    if not is_configured():
        return "⚠️ 当前 LLM 未配置，无法出题。"

    definition = (vocab.definition or "").strip() or f"TOEFL-level academic word: {vocab.word}"
    messages = [
        {"role": "system", "content": QUIZ_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Target word: {vocab.word}\nDefinition: {definition}",
        },
    ]
    result = chat_json(messages, temperature=0.5, max_tokens=700, timeout=60)
    if not result:
        return "⚠️ 出题暂时失败，请稍后再试。"

    stem = (result.get("question_stem") or "").strip()
    options = result.get("options") or {}
    correct = (result.get("correct") or "").strip().upper()
    if not stem or not options or correct not in {"A", "B", "C", "D"}:
        return "⚠️ 生成的题目格式不正确，请再试一次。"

    _save_state({
        "word": vocab.word,
        "question": result,
        "started_at": datetime.now().isoformat(),
    })

    lines = [
        "🧪 TOEFL 词义辨析",
        f"目标词: {vocab.word}",
        "",
        stem,
        "",
    ]
    for key in ["A", "B", "C", "D"]:
        value = (options.get(key) or "").strip()
        lines.append(f"  {key}. {value}")
    lines.append("")
    lines.append("回复 !eng quiz answer <A/B/C/D> 提交答案。")
    return "\n".join(lines)


def answer_quiz(letter: str) -> str:
    state = _load_state()
    question = state.get("question")
    word = state.get("word")
    if not question or not word:
        return "⚠️ 当前没有进行中的小测。发送 !eng quiz 出题。"

    letter = (letter or "").strip().upper()
    if letter not in {"A", "B", "C", "D"}:
        return "⚠️ 请回复 A / B / C / D。"

    correct = (question.get("correct") or "").upper()
    options = question.get("options") or {}
    explanation = (question.get("explanation") or "").strip()

    is_correct = int(letter == correct)

    try:
        db = SessionLocal()
        try:
            db.add(QuizAttempt(
                word=word,
                question_type="vocab_mcq",
                question_json=json.dumps(question, ensure_ascii=False),
                correct_answer=correct,
                user_answer=letter,
                is_correct=is_correct,
            ))
            db.add(StudyEvent(
                event_type="quiz_answered",
                payload_json=json.dumps({
                    "word": word,
                    "correct": bool(is_correct),
                }, ensure_ascii=False),
            ))

            # 答错时把词标回到期，下一次复习自动覆盖到它
            vocab = db.query(Vocabulary).filter_by(word=word).first()
            if vocab and not is_correct:
                vocab.lapses = (vocab.lapses or 0) + 1
                vocab.next_review_at = datetime.now()
            db.commit()
        finally:
            db.close()
    except Exception:
        pass

    _save_state({})

    result_tag = "✅ 正确" if is_correct else f"❌ 错误，正确答案是 {correct}: {options.get(correct, '').strip()}"
    lines = [
        result_tag,
        "",
        f"解析: {explanation}" if explanation else "",
        "",
        "继续练习发送 !eng quiz；进入 SM-2 复习发送 !eng review。",
    ]
    return "\n".join([line for line in lines if line is not None])
