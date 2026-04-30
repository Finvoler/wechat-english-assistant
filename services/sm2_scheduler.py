#!/usr/bin/env python3
"""SM-2 间隔重复调度器：生成每日复习队列，按用户反馈更新记忆曲线。

评分约定（参考 SM-2 原始论文）：
- 5 again too easy, 4 remembered with hesitation, 3 correct with effort,
- 2 wrong but familiar, 1 wrong, 0 blackout.

微信场景只暴露 3 个按键：
- good -> grade 4
- easy -> grade 5
- again -> grade 2
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from sqlalchemy import asc, or_

from database import SessionLocal
from models import ReviewLog, StudyEvent, Vocabulary
from services.llm_client import chat_completion


project_root = Path(__file__).resolve().parent.parent
review_state_file = project_root / "data" / "review_state.json"

# 内存缓存：本轮复习中已生成的释义（word -> definition）
_generated_definitions_cache: dict[str, str] = {}

GRADE_ALIASES = {
    "easy": 5,
    "good": 4,
    "ok": 3,
    "hard": 2,
    "again": 1,
    "forgot": 0,
    "5": 5,
    "4": 4,
    "3": 3,
    "2": 2,
    "1": 1,
    "0": 0,
}


def _ensure_state_dir() -> None:
    review_state_file.parent.mkdir(parents=True, exist_ok=True)


def _load_state() -> dict:
    if not review_state_file.exists():
        return {"current_queue": [], "current_word": None, "session_started_at": None}
    try:
        with open(review_state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"current_queue": [], "current_word": None, "session_started_at": None}


def _save_state(state: dict) -> None:
    _ensure_state_dir()
    with open(review_state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _sm2_update(vocab: Vocabulary, grade: int) -> tuple[float, int, int]:
    """返回更新后的 ease_factor, interval_days, repetitions。"""
    ease = vocab.ease_factor or 2.5
    repetitions = vocab.repetitions or 0
    interval = vocab.interval_days or 0

    if grade < 3:
        repetitions = 0
        interval = 1
    else:
        if repetitions == 0:
            interval = 1
        elif repetitions == 1:
            interval = 3
        else:
            interval = max(1, round(interval * ease))
        repetitions += 1

    ease = ease + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
    if ease < 1.3:
        ease = 1.3

    return ease, interval, repetitions


def _pick_due_words(limit: int = 10) -> list[Vocabulary]:
    db = SessionLocal()
    try:
        now = datetime.now()
        query = db.query(Vocabulary).filter(
            or_(Vocabulary.next_review_at == None, Vocabulary.next_review_at <= now)
        ).order_by(
            asc(Vocabulary.next_review_at),
            asc(Vocabulary.repetitions),
            asc(Vocabulary.review_count),
        )
        return query.limit(limit).all()
    finally:
        db.close()


def _generate_definition(word: str) -> str:
    """调用 LLM 生成单词释义，失败时返回空字符串。"""
    if word in _generated_definitions_cache:
        return _generated_definitions_cache[word]

    messages = [
        {
            "role": "system",
            "content": (
                "You are a professional English vocabulary teacher. "
                "Given an English word, respond with ONLY its definition in English. "
                "Be concise (within 20 words). No markdown, no explanation, no example sentences."
            ),
        },
        {"role": "user", "content": word},
    ]
    result = chat_completion(messages, temperature=0.3, max_tokens=150, timeout=30)
    if result:
        result = result.strip()
        _generated_definitions_cache[word] = result
    else:
        result = ""
    return result


def _format_card(vocab: Vocabulary, position: int, total: int) -> str:
    definition = (vocab.definition or "").strip()
    if not definition:
        definition = _generate_definition(vocab.word)
        if not definition:
            definition = "(no definition stored)"
    example = (vocab.example or "").strip()
    lines = [
        f"📇 SM-2 复习 {position}/{total}",
        f"单词: {vocab.word}",
        f"释义: {definition}",
    ]
    if example:
        lines.append(f"例句: {example}")
    lines.append("")
    lines.append("回复 good / easy / again 来评分。easy=全记得, good=有点模糊, again=忘了。")
    lines.append("或发送 !eng review skip 跳过本条，!eng review stop 结束复习。")
    return "\n".join(lines)


def start_review_session(limit: int = 10) -> str:
    global _generated_definitions_cache
    _generated_definitions_cache.clear()  # 开始新复习时清空缓存
    words = _pick_due_words(limit)
    if not words:
        return "🎉 今日暂时没有到期要复习的单词。继续加词或明天再来。"

    queue = [w.word for w in words]
    state = {
        "current_queue": queue,
        "current_word": queue[0],
        "session_started_at": datetime.now().isoformat(),
        "total": len(queue),
    }
    _save_state(state)

    head = words[0]
    return _format_card(head, 1, len(queue))


def _peek_current_vocab() -> tuple[dict, Vocabulary | None]:
    state = _load_state()
    current_word = state.get("current_word")
    if not current_word:
        return state, None
    db = SessionLocal()
    try:
        vocab = db.query(Vocabulary).filter_by(word=current_word).first()
    finally:
        db.close()
    return state, vocab


def _advance_queue(state: dict) -> tuple[str | None, int, int]:
    queue = state.get("current_queue", [])
    total = state.get("total") or len(queue) or 1
    if queue:
        queue.pop(0)
    next_word = queue[0] if queue else None
    state["current_queue"] = queue
    state["current_word"] = next_word
    position = (total - len(queue))
    if next_word is None:
        position = total
    _save_state(state)
    return next_word, position, total


def skip_current() -> str:
    state, _ = _peek_current_vocab()
    if not state.get("current_word"):
        return "⚠️ 当前没有进行中的 SM-2 复习。发送 !eng review 开始。"
    next_word, position, total = _advance_queue(state)
    if not next_word:
        return "✅ 今天的复习都跳过了。输入 !eng review 重新开始。"
    db = SessionLocal()
    try:
        vocab = db.query(Vocabulary).filter_by(word=next_word).first()
    finally:
        db.close()
    if not vocab:
        return "⚠️ 找不到下一张复习卡。"
    return _format_card(vocab, position + 1, total)


def stop_review() -> str:
    _save_state({"current_queue": [], "current_word": None, "session_started_at": None})
    return "🛑 已结束本次复习。"


def grade_current(grade_input: str) -> str:
    state, vocab = _peek_current_vocab()
    if not vocab:
        return "⚠️ 当前没有进行中的 SM-2 复习。发送 !eng review 开始。"

    normalized = grade_input.strip().lower()
    if normalized not in GRADE_ALIASES:
        return "⚠️ 请用 good / easy / again / hard / ok 评分，或数字 0-5。"
    grade = GRADE_ALIASES[normalized]

    ease_before = vocab.ease_factor or 2.5
    ease_after, interval_after, repetitions_after = _sm2_update(vocab, grade)

    db = SessionLocal()
    try:
        persistent = db.query(Vocabulary).filter_by(id=vocab.id).first()
        if not persistent:
            return "⚠️ 单词已被删除。"

        persistent.ease_factor = ease_after
        persistent.interval_days = interval_after
        persistent.repetitions = repetitions_after
        persistent.review_count = (persistent.review_count or 0) + 1
        persistent.last_reviewed = datetime.now()
        persistent.next_review_at = datetime.now() + timedelta(days=interval_after)
        if grade < 3:
            persistent.lapses = (persistent.lapses or 0) + 1

        db.add(ReviewLog(
            word=persistent.word,
            grade=grade,
            ease_before=ease_before,
            ease_after=ease_after,
            interval_after=interval_after,
        ))
        db.add(StudyEvent(
            event_type="sm2_review",
            payload_json=json.dumps({
                "word": persistent.word,
                "grade": grade,
                "interval_days": interval_after,
                "ease_factor": ease_after,
            }, ensure_ascii=False),
        ))
        db.commit()
    finally:
        db.close()

    next_word, position, total = _advance_queue(state)
    summary = (
        f"✅ {vocab.word}: 下次复习 {interval_after} 天后, "
        f"难度系数 {ease_after:.2f}"
    )

    if not next_word:
        return f"{summary}\n🎉 本轮复习结束，共复习 {total} 个词。发送 !eng report 看周报。"

    db = SessionLocal()
    try:
        next_vocab = db.query(Vocabulary).filter_by(word=next_word).first()
    finally:
        db.close()

    if not next_vocab:
        return f"{summary}\n⚠️ 找不到下一张卡。"

    return f"{summary}\n\n" + _format_card(next_vocab, position + 1, total)


def due_count() -> int:
    return len(_pick_due_words(1000))


def initialize_existing_words() -> int:
    """将老库里 next_review_at 为空的词当成新卡，今日可复习。"""
    db = SessionLocal()
    updated = 0
    try:
        rows = db.query(Vocabulary).filter(Vocabulary.next_review_at == None).all()
        now = datetime.now()
        for vocab in rows:
            vocab.next_review_at = now
            if not vocab.ease_factor:
                vocab.ease_factor = 2.5
            updated += 1
        db.commit()
    finally:
        db.close()
    return updated


def grade_aliases() -> Iterable[str]:
    return GRADE_ALIASES.keys()
