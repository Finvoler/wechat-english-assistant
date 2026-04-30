#!/usr/bin/env python3
"""数据库模型定义：核心词汇/文章表 + SM-2 复习、写作/口语评分、测验、学习事件。"""
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Vocabulary(Base):
    __tablename__ = "vocabulary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    word: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    definition: Mapped[str] = mapped_column(Text, default="")
    example: Mapped[str] = mapped_column(Text, default="")
    review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    added_via: Mapped[str] = mapped_column(String(30), default="manual", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    last_reviewed: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # SM-2 间隔重复调度字段
    ease_factor: Mapped[float] = mapped_column(Float, default=2.5, nullable=False)
    interval_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    repetitions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lapses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    word_list: Mapped[str] = mapped_column(Text, nullable=False)
    questions: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    difficulty_level: Mapped[str] = mapped_column(String(30), default="TOEFL", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)


class ReviewLog(Base):
    """每次 SM-2 复习的判定日志，用于学习曲线和周报。"""
    __tablename__ = "review_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    word: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    grade: Mapped[int] = mapped_column(Integer, nullable=False)
    ease_before: Mapped[float] = mapped_column(Float, default=2.5, nullable=False)
    ease_after: Mapped[float] = mapped_column(Float, default=2.5, nullable=False)
    interval_after: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)


class EssayScore(Base):
    """TOEFL 写作评分记录。支持 2026 新题型 academic_discussion / email / independent / build_sentence。"""
    __tablename__ = "essay_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prompt_type: Mapped[str] = mapped_column(String(30), default="independent", nullable=False)
    task_type: Mapped[str] = mapped_column(String(40), default="independent", nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    essay_text: Mapped[str] = mapped_column(Text, nullable=False)
    overall_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    holistic_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    task_response: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    coherence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    language_use: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    grammar: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    feedback_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)


class SpeakingScore(Base):
    """TOEFL 口语独立题应答评分记录（文字版，后续可扩语音）。"""
    __tablename__ = "speaking_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    overall_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    delivery: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    language_use: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    topic_development: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    feedback_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)


class QuizAttempt(Base):
    """自适应小测记录。"""
    __tablename__ = "quiz_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    word: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    question_type: Mapped[str] = mapped_column(String(30), default="cloze", nullable=False)
    question_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    correct_answer: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    user_answer: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    is_correct: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)


class StudyEvent(Base):
    """泛用学习事件，用于周报聚合。"""
    __tablename__ = "study_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
