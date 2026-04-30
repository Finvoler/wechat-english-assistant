#!/usr/bin/env python3
"""
微信消息处理器
处理来自微信的英语学习命令
"""
import os
import sys
import json
import asyncio
import re
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from word_manager import process_wechat_command, add_word_from_plain_text
from conversation_manager import set_chat_mode, is_chat_mode_enabled, rewrite_sentence
from generate_daily_article import generate_daily_article
from services import sm2_scheduler, writing_coach, speaking_coach, quiz_engine, progress_report, reading_coach, listening_coach


def _dispatch_advanced_command(command_text: str):
    """拦截 SM-2 / 写作 / 口语 / 测验 / 周报 等新命令，未命中返回 None。"""
    if not command_text:
        return None

    parts = command_text.split(None, 1)
    head = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if head == "review":
        action = rest.lower().strip()
        if not action:
            return sm2_scheduler.start_review_session()
        if action == "skip":
            return sm2_scheduler.skip_current()
        if action in {"stop", "exit", "end"}:
            return sm2_scheduler.stop_review()
        if action == "init":
            count = sm2_scheduler.initialize_existing_words()
            return f"✅ 已把 {count} 个旧词标记为今日到期。发送 !eng review 开始。"
        return "⚠️ 用法: !eng review / review skip / review stop / review init"

    if head in sm2_scheduler.GRADE_ALIASES:
        return sm2_scheduler.grade_current(head)

    if head == "grade":
        return sm2_scheduler.grade_current(rest)

    if head == "essay":
        return writing_coach.handle_essay_command(rest)

    if head == "speak":
        return speaking_coach.handle_speaking_command(rest)

    if head == "listen":
        return listening_coach.handle_listening_command(rest)

    if head == "read":
        return reading_coach.handle_read_command(rest)

    if head == "article":
        topic_hint = reading_coach.normalize_topic_hint(rest)
        result = _run_generate_daily_article_sync(topic_hint)
        return _format_article_message(result)

    if head == "quiz":
        lowered = rest.lower()
        if not rest or lowered in {"new", "next"}:
            return quiz_engine.generate_quiz()
        if lowered.startswith("answer"):
            return quiz_engine.answer_quiz(lowered.replace("answer", "", 1).strip())
        if rest.strip().upper() in {"A", "B", "C", "D"}:
            return quiz_engine.answer_quiz(rest)
        return "⚠️ 用法: !eng quiz / !eng quiz answer <A-D>"

    if head == "report":
        days = 7
        if rest.isdigit():
            days = max(1, min(30, int(rest)))
        return progress_report.generate_report(days)

    return None

learning_state_file = project_root / "data" / "learning_state.json"


def set_learning_mode(enabled: bool) -> None:
    learning_state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(learning_state_file, "w", encoding="utf-8") as f:
        json.dump({"learning_mode": enabled}, f, ensure_ascii=False, indent=2)


def is_learning_mode_enabled() -> bool:
    if not learning_state_file.exists():
        return False
    try:
        with open(learning_state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("learning_mode", False))
    except Exception:
        return False


def _run_generate_daily_article_sync(topic_hint: str | None = None):
    """在同步上下文中安全执行异步文章生成。"""
    try:
        return asyncio.run(generate_daily_article(topic_hint=topic_hint))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(generate_daily_article(topic_hint=topic_hint))
        finally:
            loop.close()
            asyncio.set_event_loop(None)


def _format_article_message(result):
    if not result:
        return "❌ 文章生成失败，请稍后再试。"

    message = f"📚 今日英语文章\n\n标题: {result.get('title', 'Untitled')}\n\n"
    message += result.get("content", "")

    source = result.get("source")
    if source:
        message += f"\n\n📎 生成模式: {source}"

    topic_hint = result.get("topic_hint")
    if topic_hint:
        message += f"\n\n🏷️ 话题: {reading_coach.topic_hint_display(topic_hint)}"

    embedded_words = result.get("embedded_words") or []
    words = embedded_words or result.get("word_list", [])
    if words:
        total_words = len(result.get("word_list", [])) or len(words)
        message += f"\n\n🎯 生词覆盖 ({len(words)}/{total_words}): {', '.join(words[:24])}"

    message += "\n\n📘 如需托福阅读题，请发送: !eng read quiz [题量]"

    return message


def _parse_article_request(message_text: str):
    text = (message_text or "").strip()
    if not text:
        return False, None

    direct_cn = ["文章", "今日文章", "来篇文章"]
    direct_en = ["article", "daily article", "generate article"]

    lowered = text.lower()
    if text in direct_cn or lowered in direct_en:
        return True, None

    for prefix in direct_cn:
        if text.startswith(prefix + " "):
            tail = text[len(prefix) :].strip()
            topic_hint = reading_coach.normalize_topic_hint(tail)
            return True, topic_hint
        if text.startswith(prefix) and len(text) > len(prefix):
            tail = text[len(prefix) :].strip()
            topic_hint = reading_coach.normalize_topic_hint(tail)
            if topic_hint:
                return True, topic_hint

    for prefix in direct_en:
        if lowered.startswith(prefix + " "):
            tail = lowered[len(prefix) :].strip()
            topic_hint = reading_coach.normalize_topic_hint(tail)
            return True, topic_hint

    return False, None


def _chat_mode_command(message_text: str):
    normalized = re.sub(r"[^a-z\u4e00-\u9fff]", "", message_text.strip().lower())
    on_commands = {
        "chaton", "engchaton", "startchat", "enablechat", "打开聊天", "开启聊天",
    }
    off_commands = {
        "chatoff", "engchatoff", "stopchat", "exitchat", "disablechat", "关闭聊天", "退出聊天",
    }
    if normalized in on_commands:
        return True
    if normalized in off_commands:
        return False
    return None

class WechatHandler:
    """微信消息处理器"""
    
    def __init__(self):
        self.command_prefix = "!eng"
        self.help_text = f"""
📖 英语学习系统 (使用 {self.command_prefix} 前缀)

词汇管理:
- {self.command_prefix} add <单词> [定义] [例句]
- 直接发送单个英文单词 - 快速加词
- {self.command_prefix} list - 最近加入的词
- {self.command_prefix} sync - 从本地词库批量导入
- {self.command_prefix} status - 学习进度统计

SM-2 间隔重复复习:
- {self.command_prefix} review - 抽取今日到期单词并开始复习
- good / easy / again - 直接评分当前卡片
- {self.command_prefix} review skip / stop - 跳过或结束
- {self.command_prefix} review init - 首次启用时把旧词标记为今日到期

TOEFL 写作教练:
- {self.command_prefix} essay types - 查看支持的所有题型
- {self.command_prefix} essay prompt <题型> - 生成该题型例卷风格题目
- {self.command_prefix} essay submit <英文答案> - 按最近题目评分
- {self.command_prefix} essay sample [题型] [保守版|平衡版|激进版] [标准模式|词库强化] - 生成带讲解和diff的范文
- {self.command_prefix} essay <题型> <英文答案> - 直接按指定题型评分
- {self.command_prefix} essay <英文答案> - 默认按最近题目评分

TOEFL 口语教练:
- {self.command_prefix} speak types - 查看 2026 口语题型
- {self.command_prefix} speak prompt <listen_repeat|take_interview> [生物|人文|科技|校园|政策...] - 生成口语题目
- {self.command_prefix} speak submit <你的英文作答> - 评分 + 标准答案 + 高分关键 + diff
- {self.command_prefix} speak sample [题型] [保守版|平衡版|激进版] [标准模式|词库强化] - 生成讲解版口语范例

TOEFL 听力教练:
- {self.command_prefix} listen types - 查看 2026 听力题型与题量模板
- {self.command_prefix} listen prompt <choose_response|conversation|announcement|academic_talk|section> [生物|科技|校园|政策|人文|心理|环境...]
- {self.command_prefix} listen submit <全部答案> - 一次性提交并评分（例如 1A 2B... 或 ABCD...）
- {self.command_prefix} listen explain <题号> - 查看错题对应证据与解析

自适应词义小测:
- {self.command_prefix} quiz - 针对弱项词出 TOEFL 选择题
- {self.command_prefix} quiz answer <A-D> - 提交答案

每周 AI 学习报告:
- {self.command_prefix} report [天数]

文章与对话:
- 发送 文章 [生物|科技|心理|环境|考古|人文...] - 按话题生成带生词的 TOEFL 文章
- {self.command_prefix} article [biology|technology|psychology|environment|archaeology|humanities] - 英文话题生成文章
- 文章生成后可继续出题: {self.command_prefix} read quiz [题量]
- {self.command_prefix} read quiz [题量] - 基于最近文章生成 TOEFL 阅读题
- {self.command_prefix} read answer <全部答案> - 一次性提交整卷答案并评分
- {self.command_prefix} read explain <题号> - 查看单题解析与原文定位
- {self.command_prefix} read help - 查看阅读题完整用法
- chat on / chat off - 开关对话改写模式
- 发送 英语 / english - 进入学习模块
- 发送 退出英语 - 退出学习模块
"""
    
    def process_message(self, message_text):
        """处理微信消息"""
        message_text = message_text.strip()
        lowered = message_text.lower()

        is_article_request, topic_hint = _parse_article_request(message_text)
        if is_learning_mode_enabled() and is_article_request:
            result = _run_generate_daily_article_sync(topic_hint)
            return _format_article_message(result)

        chat_command = _chat_mode_command(message_text)
        if chat_command is True:
            set_chat_mode(True)
            return "Chat mode is on. Send me an English sentence, and I will reply in English with 'Do you mean: ...' plus a follow-up question."

        if chat_command is False:
            set_chat_mode(False)
            return "Chat mode is off."

        if is_chat_mode_enabled() and not message_text.startswith(self.command_prefix):
            return rewrite_sentence(message_text)

        quick_add = add_word_from_plain_text(message_text) if is_learning_mode_enabled() else None
        if quick_add:
            return quick_add
        
        # 检查是否是英语学习命令
        if not message_text.startswith(self.command_prefix):
            return None
        
        # 提取命令部分
        command_text = message_text[len(self.command_prefix):].strip()
        
        if not command_text:
            return self.help_text

        advanced = _dispatch_advanced_command(command_text)
        if advanced is not None:
            return advanced

        # 处理命令
        return process_wechat_command(command_text)
    
    def generate_welcome_message(self):
        """生成欢迎消息"""
        welcome = "🎯 英语学习系统已激活！\n\n"
        welcome += self.help_text
        return welcome

def handle_wechat_message(message):
    """处理微信消息的入口函数"""
    handler = WechatHandler()
    msg = message.strip().lower()

    if msg in ["退出英语", "exit english", "stop english"]:
        set_learning_mode(False)
        set_chat_mode(False)
        return "👋 已退出英语学习模块。"
    
    if msg in ["英语", "english", "学习"]:
        set_learning_mode(True)
        return handler.generate_welcome_message()
    
    response = handler.process_message(message)
    
    if response is None:
        return None  # 不是英语学习命令，不响应
    
    return response

if __name__ == "__main__":
    # 测试模式
    if len(sys.argv) > 1:
        test_message = " ".join(sys.argv[1:])
        
        if test_message == "test":
            # 运行测试
            test_cases = [
                "!eng add cognitive related to thinking",
                "!eng list",
                "!eng help",
                "!eng status",
                "英语"
            ]
            
            for test in test_cases:
                print(f"\n测试: {test}")
                print("结果:", handle_wechat_message(test))
        else:
            # 测试特定消息
            result = handle_wechat_message(test_message)
            if result:
                print("处理结果:", result)
            else:
                print("不是英语学习命令")
    else:
        print("使用方式:")
        print("  python wechat_handler.py test - 运行测试")
        print("  python wechat_handler.py <消息> - 处理特定消息")