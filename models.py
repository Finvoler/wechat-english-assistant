#!/usr/bin/env python3
"""数据库模型定义。"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
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


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    word_list: Mapped[str] = mapped_column(Text, nullable=False)
    questions: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    difficulty_level: Mapped[str] = mapped_column(String(30), default="TOEFL", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
