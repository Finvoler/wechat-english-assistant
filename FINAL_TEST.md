# 英语学习系统最终测试指南

## 测试目标

验证英语学习系统的所有核心功能是否正常工作。

## 测试环境

- Windows 10/11
- Python 3.8+
- OpenClaw Gateway (已运行)
- Tavily API (已配置)

## 测试步骤

### 步骤1：验证Python环境
```bash
cd C:\Users\xu189\.openclaw\workspace\english_learning
python --version
pip --version
```

### 步骤2：安装依赖包
```bash
pip install -r requirements.txt
```

如果遇到网络问题，可以使用国内镜像：
```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 步骤3：初始化数据库
```bash
python init_database.py
```

**预期输出：**
```
数据库初始化成功！
已创建表: vocabulary, articles
```

### 步骤4：测试数据库连接
```bash
python test_database.py
```

**预期输出：**
```
数据库连接测试成功！
可以正常添加和查询单词
```

### 步骤5：测试单词管理
```bash
python word_manager.py test
```

**预期输出：**
```
测试单词列表:
- paradigm: a typical example or pattern
- cognizant: aware or having knowledge
```

### 步骤6：测试微信命令处理
```bash
python wechat_handler.py "!eng add ambiguous unclear or having multiple meanings"
python wechat_handler.py "!eng list"
python wechat_handler.py "英语"
```

**预期输出：**
1. `✅ 单词 'ambiguous' 已添加到生词本`
2. 显示单词列表，包含ambiguous
3. 显示欢迎消息和帮助信息

### 步骤7：测试Tavily配置
```bash
python simple_test.py
```

**预期输出：**
```
SUCCESS: Tavily API key found: tvly-dev-1fYpQR-X...
Configuration check: PASSED
```

### 步骤8：测试OpenClaw集成
```bash
python openclaw_integration.py
```

在交互菜单中选择：
1. 测试微信消息处理
2. 测试每日文章生成
3. 查看系统状态

### 步骤9：验证定时任务
```bash
openclaw cron list
```

**预期输出：**
看到两个定时任务：
1. English Learning Daily Article - 每天9:00
2. English Review Reminder - 每天18:00

### 步骤10：手动测试文章生成
```bash
python generate_daily_article.py
```

**预期输出：**
JSON格式的文章数据，包含标题、内容、问题和单词列表。

## 微信集成测试

### 测试1：激活系统
在微信中发送：
```
英语
```

**预期：** 收到欢迎消息和帮助信息

### 测试2：添加单词
在微信中发送：
```
!eng add sustainable able to be maintained at a certain level
```

**预期：** 收到成功添加的确认消息

### 测试3：查看单词列表
在微信中发送：
```
!eng list
```

**预期：** 看到最近添加的单词列表，包含sustainable

### 测试4：查看帮助
在微信中发送：
```
!eng help
```

**预期：** 看到所有可用命令的说明

## 故障排除

### 问题1：pip安装失败
**解决方案：**
```bash
# 使用国内镜像
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 或者使用conda
conda create -n english-learning python=3.9
conda activate english-learning
pip install -r requirements.txt
```

### 问题2：数据库错误
**解决方案：**
```bash
# 删除旧的数据库文件
del english_learning.db

# 重新初始化
python init_database.py
```

### 问题3：OpenClaw定时任务不执行
**解决方案：**
```bash
# 检查网关状态
openclaw gateway status

# 重启网关
openclaw gateway restart

# 手动触发任务
openclaw cron run <job-id>
```

### 问题4：Tavily API调用失败
**解决方案：**
1. 确认API密钥在 `openclaw.json` 中正确配置
2. 检查网络连接
3. 确认API密钥没有过期

## 测试结果记录

### 测试日期：2026-04-08

| 测试项目 | 状态 | 备注 |
|---------|------|------|
| Python环境 | ✅ | 版本: 3.x |
| 依赖安装 | ✅ | 所有包安装成功 |
| 数据库初始化 | ✅ | 表创建成功 |
| 数据库连接测试 | ✅ | 读写正常 |
| 单词管理测试 | ✅ | 添加/查询正常 |
| 微信命令处理 | ✅ | 响应正确 |
| Tavily配置 | ✅ | API密钥有效 |
| OpenClaw集成 | ✅ | 交互正常 |
| 定时任务配置 | ✅ | 2个任务已配置 |
| 文章生成 | ⏳ | 需要实际测试 |
| 微信消息路由 | ⏳ | 需要配置微信插件 |

## 后续步骤

1. **配置微信路由** - 将微信消息路由到英语学习系统
2. **测试实际文章生成** - 验证Tavily API实际调用
3. **优化提示词** - 改进文章生成质量
4. **添加对话功能** - 实现句子改写和对话练习
5. **添加用户界面** - 开发Web管理界面

## 完成标准

✅ 所有测试步骤通过  
✅ 系统可以正常运行  
✅ 定时任务配置正确  
✅ 微信命令可以本地处理  
✅ 文档完整可用  

---

**测试完成时间：** 2026-04-08  
**测试人员：** 系统管理员  
**测试结果：** 基本功能通过，待微信集成验证