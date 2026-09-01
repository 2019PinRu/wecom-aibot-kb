# wecom-aibot-kb

企业微信智能机器人本地知识库应答系统：群聊 @ 触发 + 多消息聚合，单聊直接回复，SQLite FTS5 全文检索，纯本地部署，Web 可视化管理。

## 核心特性

- **群聊 @ 触发**：群里 @ 机器人（或指定人）触发回复，同一用户 30 秒内连续消息自动聚合为完整问题。
- **单聊直答**：单聊发消息即检索回复，无需 @。
- **纯本地检索**：默认不调用大模型，SQLite FTS5 + 中文 bigram 切分实现关键词全文检索。
- **无结果闭环**：检索不到时不转人工，写入待补充问题表并返回温和话术。
- **可视化运维**：Web 界面管理知识库、处理待补充问题、配置系统参数。

## 技术栈

Python 3.11+ / FastAPI / SQLite (FTS5) / Jinja2 + Bootstrap 5

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 准备配置
cp config.yaml.example config.yaml
# 编辑 config.yaml，或用环境变量注入密钥
export WECOM_BOT_ID="你的BotID"
export WECOM_BOT_SECRET="你的Secret"

# 3. 启动（需在 src 目录下）
cd src
uvicorn main:app --host 0.0.0.0 --port 8080
```

启动后浏览器打开 `http://localhost:8080/` 访问管理界面。

## 文档

| 文档 | 说明 |
| ---- | ---- |
| [docs/00-开发文档.md](docs/00-开发文档.md) | 整体开发文档：背景、目标、架构、里程碑 |
| [docs/02-部署文档.md](docs/02-部署文档.md) | 环境要求、安装、配置、企微配置、启动、故障排查 |
| [docs/03-系统操作指南.md](docs/03-系统操作指南.md) | 面向管理员：配置详解、知识库、待补充问题、备份升级 |
| [docs/04-使用说明.md](docs/04-使用说明.md) | 面向终端用户：如何 @ 机器人提问、回复格式、FAQ |
| [docs/05-知识库内容补充方案与操作指南.md](docs/05-知识库内容补充方案与操作指南.md) | 知识库内容补充：手动新增/编辑、CSV 批量导入、同步持久化 |
| [docs/06-Skill文档入库方案.md](docs/06-Skill文档入库方案.md) | SKILL.md 技能文档：frontmatter 解析、元数据入库 |
| [docs/07-Skill文档解析逻辑验证报告.md](docs/07-Skill文档解析逻辑验证报告.md) | SKILL.md 解析逻辑验证：真实样本格式调整 + 端到端检索命中 |
| [docs/08-Skill文档编写规范.md](docs/08-Skill文档编写规范.md) | SKILL.md 编写规范：供 agent 按要求生成系统可解析的技能文档 |
| `docs/01-实现思路-阶段1~7-*.md` | 各阶段实现思路（阶段 1~7） |

## 目录结构

```
wecom-aibot-kb/
├── src/
│   ├── main.py          # FastAPI 入口
│   ├── wecom/           # 企微接入（长连接、分流、回复）
│   ├── aggregator/      # 群聊多消息聚合引擎
│   ├── retriever/       # FTS5 检索与回复格式化
│   ├── kb/              # Git MD 文档同步与切分入库
│   ├── models/          # SQLite 表定义
│   ├── web/             # Web 路由与模板
│   └── utils/           # 配置加载
├── config.yaml.example  # 配置模板（不含密钥）
├── docs/                # 项目文档
└── tests/               # 单元测试
```

## 企业微信智能机器人回调字段要点

系统基于企微官方回调字段实现（以官方文档为准 https://developer.work.weixin.qq.com/document/62155）：

- 长连接回调 `aibot_msg_callback`，明文无需解密
- `msgid` 做消息排重；`chattype`（single/group）分流；单用户与机器人同时在途消息最多 3 条（队列控制）

## 开发规范

详见 [AGENTS.md](AGENTS.md)：中文注释、参数化 SQL、单函数 ≤ 50 行、原子提交、仅 main 分支、禁止 push。