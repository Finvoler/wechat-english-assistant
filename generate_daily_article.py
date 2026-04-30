#!/usr/bin/env python3
"""
每日文章生成脚本
通过OpenClaw执行，每天9点自动生成包含用户生词的文章
"""
import os
import sys
import json
import asyncio
import hashlib
from pathlib import Path
from datetime import datetime

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from database import SessionLocal, get_db
from services.article_generator import ArticleGenerator
from models import Vocabulary, Article
from sync_words_file import import_words, DEFAULT_FILE
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORD_PROGRESS_FILE = project_root / "data" / "word_progress.json"
ARTICLE_TARGET_WORDS = 24
ARTICLE_HASH_HISTORY_FILE = project_root / "data" / "article_hash_history.json"
ARTICLE_HASH_HISTORY_MAX = 500
ARTICLE_GENERATION_MAX_RETRIES = 5


def _load_word_progress() -> dict:
    if not WORD_PROGRESS_FILE.exists():
        return {}
    try:
        with open(WORD_PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_word_progress(progress: dict) -> None:
    WORD_PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(WORD_PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def _load_article_hash_history() -> list[str]:
    if not ARTICLE_HASH_HISTORY_FILE.exists():
        return []
    try:
        with open(ARTICLE_HASH_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(item) for item in data if str(item).strip()]
    except Exception:
        return []
    return []


def _save_article_hash_history(hashes: list[str]) -> None:
    ARTICLE_HASH_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    trimmed = [str(item).strip() for item in hashes if str(item).strip()][-ARTICLE_HASH_HISTORY_MAX:]
    with open(ARTICLE_HASH_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)


def _article_content_hash(title: str, content: str) -> str:
    normalized_title = (title or "").strip().lower()
    normalized_content = " ".join((content or "").strip().lower().split())
    digest = hashlib.sha256(f"{normalized_title}\n{normalized_content}".encode("utf-8")).hexdigest()
    return digest


def _select_words_for_article(db, target_count: int = ARTICLE_TARGET_WORDS):
    progress = _load_word_progress()
    candidates = db.query(Vocabulary).order_by(
        Vocabulary.review_count.asc(),
        Vocabulary.created_at.desc()
    ).limit(400).all()

    if not candidates:
        return []

    def score(item):
        meta = progress.get(item.word, {})
        embedded_count = int(meta.get("embedded_count", 0))
        selected_count = int(meta.get("selected_count", 0))
        last_selected_at = meta.get("last_selected_at") or ""
        return (
            embedded_count > 0,
            embedded_count,
            selected_count,
            item.review_count,
            last_selected_at,
            -len(item.word),
            -(item.created_at.timestamp() if item.created_at else 0),
        )

    ranked = sorted(candidates, key=score)
    return ranked[:target_count]


def _update_word_progress(selected_words: list[str], embedded_words: list[str]) -> None:
    progress = _load_word_progress()
    now = datetime.now().isoformat()

    for word in selected_words:
        entry = progress.setdefault(word, {})
        entry["selected_count"] = int(entry.get("selected_count", 0)) + 1
        entry["last_selected_at"] = now

    for word in embedded_words:
        entry = progress.setdefault(word, {})
        entry["embedded_count"] = int(entry.get("embedded_count", 0)) + 1
        entry["last_embedded_at"] = now

    _save_word_progress(progress)

async def generate_daily_article(topic_hint: str | None = None):
    """生成每日文章"""
    try:
        logger.info("开始生成每日文章...")

        # 在每日任务开始时同步本地 txt 词库，支持电脑端快速批量维护。
        added, skipped = import_words(DEFAULT_FILE)
        logger.info(f"词库文件同步完成: 新增 {added}, 跳过 {skipped}")
        
        # 获取数据库会话
        db = SessionLocal()
        
        try:
            selected_records = _select_words_for_article(db)

            if not selected_records:
                logger.warning("没有找到需要复习的生词")
                word_list = ["cognitive", "diverse", "paradigm", "sustainable", "ambiguous"]
            else:
                word_list = [word.word for word in selected_records]
            
            logger.info(f"使用单词列表: {word_list}")
            
            # 创建文章生成器
            generator = ArticleGenerator()
            
            # 生成文章（去重：命中历史 hash 则重试，避免重复文章反复出现）
            history_hashes = _load_article_hash_history()
            result = None
            for _ in range(ARTICLE_GENERATION_MAX_RETRIES):
                candidate = await generator.generate_article_with_words(word_list, topic_hint=topic_hint)
                if not candidate:
                    continue
                content_hash = _article_content_hash(candidate.get("title", ""), candidate.get("content", ""))
                if content_hash in history_hashes:
                    logger.warning("检测到重复文章，自动重试生成")
                    continue
                candidate["content_hash"] = content_hash
                result = candidate
                history_hashes.append(content_hash)
                _save_article_hash_history(history_hashes)
                break
            
            if result:
                logger.info(f"文章来源: {result.get('source', 'unknown')}")
                # 保存到数据库
                article = Article(
                    title=result["title"],
                    content=result["content"],
                    word_list=json.dumps(word_list),
                    questions=json.dumps(result.get("questions", [])),
                    difficulty_level="TOEFL-like"
                )
                db.add(article)
                db.commit()
                db.refresh(article)
                
                # 更新单词复习计数与文章嵌入进度
                for word in selected_records:
                    word.review_count += 1
                    word.last_reviewed = datetime.now()
                
                db.commit()
                _update_word_progress(word_list, result.get("embedded_words", []))
                
                logger.info(f"文章生成成功: {result['title']}")
                
                # 输出文章内容
                output = {
                    "article_id": article.id,
                    "title": result["title"],
                    "content": result["content"],
                    "questions": result.get("questions", []),
                    "embedded_words": result.get("embedded_words", []),
                    "references": result.get("references", []),
                    "cache_id": result.get("cache_id"),
                    "source_topic": result.get("source_topic"),
                    "topic_hint": result.get("topic_hint") or topic_hint,
                    "source": result.get("source", "unknown"),
                    "content_hash": result.get("content_hash"),
                    "word_list": word_list,
                    "timestamp": datetime.now().isoformat()
                }
                
                print(json.dumps(output, indent=2, ensure_ascii=False))
                
                # 保存到文件
                output_file = project_root / "output" / f"article_{datetime.now().strftime('%Y%m%d')}.json"
                output_file.parent.mkdir(exist_ok=True)
                
                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(output, f, indent=2, ensure_ascii=False)
                
                # 将文章内容打印到标准输出，如果是由OpenClaw直接调用的，它会自动捕获并发送回微信
                wechat_message = f"【每日英语文章】\n{result['title']}\n\n{result['content']}"
                if result.get("embedded_words"):
                    wechat_message += f"\n\n【本篇重点词】{', '.join(result['embedded_words'][:24])}"
                if result.get("references"):
                    wechat_message += "\n\n【参考来源】"
                    for idx, ref in enumerate(result["references"][:3], 1):
                        title = ref.get("title") or ref.get("domain") or "Academic source"
                        url = ref.get("url", "")
                        wechat_message += f"\n{idx}. {title} {url}".rstrip()
                
                print("\n================== 文章内容 ==================\n")
                print(wechat_message)
                print("\n==============================================\n")
                
                return output
            else:
                logger.error("文章生成失败")
                return None
                
        finally:
            db.close()
            
    except Exception as e:
        logger.error(f"生成文章时出错: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    # 运行异步函数
    result = asyncio.run(generate_daily_article())
    
    if result:
        sys.exit(0)
    else:
        sys.exit(1)
