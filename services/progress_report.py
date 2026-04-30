#!/usr/bin/env python3
"""每周 AI 学习报告：聚合 SM-2/写作/口语/测验事件，让 minimax 生成诊断。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from statistics import mean

from database import SessionLocal
from models import (
    EssayScore,
    QuizAttempt,
    ReviewLog,
    SpeakingScore,
    Vocabulary,
)
from services.llm_client import chat_json, is_configured


REPORT_SYSTEM_PROMPT = """You are a personal TOEFL coach writing a concise weekly progress report in Chinese.

Given the aggregated stats, return a JSON object with this exact shape:
{
  "headline": string,             // 20-30 Chinese chars, motivating
  "summary": string,              // 2-3 sentences, plain Chinese
  "strengths": [string, string],  // Chinese bullet points
  "weaknesses": [string, string], // Chinese bullet points
  "next_week_plan": [string, string, string]  // Chinese concrete actions
}

Keep tone encouraging but honest. Reference numbers from the stats when possible.
"""


def _collect_stats(days: int = 7) -> dict:
    since = datetime.now() - timedelta(days=days)
    db = SessionLocal()
    try:
        reviews = db.query(ReviewLog).filter(ReviewLog.created_at >= since).all()
        essays = db.query(EssayScore).filter(EssayScore.created_at >= since).all()
        speakings = db.query(SpeakingScore).filter(SpeakingScore.created_at >= since).all()
        quizzes = db.query(QuizAttempt).filter(QuizAttempt.created_at >= since).all()
        vocab_total = db.query(Vocabulary).count()
        vocab_due = db.query(Vocabulary).filter(
            (Vocabulary.next_review_at == None) | (Vocabulary.next_review_at <= datetime.now())
        ).count()
    finally:
        db.close()

    review_grades = [r.grade for r in reviews]
    quiz_correct = sum(1 for q in quizzes if q.is_correct)

    stats = {
        "window_days": days,
        "vocab_total": vocab_total,
        "vocab_due_today": vocab_due,
        "reviews_count": len(reviews),
        "reviews_avg_grade": round(mean(review_grades), 2) if review_grades else None,
        "reviews_again_rate": round(
            sum(1 for g in review_grades if g < 3) / len(review_grades), 2
        ) if review_grades else None,
        "essays_count": len(essays),
        "essays_avg_overall": round(mean([e.overall_score for e in essays]), 2) if essays else None,
        "essays_grammar_avg": round(mean([e.grammar for e in essays]), 2) if essays else None,
        "essays_language_avg": round(mean([e.language_use for e in essays]), 2) if essays else None,
        "speakings_count": len(speakings),
        "speakings_avg_overall": round(mean([s.overall_score for s in speakings]), 2) if speakings else None,
        "speakings_delivery_avg": round(mean([s.delivery for s in speakings]), 2) if speakings else None,
        "quizzes_count": len(quizzes),
        "quizzes_accuracy": round(quiz_correct / len(quizzes), 2) if quizzes else None,
    }
    return stats


def _static_report_fallback(stats: dict) -> str:
    lines = [
        "📊 本周英语学习报告",
        f"• 词库总数: {stats['vocab_total']}",
        f"• 今日到期待复习: {stats['vocab_due_today']}",
        f"• SM-2 复习次数: {stats['reviews_count']}"
        + (f" (平均评分 {stats['reviews_avg_grade']})" if stats['reviews_avg_grade'] is not None else ""),
        f"• 写作评分次数: {stats['essays_count']}"
        + (f" (均分 {stats['essays_avg_overall']}/30)" if stats['essays_avg_overall'] is not None else ""),
        f"• 口语评分次数: {stats['speakings_count']}"
        + (f" (均分 {stats['speakings_avg_overall']}/4)" if stats['speakings_avg_overall'] is not None else ""),
        f"• 小测次数: {stats['quizzes_count']}"
        + (f" (准确率 {int((stats['quizzes_accuracy'] or 0) * 100)}%)" if stats['quizzes_accuracy'] is not None else ""),
        "",
        "发送 !eng review 开始 SM-2 复习，!eng essay / !eng speak 提交作文或口语。",
    ]
    return "\n".join(lines)


def generate_report(days: int = 7) -> str:
    stats = _collect_stats(days)

    if not is_configured():
        return _static_report_fallback(stats)

    messages = [
        {"role": "system", "content": REPORT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "Aggregated stats:\n" + json.dumps(stats, ensure_ascii=False, indent=2),
        },
    ]
    result = chat_json(messages, temperature=0.4, max_tokens=800, timeout=60)
    if not result:
        return _static_report_fallback(stats)

    headline = (result.get("headline") or "").strip()
    summary = (result.get("summary") or "").strip()
    strengths = result.get("strengths") or []
    weaknesses = result.get("weaknesses") or []
    plan = result.get("next_week_plan") or []

    lines = [
        "📊 本周英语学习报告",
        headline if headline else "持续稳扎稳打",
        "",
        summary,
        "",
        f"• 词库总数 {stats['vocab_total']} / 今日到期 {stats['vocab_due_today']}",
        f"• SM-2 复习 {stats['reviews_count']} 次"
        + (f" (均分 {stats['reviews_avg_grade']})" if stats['reviews_avg_grade'] is not None else ""),
        f"• 写作 {stats['essays_count']} 篇"
        + (f" (均分 {stats['essays_avg_overall']}/30)" if stats['essays_avg_overall'] is not None else ""),
        f"• 口语 {stats['speakings_count']} 次"
        + (f" (均分 {stats['speakings_avg_overall']}/4)" if stats['speakings_avg_overall'] is not None else ""),
        f"• 小测 {stats['quizzes_count']} 次"
        + (f" (准确率 {int((stats['quizzes_accuracy'] or 0) * 100)}%)" if stats['quizzes_accuracy'] is not None else ""),
    ]

    if strengths:
        lines.append("")
        lines.append("亮点:")
        for item in strengths[:3]:
            lines.append(f"  ✓ {item}")
    if weaknesses:
        lines.append("")
        lines.append("待加强:")
        for item in weaknesses[:3]:
            lines.append(f"  ✗ {item}")
    if plan:
        lines.append("")
        lines.append("下周计划:")
        for idx, item in enumerate(plan[:4], 1):
            lines.append(f"  {idx}. {item}")

    return "\n".join(lines)
