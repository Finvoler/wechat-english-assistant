# 📚 WeChat English Learning Assistant (微信英语学习专属系统)

基于大语言模型（LLM）的私有化微信英语学习系统，专为个人学习打造，可以作为你微信上的24小时无情英语老师。

## ✨ 核心功能
- **随时随地背单词**：通过微信给机器人发生词，一键自动保存并抓取中英双语的定义、地道例句。
- **根据生词定制长篇阅读**：通过发送关键词“文章”或每天定时推送，调用 AI 模型基于你刚学的生词量身定做一篇全英文的高质量文章，将生单词埋入语境中，还会自动生成三道阅读理解题。
- **对话改写增强**：随时向机器人输入英文并开启改写模式（Chat on/off），老师会自动帮你纠正发音弱点和中式英语。
- **完美融合微信**：结合 OpenClaw 平台，消息不漏接、不延时。

## 🛠 配置说明

### 1. 安装依赖
\\\ash
pip install -r requirements.txt
\\\

### 2. 准备私人 API Keys
本系统依赖外部大语言模型。请将本目录下的 \llm_config.example.json\ 改名为 \llm_config.json\ 并填入您专属的 API 配置。
\\\json
{
  "base_url": "https://api.deepseek.com/v1/chat/completions",
  "api_key": "sk-你的API-KEY",
  "model": "deepseek-chat"
}
\\\

### 3. OpenClaw WeChat 整合（推荐）
系统专门编写了 \wechat_gateway.py\ 网关脚本。您只需在本地运行 OpenClaw 即可将微信的任意消息通过代理输入给机器人的逻辑引擎。
(使用 \openclaw agents bind\ 命令指向该应用的守护目录即可)

## 📖 微信命令一览
在微信聊天框可以直接发送以下指令：
- \!eng add <单词> [定义] [例句]\ - 添加新单词
- \文章 / 今日文章 / 来篇文章\ - 立即生成包含近期生词的文章阅读！
- \!eng list\ - 列出最近添加的单词
- \!eng sync\ - 从本地 txt 词库文件批量导入
- \!eng status\ - 查看学习状态
- \chat on / chat off\ - 开关对话改写模式
- \!eng help\ - 显示帮助信息

## 💡 License
MIT License.
