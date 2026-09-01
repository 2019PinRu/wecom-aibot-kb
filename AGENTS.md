# AGENTS.md — wecom-aibot-kb 项目规则

> 本文件为 TraeWork 项目级规则。所有 AI 生成代码、修改代码、重构代码都必须遵守以下规范。

## 一、项目定位

企业微信**智能机器人（AIBot）**本地知识库应答系统：
- 群聊：仅当用户@本机器人（或@配置的指定人）时触发；同一用户的多条连续消息按时间窗聚合为完整问题
- 单聊：用户发消息即视为新问题，直接检索回复
- 无结果时不转人工，记录到"待补充问题表"
- 纯本地部署，默认不调大模型，SQLite FTS5 全文检索即回复
- 提供 Web 可视化界面管理知识库、查看待补充问题、配置系统参数

## 二、企业微信智能机器人接口硬约束（必须严格遵守）

基于企微官方回调字段 ：
- 用户与智能机器人发生交互时，企业微信通过长连接推送消息回调 `aibot_msg_callback`
- 回调报文通用字段：`msgid`、`aibotid`、`chatid`（仅群聊）、`chattype`（`single`/`group`）、`from.userid`、`msgtype`、`response_url`
- `msgid`：本次回调的唯一性标志，**必须据此做消息排重**（可能因网络等原因重复回调）
- `chatid`：仅 `chattype=group` 时返回
- `chattype`：`single` 单聊 / `group` 群聊
- `from.userid`：消息发送者 userid；智能机器人创建者为超级管理员时为明文，否则为加密 userid
- `response_url`：用于被动回复
- 触发场景：群里@智能机器人，或单聊中向机器人发送文本消息
- **用户跟同一个智能机器人最多同时有 3 条消息交互中**，需做队列控制
- 群聊@强提醒：原生智能机器人默认不支持真正的@强提醒，回复开头加 "@提问人" 文本前缀作为变通

## 三、群聊多消息聚合规则（核心业务规则）

1. 仅当首条消息@本机器人（或@指定人）时，才开始聚合该 `chatid+userid` 的缓冲区
2. 同一 `userid` 在时间窗（默认 30 秒）内的连续消息拼接为完整问题
3. 时间窗内其他 `userid` 的发言**不中断**当前用户的聚合计时（仅忽略）
4. 时间窗到期后触发检索；若首条消息未@本机器人，整个缓冲丢弃
5. 基于 `msgid` 做消息排重，防止网络重试导致重复处理

## 四、技术栈与目录结构

技术栈：Python 3.11+ / FastAPI / SQLite (FTS5) / Jinja2 + Bootstrap 5

目录结构（必须遵循）：

wecom-aibot-kb/
├── AGENTS.md                 # 本规则文件
├── README.md                 # 项目说明
├── .gitignore
├── requirements.txt          # 仅 fastapi uvicorn httpx pyyaml jinja2
├── config.yaml.example       # 配置模板（不含密钥）
├── src/
│   ├── main.py               # FastAPI 入口
│   ├── wecom/                # 企微智能机器人接入层
│   │   ├── __init__.py
│   │   ├── callback.py       # 长连接回调接收 + 验签
│   │   ├── dispatcher.py     # chattype 分流
│   │   └── client.py         # 被动回复 / 主动推送
│   ├── aggregator/           # 群聊多消息聚合引擎
│   │   ├── __init__.py
│   │   └── buffer.py         # chatid+userid 时间窗缓冲
│   ├── retriever/            # 检索应答引擎
│   │   ├── __init__.py
│   │   ├── fts5.py           # SQLite FTS5 检索
│   │   └── responder.py      # 回复格式化 + 前缀拼接
│   ├── kb/                   # 知识入库流水线
│   │   ├── __init__.py
│   │   ├── git_sync.py       # Git MD 文档同步与切分
│   │   └── wechat_clean.py   # 微信聊天记录清洗入库（暂缓）
│   ├── models/               # 数据模型
│   │   ├── __init__.py
│   │   └── db.py             # SQLite 表定义与连接
│   ├── web/                  # Web UI
│   │   ├── __init__.py
│   │   ├── routes.py         # 路由
│   │   └── templates/        # Jinja2 模板
│   └── utils/
│       ├── __init__.py
│       └── config.py         # 配置加载
├── data/                     # SQLite 数据库文件存放（gitignore）
├── logs/                     # 日志（gitignore）
└── tests/                    # 单元测试


## 五、代码规范（Python）

### 1. 风格
- 遵循 PEP 8，单行 ≤ 100 字符
- 使用 `black` 格式化，`isort` 排序 import
- 缩进 4 空格，禁止 Tab
- 文件编码 UTF-8，文件头统一 `# -*- coding: utf-8 -*-`

### 2. 命名
- 变量/函数：snake_case
- 类：PascalCase
- 常量：UPPER_SNAKE_CASE
- 模块/文件：snake_case

### 3. 注释与文档
- 所有函数必须有一行中文 docstring，说明用途、参数、返回值
- 关键业务逻辑（聚合算法、检索阈值判定、排重）必须加简明中文注释
- 禁止英文注释，除非引用官方术语（如 `msgid`、`chattype`、`from.userid`）

### 4. 函数设计
- 单函数 ≤ 50 行，超过必须拆分
- 函数单一职责，禁止"上帝函数"
- 异步函数统一使用 `async/await`
- 禁止在函数内直接操作全局配置，通过参数传入

### 5. 错误处理
- 所有外部调用（企微 API、SQLite、Git）必须 try/except
- 禁止裸 `except:`，必须指定异常类型
- 关键错误写入 logs/ 目录，格式：`时间 | 级别 | 模块 | 消息`
- 禁止吞掉异常不记录

### 6. 配置管理
- 密钥（BotID、BotSecret）**禁止硬编码**，必须从环境变量或 `config.yaml` 读取
- `config.yaml` 必须提供 `.example` 版本入库，真实配置 gitignore
- 可配置项：回复前缀、无结果话术、@目标、聚合时间窗、FTS5 阈值

### 7. 数据库规范
- 所有 SQL 使用参数化查询，禁止字符串拼接防注入
- 表名 snake_case，字段 snake_case
- FTS5 虚拟表与业务表分离
- 迁移脚本放在 `src/models/migrations/`

## 六、Git 工作流规范（⚠️ 核心约束）

> **本仓库不使用分支、不使用 PR、不推送远程。所有改动直接在 `main` 分支提交。**

### 1. 分支策略
- 单一 `main` 分支，所有工作直接在 `main` 上进行
- **禁止**创建 feature/fix/refactor 等任何其他分支
- **禁止** `git push` 操作（包括 `git push origin main`）
- 只允许 `git add` + `git commit`

### 2. 原子提交
- **禁止一次性提交所有代码**。按功能/模块/业务逐步提交
- 每个 commit 只做一件事（一个功能点、一个 bug fix、一次重构）
- 单个 commit 文件变更 ≤ 10 个，超过则拆分
- 提交前确保代码可运行、不破坏现有功能

### 3. Commit Message 格式
采用 Conventional Commits 中文版：

<类型>(<模块>): <简短描述>

<详细描述（可选）>


**类型清单**：
- `feat`: 新功能
- `fix`: Bug 修复
- `refactor`: 重构（不改变外部行为）
- `docs`: 文档更新
- `style`: 格式调整（不影响代码逻辑）
- `perf`: 性能优化
- `test`: 测试相关
- `chore`: 构建/依赖/工具链调整

**模块清单**：
- `wecom`: 企微接入层
- `aggregator`: 群聊聚合引擎
- `retriever`: 检索应答
- `kb`: 知识入库
- `web`: Web UI
- `models`: 数据模型
- `config`: 配置
- `deploy`: 部署/运维

**示例**：

feat(wecom): 实现智能机器人长连接回调接收与 msgid 排重

• 解析 aibot_msg_callback 报文

• 提取 chattype/from.userid/msgid 等字段

• 基于 msgid 做消息排重

• 不支持的消息类型记录日志并忽略



fix(aggregator): 修复群聊第三人插话误中断聚合的问题

• 缓冲键由 chatid 改为 chatid+userid

• 时间窗内仅聚合同一 userid 的消息

• 其他 userid 发言不触发缓冲刷新


### 4. 提交频率
- 每完成一个**独立功能点**立即提交，不要累积
- 一天工作结束时，即使功能未完成，也应提交阶段性进度
- 提交前自查：代码是否符合本规范"代码规范"章节

### 5. 首批提交顺序（MVP 阶段）
1. `chore`: 初始化仓库结构 + .gitignore + requirements.txt
2. `docs`: 添加 README.md 与项目说明
3. `feat(models)`: SQLite 表结构（kb_docs FTS5 + pending_questions + sys_config）
4. `feat(config)`: 配置加载模块 + config.yaml.example
5. `feat(wecom)`: 长连接回调接收与 chattype 分流
6. `feat(aggregator)`: 群聊多消息聚合引擎
7. `feat(retriever)`: FTS5 检索 + 回复格式化 + 前缀拼接
8. `feat(kb)`: Git MD 文档同步与切分入库
9. `feat(wecom)`: 被动回复与无结果记录
10. `feat(web)`: Web UI 四个核心页面
11. `test`: 单元测试与集成测试
12. `docs`: 补充部署文档与使用说明

## 七、AI 行为约束

1. 生成代码时**必须**遵循上述代码规范
2. 涉及企微接口字段时，**必须以官方文档为准**，禁止凭印象编造字段名
3. 不确定企微接口行为时，先搜索官方文档或明确标注"待验证"
4. 修改代码时先理解现有结构，保持风格一致
5. 每个功能点完成后，提醒用户按原子提交规范 commit
6. 禁止为了提高"完整度"一次性生成大量未经测试的代码
7. 关键业务逻辑（聚合、检索、排重）生成后必须提醒用户补充单元测试
8. **禁止执行 `git push` 命令**，只允许 `git add` + `git commit`
9. **禁止创建除 `main` 以外的分支**

## 八、禁止事项

- ❌ 禁止硬编码密钥、Token、用户 ID
- ❌ 禁止 SQL 字符串拼接
- ❌ 禁止裸 except
- ❌ 禁止单函数超过 50 行不拆分
- ❌ 禁止英文注释（官方术语除外）
- ❌ 禁止一次性提交所有代码
- ❌ 禁止 `git push` 操作
- ❌ 禁止创建非 main 分支
- ❌ 禁止编造企微接口字段
- ❌ 禁止在群聊回复时声称已实现"真正的@强提醒"（企微原生智能机器人默认不支持）