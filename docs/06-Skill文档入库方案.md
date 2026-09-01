# 06-Skill 文档入库方案

> 版本：v0.1
> 说明：知识入库流水线支持 **SKILL.md**（AI 技能定义文档）——识别并解析 YAML frontmatter 元数据，与普通 Markdown 文档一并同步入库。

***

## 一、背景与目标

知识库当前仅支持普通 Markdown 文档入库（`git_sync.py`）。而 AI 生态中的「技能（Skill）」以 `SKILL.md` 文件分发，其头部携带 YAML frontmatter（`name`、`description`），正文为使用说明/指令。

用户希望这类 `SKILL.md` 能被知识库识别入库，且：

1. **解析元数据单独入库**：提取 `name` 作为标题、`description` 参与检索索引，与普通文档区分。
2. **并入现有 MD 同步**：`SKILL.md` 随 `repo_url` / `repo_path` 扫描到的 `.md` 一起同步，无需额外配置源。

***

## 二、SKILL.md 格式约定

```markdown
---
name: my-skill
description: 这个技能做什么、何时使用它（用于检索与匹配）
---

# 技能标题（可选）

正文：技能的使用说明、步骤、示例等……
```

- `name`：技能名（小写字母/数字/连字符），用于知识片段**标题**。

- `description`：技能描述，用于**检索索引**，让用户按语义提问也能命中。

- `---` 之间是 YAML 块；正文是第二个 `---`（或 `...`）之后的内容。

***

## 三、识别与解析规则

1. **识别**：文件名（basename）忽略大小写为 `SKILL.md`。
2. **解析 frontmatter**：文本以 `---` 开头时，截取第二个分隔符前的 YAML 块，用 `yaml.safe_load` 解析 `name` / `description`，剩余内容作为正文 `body`。
3. **回退**：非 `SKILL.md` 文件，或 `SKILL.md` 无有效 frontmatter（缺 `name`），回退到普通 Markdown 处理（保持原有行为）。

***

## 四、入库设计（`src/kb/git_sync.py`）

| 字段            | 普通 MD              | SKILL.md                          |
| ------------- | ------------------ | --------------------------------- |
| `title`       | 首个 `# 标题`（无则文件名）   | `name`（无则文件名）                     |
| `content`（索引） | bigram(正文片段)       | bigram(`name description` + 正文片段) |
| `source`      | 相对路径               | 相对路径（保持一致）                        |
| `doc_id`      | `{rel_path}:{idx}` | `{rel_path}:{idx}`                |
| `raw_content` | 正文片段               | 正文片段                              |

- `description` 纳入 `content` 索引，使「这个技能是干嘛的」「XX 技能」等语义提问也能命中。

- `source` / `doc_id` 沿用相对路径规则，与普通文档、pending/manual/import 均无冲突，且随全量同步自然重建。

### 新增接口

| 接口                                                                              | 说明               | <br />                                                |
| ------------------------------------------------------------------------------- | ---------------- | ----------------------------------------------------- |
| `_is_skill_file(rel_path) -> bool`                                              | 判断文件是否为 SKILL.md | <br />                                                |
| \`parse\_skill\_frontmatter(raw) -> dict                                        | None\`           | 解析 frontmatter，返回 `{name, description, body}`；无则 None |
| `build_skill_docs(repo_dir, rel_path, chunk_size, chunk_overlap) -> list[dict]` | 按 Skill 语义构造入库片段 | <br />                                                |

`sync_and_ingest` 的扫描循环改为：`SKILL.md` 走 `build_skill_docs`，其余走 `build_docs`。

***

## 五、操作流程

1. 将含 `SKILL.md` 的技能目录放入 `kb.repo_path`（本地目录）或 `kb.repo_url`（Git 仓库）指向的文档根目录；
2. 进入 Web「知识库管理」页点击「触发同步」（或定时/启动触发）；
3. `sync_and_ingest` 扫描到 `SKILL.md`，解析 frontmatter 后按 Skill 语义入库；
4. 用户在企业微信提问技能名/描述相关内容，即可检索命中并回复正文。

***

## 六、验收标准

1. `SKILL.md` 被识别并解析出 `name` / `description`；
2. 入库片段的 `title` 为技能 `name`，`content` 索引包含 `description`；
3. 无有效 frontmatter 的 `SKILL.md` 回退为普通 Markdown 入库，不报错；
4. 普通 `.md` 文件行为保持不变；
5. 代码符合规范：中文注释、参数化 SQL、try/except 指定异常类型、单函数 ≤ 50 行。

***

> 说明：本阶段仅扩展 `src/kb/git_sync.py` 的解析逻辑，不涉及数据表变更，不改变企微接入与检索核心链路。

