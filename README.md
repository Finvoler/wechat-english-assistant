# WeChat English Learning Assistant

一个基于 LLM 的微信英语学习助手，围绕词汇积累、阅读输入、写作反馈、口语训练和间隔重复复习设计，适合个人私有部署。

## Features

- Vocabulary capture: 通过微信直接添加单词，自动保存释义、例句和学习记录。
- Daily article generation: 基于近期词汇生成英文文章，并支持按主题提示定制内容。
- SM-2 review: 内置间隔重复复习流程，支持开始、评分、跳过和结束当前复习卡片。
- TOEFL-oriented coaching: 提供写作、口语、阅读、听力、测验和学习周报模块。
- Chat rewrite mode: 开启后可对英文表达做即时改写和纠偏。
- OpenClaw integration: 可通过 OpenClaw 接入微信消息流。

## Project Layout

- wechat_handler.py: 微信消息入口与命令分发。
- word_manager.py: 单词管理和基础学习指令。
- generate_daily_article.py: 每日文章生成。
- services/: 阅读、听力、写作、口语、测验、周报、SM-2 调度等增强模块。
- llm_config.example.json: LLM 配置示例。

## Setup

1. 安装依赖

```bash
pip install -r requirements.txt
```

2. 准备 LLM 配置

将 llm_config.example.json 复制为 llm_config.json，然后填写你自己的模型服务配置。

```json
{
  "base_url": "https://api.deepseek.com/v1/chat/completions",
  "api_key": "sk-your-api-key",
  "model": "deepseek-chat"
}
```

3. 初始化数据库

```bash
python init_database.py
```

4. 接入 OpenClaw

通过 wechat_gateway.py 将 OpenClaw 的微信消息转给本项目的处理逻辑即可。

## Commands

- !eng add <word> [definition] [example]
- !eng list
- !eng sync
- !eng status
- !eng review
- good / easy / again
- !eng essay ...
- !eng speak ...
- !eng read ...
- !eng listen ...
- !eng quiz
- !eng report [days]
- article / 今日文章 / 来篇文章
- chat on / chat off
- !eng help

## Privacy

仓库默认不包含以下本地隐私数据：

- 真实 llm_config.json
- 本地数据库和学习记录
- 生成文章缓存和输出目录
- 虚拟环境、缓存文件和本机运行产物

请仅在你自己的环境中填写 API key，并根据需要自行初始化学习数据。

## License

MIT License.
