#!/usr/bin/env python3
"""
微信消息处理器
处理来自微信的英语学习命令
"""
import os
import sys
import json
import asyncio
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from word_manager import process_wechat_command, add_word_from_plain_text
from conversation_manager import set_chat_mode, is_chat_mode_enabled, rewrite_sentence
from generate_daily_article import generate_daily_article

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


def _run_generate_daily_article_sync():
    """在同步上下文中安全执行异步文章生成。"""
    try:
        return asyncio.run(generate_daily_article())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(generate_daily_article())
        finally:
            loop.close()
            asyncio.set_event_loop(None)


def _format_article_message(result):
    if not result:
        return "❌ 文章生成失败，请稍后再试。"

    message = f"📚 今日英语文章\n\n标题: {result.get('title', 'Untitled')}\n\n"
    message += result.get("content", "")

    questions = result.get("questions", [])
    if questions:
        message += "\n\n📝 阅读题:"
        for idx, q in enumerate(questions[:3], 1):
            question_text = q if isinstance(q, str) else (q.get("question_text") or q.get("question", ""))
            message += f"\n{idx}. {question_text}"

    words = result.get("word_list", [])
    if words:
        message += f"\n\n🎯 生词覆盖: {', '.join(words[:8])}"

    return message

class WechatHandler:
    """微信消息处理器"""
    
    def __init__(self):
        self.command_prefix = "!eng"
        self.help_text = f"""
📖 英语学习系统 (使用 {self.command_prefix} 前缀)

命令格式: {self.command_prefix} <命令> [参数]

可用命令:
- {self.command_prefix} add <单词> [定义] [例句] - 添加新单词
- 直接发送单个英文单词 - 快速添加单词
- 发送 文章 / 今日文章 / 来篇文章 - 立即生成并返回文章
- {self.command_prefix} list - 列出最近添加的单词
- {self.command_prefix} sync - 从本地词库文件批量导入
- {self.command_prefix} status - 查看学习状态
- chat on / chat off - 开关对话改写模式
- {self.command_prefix} help - 显示帮助信息
- 发送 英语 / english - 进入学习模块
- 发送 退出英语 - 退出学习模块

示例:
  {self.command_prefix} add paradigm "a typical example" "This is a new paradigm"
  {self.command_prefix} list
"""
    
    def process_message(self, message_text):
        """处理微信消息"""
        message_text = message_text.strip()
        lowered = message_text.lower()

        article_keywords = ["文章", "今日文章", "来篇文章", "generate article", "daily article"]
        if is_learning_mode_enabled() and (message_text in article_keywords or lowered in article_keywords):
            result = _run_generate_daily_article_sync()
            return _format_article_message(result)

        if lowered in ["chat on", "!eng chat on"]:
            set_chat_mode(True)
            return "🗣️ 对话改写模式已开启，直接发送英文句子即可。"

        if lowered in ["chat off", "!eng chat off"]:
            set_chat_mode(False)
            return "✅ 对话改写模式已关闭。"

        quick_add = add_word_from_plain_text(message_text) if is_learning_mode_enabled() else None
        if quick_add:
            return quick_add

        if is_chat_mode_enabled() and not message_text.startswith(self.command_prefix):
            return rewrite_sentence(message_text)
        
        # 检查是否是英语学习命令
        if not message_text.startswith(self.command_prefix):
            return None
        
        # 提取命令部分
        command_text = message_text[len(self.command_prefix):].strip()
        
        if not command_text:
            return self.help_text
        
        # 处理命令
        return process_wechat_command(command_text)
    
    def generate_welcome_message(self):
        """生成欢迎消息"""
        welcome = "🎯 英语学习系统已激活！\n\n"
        welcome += self.help_text
        welcome += "\n💡 提示: 每天9点会自动生成包含您生词的文章"
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