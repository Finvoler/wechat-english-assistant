# LLM 接入说明（SJTU Models）

## 1. 你的参数

- API Base URL: https://models.sjtu.edu.cn/api/v1/chat/completions
- API Key: 你提供的 key
- 可用模型:
  - deepseek-chat / deepseek-v3.2
  - minimax / minimax-m2.5
  - qwen3coder
  - qwen3vl

## 2. URL 要不要去掉后缀

本项目现在已自动兼容两种写法：

- https://models.sjtu.edu.cn/api/v1
- https://models.sjtu.edu.cn/api/v1/chat/completions

你写哪一种都可以，代码会自动规范化。

## 3. 推荐配置方式

优先使用 OpenClaw 的主配置文件：
- C:/Users/xu189/.openclaw/openclaw.json

因为项目已支持从该文件自动读取 provider 的 baseUrl/apiKey/model，通常不用再单独设置环境变量。

## 4. 若你想手工指定模型

在 PowerShell 中设置（当前终端有效）：

$env:LLM_BASE_URL="https://models.sjtu.edu.cn/api/v1"
$env:LLM_API_KEY="<你的key>"
$env:LLM_MODEL="deepseek-chat"

然后运行：

python generate_daily_article.py

## 5. 可选模型切换示例

- DeepSeek: LLM_MODEL=deepseek-chat
- MiniMax: LLM_MODEL=minimax
- Qwen3Coder: LLM_MODEL=qwen3coder
- Qwen3VL: LLM_MODEL=qwen3vl
