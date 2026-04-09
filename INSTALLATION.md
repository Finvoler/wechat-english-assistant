# 英语学习系统安装指南

## 系统概述

这是一个基于OpenClaw的英语学习系统，具有以下功能：

1. **每日文章生成** - 每天9点自动生成包含您生词的文章
2. **微信集成** - 通过微信机器人管理单词和文章
3. **对话练习** - 使用生词进行对话练习（开发中）
4. **数据库管理** - 本地SQLite数据库存储所有数据

## 系统要求

- Python 3.8+
- OpenClaw Gateway (已安装并运行)
- Tavily API密钥 (已配置)

## 安装步骤

### 1. 安装依赖

```bash
cd C:\Users\xu189\.openclaw\workspace\english_learning
pip install -r requirements.txt
```

### 2. 初始化数据库

```bash
python init_database.py
```

### 3. 配置OpenClaw定时任务

定时任务已通过以下命令配置：
- 每日9点生成文章 (cron: 0 1 * * *)
- 每日18点复习提醒 (cron: 0 10 * * *)

查看定时任务状态：
```bash
openclaw cron list
```

### 4. 测试系统

```bash
# 测试数据库连接
python test_database.py

# 测试单词管理
python word_manager.py test

# 测试微信集成
python wechat_handler.py test

# 测试完整系统
python openclaw_integration.py
```

## 使用说明

### 通过微信添加单词

在微信中发送以下命令：

```
!eng add <单词> [定义] [例句]
```

示例：
```
!eng add paradigm "a typical example" "This is a new paradigm"
```

### 查看单词列表

```
!eng list
```

### 查看帮助

```
!eng help
```

### 激活系统

发送以下任意消息激活系统：
- 英语
- english
- 学习

## 系统架构

### 文件结构

```
english_learning/
├── database.py              # 数据库连接
├── models.py               # 数据库模型
├── init_database.py        # 数据库初始化
├── requirements.txt        # Python依赖
├── generate_daily_article.py  # 每日文章生成
├── word_manager.py         # 单词管理
├── wechat_handler.py       # 微信消息处理
├── openclaw_integration.py # OpenClaw集成
├── main.py                 # FastAPI服务（开发中）
├── run.ps1                 # PowerShell启动脚本
└── INSTALLATION.md         # 本文件
```

### 数据库表结构

1. **vocabulary** - 生词表
   - id, word, definition, example
   - review_count, created_at, last_reviewed
   - added_via (wechat/manual)

2. **articles** - 文章表
   - id, title, content, word_list
   - questions, difficulty_level, created_at

## 配置OpenClaw微信集成

### 方法1：通过OpenClaw命令处理

在OpenClaw中配置一个触发器，当收到微信消息时调用英语学习系统：

```javascript
// 在OpenClaw插件配置中添加
{
  "plugins": {
    "entries": {
      "english-learning": {
        "kind": "command",
        "config": {
          "command": "python C:\\Users\\xu189\\.openclaw\\workspace\\english_learning\\wechat_handler.py",
          "trigger": {
            "pattern": "^!eng"
          }
        }
      }
    }
  }
}
```

### 方法2：使用OpenClaw Gateway API

通过OpenClaw Gateway的Webhook接收微信消息，然后转发给英语学习系统。

## 故障排除

### 问题1：数据库连接失败

**症状**：无法添加单词或生成文章

**解决方案**：
1. 检查数据库文件是否存在：`english_learning.db`
2. 重新初始化数据库：`python init_database.py`

### 问题2：定时任务不执行

**症状**：每天9点没有收到文章

**解决方案**：
1. 检查OpenClaw Gateway状态：`openclaw gateway status`
2. 查看定时任务：`openclaw cron list`
3. 手动触发任务：`openclaw cron run <job-id>`

### 问题3：微信命令不响应

**症状**：发送`!eng`命令没有反应

**解决方案**：
1. 检查OpenClaw微信插件是否正常运行
2. 测试本地处理：`python wechat_handler.py "!eng list"`
3. 查看OpenClaw日志：`openclaw gateway logs`

### 问题4：文章生成失败

**症状**：文章生成返回空内容

**解决方案**：
1. 检查Tavily API配置是否正确
2. 检查网络连接
3. 检查是否有足够的单词可以生成文章

## 扩展功能

### 1. 添加对话练习模式

计划实现的功能：
- 使用生词进行情景对话
- 托福口语题模拟
- 实时语法纠正

### 2. 添加听力练习

计划实现的功能：
- 文章朗读（TTS）
- 听力理解题
- 听写练习

### 3. 添加进度跟踪

计划实现的功能：
- 学习进度统计
- 单词掌握程度分析
- 个性化推荐

## 技术支持

如有问题，请：
1. 查看OpenClaw日志：`openclaw gateway logs`
2. 检查系统状态：`python openclaw_integration.py` 然后选择选项3
3. 联系系统管理员

## 更新日志

### v1.0.0 (2026-04-08)
- 初始版本发布
- 基础单词管理
- 每日文章生成
- 微信集成
- OpenClaw定时任务支持