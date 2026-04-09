#!/usr/bin/env python3
"""
OpenClaw集成脚本
将英语学习系统与OpenClaw微信机器人集成
"""
import os
import sys
import json
import asyncio
from pathlib import Path
import logging

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from wechat_handler import handle_wechat_message
from generate_daily_article import generate_daily_article

class EnglishLearningIntegration:
    """英语学习系统集成"""
    
    def __init__(self):
        self.wechat_handler = None
        self.initialized = False
        
    async def initialize(self):
        """初始化系统"""
        try:
            # 检查数据库连接
            from database import SessionLocal, init_db
            from models import Base
            
            engine = SessionLocal().get_bind()
            Base.metadata.create_all(bind=engine)
            
            logger.info("数据库初始化完成")
            
            # 导入微信处理器
            from wechat_handler import handle_wechat_message
            self.handle_wechat_message = handle_wechat_message
            
            self.initialized = True
            logger.info("英语学习系统初始化完成")
            
            return True
            
        except Exception as e:
            logger.error(f"初始化失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def process_wechat_message(self, message_text):
        """处理微信消息"""
        if not self.initialized:
            success = await self.initialize()
            if not success:
                return "系统初始化失败，请稍后再试"
        
        try:
            response = handle_wechat_message(message_text)
            return response
        except Exception as e:
            logger.error(f"处理消息时出错: {e}")
            return f"处理消息时出错: {str(e)}"
    
    async def generate_daily_content(self):
        """生成每日内容"""
        if not self.initialized:
            success = await self.initialize()
            if not success:
                return None
        
        try:
            result = await generate_daily_article()
            
            if result:
                # 格式化消息
                message = f"📚 今日英语学习文章\n\n"
                message += f"**标题:** {result['title']}\n\n"
                message += f"**内容:**\n{result['content'][:500]}...\n\n"
                
                if result.get('questions'):
                    message += f"**阅读题:**\n"
                    for i, q in enumerate(result['questions'][:3], 1):
                        message += f"{i}. {q['question']}\n"
                
                message += f"\n💡 使用的生词: {', '.join(result['word_list'][:5])}"
                
                return message
            else:
                return "今日文章生成失败，请稍后再试"
                
        except Exception as e:
            logger.error(f"生成内容时出错: {e}")
            return None
    
    def get_system_status(self):
        """获取系统状态"""
        if not self.initialized:
            return "系统未初始化"
        
        try:
            from database import SessionLocal
            from models import Vocabulary, Article
            
            db = SessionLocal()
            
            try:
                # 统计信息
                word_count = db.query(Vocabulary).count()
                article_count = db.query(Article).count()
                
                status = {
                    "status": "运行中",
                    "word_count": word_count,
                    "article_count": article_count,
                    "initialized": self.initialized
                }
                
                return json.dumps(status, indent=2, ensure_ascii=False)
                
            finally:
                db.close()
                
        except Exception as e:
            return f"获取状态失败: {str(e)}"

# 全局实例
integration = EnglishLearningIntegration()

async def main():
    """主函数"""
    print("英语学习系统 - OpenClaw集成")
    print("=" * 50)
    
    # 初始化
    print("正在初始化系统...")
    success = await integration.initialize()
    
    if not success:
        print("初始化失败")
        return
    
    print("系统初始化成功")
    
    # 测试功能
    print("\n测试功能:")
    print("1. 测试微信消息处理")
    print("2. 测试每日文章生成")
    print("3. 查看系统状态")
    print("4. 退出")
    
    while True:
        choice = input("\n请选择 (1-4): ").strip()
        
        if choice == "1":
            message = input("输入微信消息: ").strip()
            response = await integration.process_wechat_message(message)
            print(f"\n响应: {response}")
        
        elif choice == "2":
            print("生成每日文章...")
            result = await integration.generate_daily_content()
            print(f"\n结果: {result}")
        
        elif choice == "3":
            status = integration.get_system_status()
            print(f"\n系统状态:\n{status}")
        
        elif choice == "4":
            print("退出")
            break
        
        else:
            print("无效选择")

if __name__ == "__main__":
    asyncio.run(main())