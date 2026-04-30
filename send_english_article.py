#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日英语文章生成和发送脚本
通过OpenClaw微信插件发送消息
"""

import subprocess
import json
import os
import sys
import tempfile
from pathlib import Path

def run_python_script():
    """执行Python脚本生成英语文章"""
    python_exe = r"C:\Users\xu189\.openclaw\workspace\english_learning\.venv\Scripts\python.exe"
    script_path = r"C:\Users\xu189\.openclaw\workspace\english_learning\generate_daily_article.py"
    
    try:
        print(f"执行脚本: {script_path}")
        
        # 执行Python脚本并捕获输出
        result = subprocess.run(
            [python_exe, script_path],
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=120  # 2分钟超时
        )
        
        # 检查返回码
        if result.returncode == 0:
            output = result.stdout
            print(f"脚本执行成功，输出长度: {len(output)} 字符")
            
            # 检查是否包含【每日英语文章】
            if "【每日英语文章】" in output:
                print("✓ 成功生成了英语文章")
                return output
            else:
                print("⚠ 警告：输出中未找到【每日英语文章】标记")
                return output
        else:
            print(f"✗ 脚本执行失败，返回码: {result.returncode}")
            print(f"标准错误: {result.stderr[:500]}")
            return None
            
    except subprocess.TimeoutExpired:
        print("✗ 脚本执行超时（超过2分钟）")
        return None
    except Exception as e:
        print(f"✗ 执行脚本时出错: {e}")
        return None

def create_wechat_message(article_content):
    """创建微信消息"""
    # 确保内容不为空
    if not article_content:
        return "【每日英语学习提醒】\n\n今日英语文章生成失败，请手动检查。"
    
    # 如果内容太长，截取前4000字符（微信消息长度限制）
    if len(article_content) > 4000:
        article_content = article_content[:4000] + "\n\n【内容过长，已截断】"
    
    return article_content

def main():
    print("=" * 50)
    print("每日英语文章生成和发送任务")
    print("=" * 50)
    
    # 执行Python脚本生成文章
    article_content = run_python_script()
    
    if article_content:
        # 创建微信消息
        wechat_message = create_wechat_message(article_content)
        
        # 创建临时文件保存消息内容
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.txt', delete=False) as f:
            f.write(wechat_message)
            temp_file = f.name
        
        print(f"消息已保存到临时文件: {temp_file}")
        print("=" * 50)
        print("任务完成！")
        print("=" * 50)
        
        # 返回临时文件路径，供OpenClaw Cron使用
        print(f"TEMP_FILE:{temp_file}")
    else:
        print("✗ 任务失败：未能生成英语文章")
        sys.exit(1)

if __name__ == "__main__":
    main()