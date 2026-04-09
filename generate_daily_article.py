#!/usr/bin/env python3
"""
每日文章生成脚本
通过OpenClaw执行，每天9点自动生成包含用户生词的文章
"""
import os
import sys
import json
import asyncio
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

async def generate_daily_article():
    """生成每日文章"""
    try:
        logger.info("开始生成每日文章...")

        # 在每日任务开始时同步本地 txt 词库，支持电脑端快速批量维护。
        added, skipped = import_words(DEFAULT_FILE)
        logger.info(f"词库文件同步完成: 新增 {added}, 跳过 {skipped}")
        
        # 获取数据库会话
        db = SessionLocal()
        
        try:
            # 获取用户最近添加的生词
            recent_words = db.query(Vocabulary).filter(
                Vocabulary.review_count < 5
            ).order_by(
                Vocabulary.created_at.desc()
            ).limit(10).all()
            
            if not recent_words:
                logger.warning("没有找到需要复习的生词")
                # 使用一些默认单词
                word_list = ["cognitive", "diverse", "paradigm", "sustainable", "ambiguous"]
            else:
                word_list = [word.word for word in recent_words]
            
            logger.info(f"使用单词列表: {word_list}")
            
            # 创建文章生成器
            generator = ArticleGenerator()
            
            # 生成文章
            result = await generator.generate_article_with_words(word_list)
            
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
                
                # 更新单词复习计数
                for word in recent_words:
                    word.review_count += 1
                    word.last_reviewed = datetime.now()
                
                db.commit()
                
                logger.info(f"文章生成成功: {result['title']}")
                
                # 输出文章内容
                output = {
                    "title": result["title"],
                    "content": result["content"],
                    "questions": result.get("questions", []),
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
                if result.get("questions"):
                    wechat_message += "\n\n【阅读理解】"
                    for idx, q in enumerate(result["questions"]):
                        if isinstance(q, str):
                            question_text = q
                        else:
                            question_text = q.get('question_text') or q.get('question', '')
                        wechat_message += f"\n{idx+1}. {question_text}"
                
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
