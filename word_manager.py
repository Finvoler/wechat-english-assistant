#!/usr/bin/env python3
"""
单词管理脚本
用于通过微信添加和管理生词
"""
import sys
import os
from pathlib import Path
import json
import shlex
import re
from datetime import datetime

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from database import SessionLocal
from models import Vocabulary


COMMON_WORD_BLACKLIST = {
    "a", "an", "and", "are", "as", "at", "be", "book", "bye", "cat", "chat", "day",
    "do", "dog", "english", "fine", "food", "for", "game", "go", "good", "hello", "help",
    "hi", "home", "house", "i", "is", "it", "job", "like", "love", "man", "me", "morning",
    "name", "nice", "no", "not", "ok", "okay", "people", "phone", "play", "school", "see",
    "she", "shop", "sleep", "sorry", "student", "study", "teacher", "test", "thanks", "the",
    "there", "they", "time", "today", "tomorrow", "tv", "very", "walk", "want", "water", "we",
    "well", "what", "where", "who", "work", "world", "yes", "you",
}


def _is_worth_collecting(word: str) -> bool:
    normalized = word.strip().lower()
    if len(normalized) < 5:
        return False
    if normalized in COMMON_WORD_BLACKLIST:
        return False
    vowel_count = sum(1 for ch in normalized if ch in "aeiou")
    if len(normalized) <= 6 and vowel_count <= 2:
        return False
    return True


def _append_word_to_input_file(word, definition="", example=""):
    """将新词追加到 vocabulary_input.txt，避免微信与本地词库分叉。"""
    input_file = project_root / "vocabulary_input.txt"
    input_file.touch(exist_ok=True)

    existing_lines = input_file.read_text(encoding="utf-8").splitlines()
    normalized = {line.split("|")[0].strip().lower() for line in existing_lines if line.strip() and not line.strip().startswith("#")}
    if word.lower() in normalized:
        return

    if example:
        line = f"{word}|{definition}|{example}" if definition else f"{word}||{example}"
    elif definition:
        line = f"{word}|{definition}"
    else:
        line = word

    with open(input_file, "a", encoding="utf-8") as f:
        if existing_lines and existing_lines[-1].strip() != "":
            f.write("\n")
        f.write(line + "\n")

def add_word(word, definition="", example="", added_via="manual", verbose=True):
    """添加新单词"""
    db = SessionLocal()
    try:
        word = word.strip().lower()
        if not word or not re.fullmatch(r"[a-zA-Z][a-zA-Z\-']*", word):
            if verbose:
                print(f"非法单词输入: {word}")
            return False
        if added_via in ["wechat", "wechat-quick"] and not _is_worth_collecting(word):
            if verbose:
                print(f"单词过于基础，已跳过: {word}")
            return False

        # 检查单词是否已存在
        existing = db.query(Vocabulary).filter_by(word=word).first()
        
        if existing:
            if verbose:
                print(f"单词 '{word}' 已存在")
            return False
        
        # 添加新单词
        new_word = Vocabulary(
            word=word,
            definition=definition,
            example=example,
            added_via=added_via,
            review_count=0
        )
        
        db.add(new_word)
        db.commit()
        
        if verbose:
            print(f"单词 '{word}' 添加成功")

        if added_via in ["wechat", "wechat-quick"]:
            _append_word_to_input_file(word, definition, example)
        
        # 保存到文件备份
        save_to_file(new_word)
        
        return True
        
    except Exception as e:
        if verbose:
            print(f"添加单词时出错: {e}")
        return False
    finally:
        db.close()

def list_words(limit=20):
    """列出最近的单词"""
    db = SessionLocal()
    try:
        words = db.query(Vocabulary).order_by(
            Vocabulary.created_at.desc()
        ).limit(limit).all()
        
        result = []
        for word in words:
            result.append({
                "word": word.word,
                "definition": word.definition,
                "example": word.example,
                "created_at": word.created_at.isoformat(),
                "review_count": word.review_count
            })
        
        return result
        
    finally:
        db.close()


def list_writing_vocab(limit=12, min_length=6):
    """返回适合写作植入的本地词汇，优先近期/薄弱且定义较完整的词。"""
    db = SessionLocal()
    try:
        rows = db.query(Vocabulary).order_by(
            Vocabulary.lapses.desc(),
            Vocabulary.review_count.asc(),
            Vocabulary.created_at.desc(),
        ).all()

        result = []
        seen = set()
        for vocab in rows:
            word = (vocab.word or "").strip().lower()
            if not word or word in seen:
                continue
            if len(word) < min_length or not _is_worth_collecting(word):
                continue

            definition = (vocab.definition or "").strip()
            example = (vocab.example or "").strip()
            priority = 0
            priority += min(len(word), 12)
            if definition:
                priority += 3
            if example:
                priority += 2
            priority += min(int(getattr(vocab, "lapses", 0) or 0), 4)
            priority -= min(int(getattr(vocab, "review_count", 0) or 0), 5)

            result.append({
                "word": word,
                "definition": definition,
                "example": example,
                "review_count": int(getattr(vocab, "review_count", 0) or 0),
                "lapses": int(getattr(vocab, "lapses", 0) or 0),
                "priority": priority,
            })
            seen.add(word)

        result.sort(key=lambda item: (-item["priority"], item["word"]))
        return result[:limit]
    finally:
        db.close()

def save_to_file(vocab):
    """保存单词到文件备份"""
    backup_dir = project_root / "backup" / "vocabulary"
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    backup_file = backup_dir / "vocabulary.json"
    
    word_data = {
        "word": vocab.word,
        "definition": vocab.definition,
        "example": vocab.example,
        "added_via": vocab.added_via,
        "created_at": datetime.now().isoformat()
    }
    
    # 读取现有数据
    data = []
    if backup_file.exists():
        try:
            with open(backup_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except:
            data = []
    
    # 添加新数据
    data.append(word_data)
    
    # 保存
    with open(backup_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def process_wechat_command(command):
    """处理微信命令"""
    try:
        parts = shlex.split(command.strip())
    except ValueError:
        return "命令格式错误，请检查引号是否成对出现"
    
    if not parts:
        return "请输入有效命令"
    
    cmd = parts[0].lower()
    
    if cmd == "add":
        if len(parts) < 2:
            return "使用方法: add <单词> [定义] [例句]"
        
        word = parts[1]
        definition = parts[2] if len(parts) > 2 else ""
        example = " ".join(parts[3:]) if len(parts) > 3 else ""
        
        success = add_word(word, definition, example, added_via="wechat")
        
        if success:
            return f"✅ 单词 '{word}' 已添加到生词本"
        else:
            if not _is_worth_collecting(word):
                return f"ℹ️ '{word}' 过于基础，未加入生词本。请优先收集托福/学术词。"
            return f"❌ 添加单词失败"
    
    elif cmd == "list":
        words = list_words(10)
        if not words:
            return "📭 生词本为空"
        
        response = "📚 最近添加的单词:\n"
        for i, w in enumerate(words, 1):
            response += f"{i}. **{w['word']}**"
            if w['definition']:
                response += f" - {w['definition']}"
            response += "\n"
        
        return response
    
    elif cmd == "help":
        return """
📖 英语学习系统命令:
- add <单词> [定义] [例句] - 添加新单词
- list - 列出最近添加的单词
- sync - 从本地 vocabulary_input.txt 批量导入
- status - 查看学习状态
- help - 显示帮助信息
"""

    elif cmd == "sync":
        from sync_words_file import import_words, DEFAULT_FILE

        added, skipped = import_words(DEFAULT_FILE)
        return f"📥 批量导入完成: 新增 {added}，跳过 {skipped}\n文件: {DEFAULT_FILE}"

    elif cmd == "status":
        words = list_words(1000)
        total = len(words)
        reviewed = sum(1 for w in words if w.get("review_count", 0) > 0)
        return f"📊 学习状态\n- 总单词数: {total}\n- 已复习单词数: {reviewed}"
    
    else:
        return f"未知命令: {cmd}，使用 help 查看可用命令"


def add_word_from_plain_text(message_text):
    """极简模式：用户只发一个英文单词时直接入库。"""
    text = message_text.strip()
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z\-']*", text):
        return None
    if not _is_worth_collecting(text):
        return "ℹ️ 这个词过于基础，已跳过。请发更偏托福/学术的词。"

    success = add_word(text, added_via="wechat-quick")
    if success:
        return f"✅ 已快速添加单词: {text.lower()}"
    return f"ℹ️ 单词已存在或格式不合法: {text.lower()}"

if __name__ == "__main__":
    # 测试模式
    if len(sys.argv) > 1:
        if sys.argv[1] == "test":
            # 测试添加单词
            add_word("paradigm", "a typical example or pattern", "This study established a new paradigm for future research.")
            add_word("cognizant", "aware or having knowledge", "We need to be cognizant of the environmental impacts.")
            
            # 列出单词
            words = list_words(5)
            print("测试单词列表:")
            for w in words:
                print(f"- {w['word']}: {w['definition']}")
        
        elif sys.argv[1] == "wechat":
            # 模拟微信命令处理
            if len(sys.argv) > 2:
                command = " ".join(sys.argv[2:])
                response = process_wechat_command(command)
                print(response)
            else:
                print("请提供微信命令")
    else:
        print("使用方式:")
        print("  python word_manager.py test - 测试功能")
        print("  python word_manager.py wechat <命令> - 处理微信命令")