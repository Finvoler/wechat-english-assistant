# 微信与本地词库使用说明

## 微信操作

### 进入/退出模块

- 进入: 发送 英语 或 english 或 学习
- 退出: 发送 退出英语

### 加词（你要的极简方式）

进入模块后，直接发送一个英文单词即可自动入库。

示例：
resilient

### 常用命令

- !eng list: 查看最近单词
- !eng status: 查看学习状态
- !eng sync: 从本地 txt 批量导入
- !eng help: 查看帮助

### 立即要文章（新增）

进入学习模块后，发送以下任一关键词可立即生成并返回文章：

- 文章
- 今日文章
- 来篇文章

### 对话改写模式

- 开启: chat on
- 关闭: chat off

开启后你发英文句子，系统会按“你想说的是...对吧”进行改写升级并继续引导对话。

## 电脑 txt 批量导词

编辑文件：
- vocabulary_input.txt

支持格式：
1) word
2) word|definition
3) word|definition|example

示例：
sustainable|able to be maintained at a certain level
paradigm|a typical example or pattern
cognizant|aware or having knowledge|Students should be cognizant of context.

导入方式：
1) 微信发 !eng sync
2) 或本地运行 python sync_words_file.py
3) 每天文章任务开始前也会自动同步该文件

## 每日文章

- 你的 OpenClaw cron 任务会在每天 9 点触发文章生成
- 文章会写入数据库 articles 表，并在 output 目录落盘 JSON
- 你也可以随时在微信中发“文章”立即拿到一篇新文章
