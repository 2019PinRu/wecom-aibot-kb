# -*- coding: utf-8 -*-
"""知识入库流水线：Git MD 文档同步、切分与 FTS5 全量入库。"""
import csv
import glob
import io
import logging
import os
import sqlite3
import subprocess

import yaml

from models.db import execute, get_connection, query
from retriever.fts5 import segment_bigrams
from utils.config import resolve_project_path

logger = logging.getLogger(__name__)

# Markdown 一级到六级标题前缀，用于按标题切分片段
_HEADING_PREFIXES = ("# ", "## ", "### ", "#### ", "##### ", "###### ")


def sync_repo(config) -> str | None:
    """同步 Git 仓库或校验本地目录，返回 MD 文档工作目录。

    参数：
        config: 配置对象（Config 实例）。

    返回：
        MD 文档所在目录路径；同步失败或未配置时返回 None。
    """
    repo_url = config.get("kb.repo_url", "") or ""
    # 本地目录与仓库工作目录基于项目根解析，与启动目录无关
    repo_path = resolve_project_path(config.get("kb.repo_path", "") or "")
    work_dir = resolve_project_path(config.get("kb.work_dir", "data/kb_repo"))
    try:
        if repo_url:
            return work_dir if _ensure_repo(repo_url, config.get("kb.repo_branch", "main"), work_dir) else None
        if repo_path:
            if os.path.isdir(repo_path):
                return repo_path
            logger.error("本地文档目录不存在: %s", repo_path)
            return None
    except subprocess.SubprocessError as exc:
        logger.error("Git 同步失败: %s", exc)
        return None
    logger.warning("未配置 kb.repo_url 与 kb.repo_path，跳过同步")
    return None


def _ensure_repo(repo_url: str, branch: str, work_dir: str) -> bool:
    """克隆或拉取 Git 仓库到工作目录。

    参数：
        repo_url: 仓库地址。
        branch: 同步分支。
        work_dir: 工作目录路径。

    返回：
        True 同步成功；False 失败。
    """
    try:
        if os.path.isdir(os.path.join(work_dir, ".git")):
            _run_git(["git", "-C", work_dir, "pull", "--ff-only"])
        else:
            os.makedirs(os.path.dirname(os.path.abspath(work_dir)) or ".", exist_ok=True)
            _run_git(["git", "clone", "--depth", "1", "-b", branch, repo_url, work_dir])
        return True
    except subprocess.SubprocessError as exc:
        logger.error("Git 仓库操作失败: %s", exc)
        return False


def _run_git(args: list[str], timeout: int = 120) -> None:
    """执行 git 命令，失败抛 subprocess 异常由上层处理。"""
    subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout)


def scan_md_files(repo_dir: str, include_patterns: list[str], exclude_patterns: list[str]) -> list[str]:
    """扫描目录下 MD 文件，返回相对路径列表。

    参数：
        repo_dir: 文档工作目录。
        include_patterns: 入库文件匹配模式。
        exclude_patterns: 排除文件名列表（按 basename 匹配）。

    返回：
        MD 文件相对路径排序列表。
    """
    files = []
    for pattern in include_patterns:
        for path in glob.glob(os.path.join(repo_dir, "**", pattern), recursive=True):
            if not os.path.isfile(path):
                continue
            rel = os.path.relpath(path, repo_dir).replace("\\", "/")
            if os.path.basename(rel) in exclude_patterns:
                continue
            files.append(rel)
    return sorted(set(files))


def split_markdown(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """按标题与长度切分 Markdown 文档为片段。

    参数：
        text: Markdown 原文。
        chunk_size: 片段目标字符数。
        chunk_overlap: 相邻片段尾部重叠字符数。

    返回：
        切分后的片段列表。
    """
    chunks, buf, buf_len = [], [], 0
    for line in text.splitlines():
        if line.lstrip().startswith(_HEADING_PREFIXES) and buf:
            chunks.append("\n".join(buf))
            buf, buf_len = [], 0
        buf.append(line)
        buf_len += len(line)
        if buf_len >= chunk_size:
            chunk, buf = _split_lines(buf, chunk_size, chunk_overlap)
            chunks.append(chunk)
            buf_len = sum(len(x) for x in buf)
    if buf:
        chunks.append("\n".join(buf))
    return [c.strip() for c in chunks if c.strip()]


def _split_lines(buf: list[str], chunk_size: int, chunk_overlap: int) -> tuple[str, list[str]]:
    """将缓冲切为一段，返回（片段, 尾部重叠剩余行）。

    参数：
        buf: 缓冲行列表。
        chunk_size: 片段目标字符数。
        chunk_overlap: 尾部重叠字符数（不超过 chunk_size 一半，防死循环）。

    返回：
        (片段文本, 进入下一片段的重叠行)。
    """
    chunk_overlap = min(chunk_overlap, chunk_size // 2)
    acc, acc_len, cut = [], 0, len(buf)
    for i, line in enumerate(buf):
        if acc and acc_len + len(line) > chunk_size:
            cut = i
            break
        acc.append(line)
        acc_len += len(line)
    chunk = "\n".join(buf[:cut])
    keep, keep_len = [], 0
    for line in reversed(buf[:cut]):
        keep.insert(0, line)
        keep_len += len(line)
        if keep_len >= chunk_overlap:
            break
    return chunk, keep


def build_docs(repo_dir: str, rel_path: str, chunk_size: int, chunk_overlap: int) -> list[dict]:
    """读取单个 MD 文件并生成入库文档片段列表。

    参数：
        repo_dir: 文档工作目录。
        rel_path: 文档相对路径。
        chunk_size: 切分片段字符数。
        chunk_overlap: 片段尾部重叠字符数。

    返回：
        入库文档字典列表（title/content/source/doc_id/raw_content）。
    """
    full_path = os.path.join(repo_dir, rel_path)
    try:
        with open(full_path, encoding="utf-8") as f:
            raw = f.read()
    except OSError as exc:
        logger.error("读取文档失败: %s | %s", rel_path, exc)
        return []
    title = _extract_title(raw) or os.path.basename(rel_path)
    docs = []
    for idx, chunk in enumerate(split_markdown(raw, chunk_size, chunk_overlap)):
        docs.append(
            {
                "title": title,
                "content": " ".join(segment_bigrams(chunk)),
                "source": rel_path,
                "doc_id": f"{rel_path}:{idx}",
                "raw_content": chunk,
            }
        )
    return docs


def _is_skill_file(rel_path: str) -> bool:
    """判断文件是否为 SKILL.md 技能定义文档（忽略大小写）。"""
    return os.path.basename(rel_path).lower() == "skill.md"


def parse_skill_frontmatter(raw: str) -> dict | None:
    """解析 SKILL.md 头部 YAML frontmatter，返回 name/description/body。

    参数：
        raw: 文件原文。

    返回：
        含 name/description/body 的字典；无有效 frontmatter 返回 None。
    """
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    # 寻找结束分隔符 --- 或 ...
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            end = i
            break
    if end is None:
        return None
    try:
        meta = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        logger.error("SKILL.md frontmatter 解析失败: %s", exc)
        return None
    if not isinstance(meta, dict):
        return None
    name = str(meta.get("name", "") or "").strip()
    description = str(meta.get("description", "") or "").strip()
    if not name:
        return None
    return {"name": name, "description": description, "body": "\n".join(lines[end + 1 :])}


def build_skill_docs(repo_dir: str, rel_path: str, chunk_size: int, chunk_overlap: int) -> list[dict]:
    """读取 SKILL.md 并按 Skill 语义生成入库片段（name 作标题、description 入索引）。

    参数：
        repo_dir: 文档工作目录。
        rel_path: 文档相对路径。
        chunk_size: 切分片段字符数。
        chunk_overlap: 片段尾部重叠字符数。

    返回：
        入库文档字典列表；无有效 frontmatter 时回退普通 Markdown 处理。
    """
    full_path = os.path.join(repo_dir, rel_path)
    try:
        with open(full_path, encoding="utf-8") as f:
            raw = f.read()
    except OSError as exc:
        logger.error("读取文档失败: %s | %s", rel_path, exc)
        return []
    meta = parse_skill_frontmatter(raw)
    if meta is None:
        return build_docs(repo_dir, rel_path, chunk_size, chunk_overlap)
    index_head = f"{meta['name']} {meta['description']}"
    chunks = split_markdown(meta["body"], chunk_size, chunk_overlap)
    if not chunks:
        # 正文为空时至少以描述作为内容，保证技能仍可被检索命中
        chunks = [meta["description"] or meta["name"]]
    docs = []
    for idx, chunk in enumerate(chunks):
        docs.append(
            {
                "title": meta["name"],
                "content": " ".join(segment_bigrams(f"{index_head}\n{chunk}")),
                "source": rel_path,
                "doc_id": f"{rel_path}:{idx}",
                "raw_content": chunk,
            }
        )
    return docs


def _extract_title(raw: str) -> str:
    """提取文档首个一级标题文本，无则返回空串。"""
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def ingest_docs(db_path: str, docs: list[dict]) -> int:
    """全量重建 kb_docs 表，返回入库片段数。

    参数：
        db_path: 数据库文件路径。
        docs: 入库文档字典列表。

    返回：
        入库片段数。
    """
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM kb_docs")
            conn.executemany(
                "INSERT INTO kb_docs (title, content, source, doc_id, raw_content) "
                "VALUES (:title, :content, :source, :doc_id, :raw_content)",
                docs,
            )
        return len(docs)
    except sqlite3.Error as exc:
        logger.error("知识入库失败: %s", exc)
        raise
    finally:
        conn.close()


def build_qa_doc(title: str, answer: str, source: str, doc_id: str) -> dict:
    """构造单条问答知识片段字典，供手动录入、批量导入与回灌共用。

    参数：
        title: 问题（标题）原文。
        answer: 答案原文。
        source: 来源标记（manual/import/pending）。
        doc_id: 片段唯一标识。

    返回：
        知识片段字典（title/content/source/doc_id/raw_content）。
    """
    index_text = f"{title} {answer}"
    return {
        "title": title,
        "content": " ".join(segment_bigrams(index_text)),
        "source": source,
        "doc_id": doc_id,
        "raw_content": answer,
    }


def build_pending_doc(question: str, answer: str, item_id: int) -> dict:
    """构造待补充问答知识片段字典，供单条写入与全量回灌共用。

    参数：
        question: 待补充问题原文。
        answer: 管理员补充的答案。
        item_id: 待补充问题行 id，用于生成唯一 doc_id。

    返回：
        知识片段字典（title/content/source/doc_id/raw_content）。
    """
    return build_qa_doc(question, answer, "pending", f"pending:{item_id}")


def load_pending_docs(db_path: str) -> list[dict]:
    """读取全部已答待补充问题，生成知识片段列表（同步重建后回灌用）。

    参数：
        db_path: 数据库文件路径。

    返回：
        pending 知识片段字典列表；查询失败返回空列表。
    """
    try:
        rows = query(
            db_path,
            "SELECT id, question, answer FROM pending_questions "
            "WHERE status='resolved' AND answer IS NOT NULL AND TRIM(answer) != ''",
        )
    except sqlite3.Error as exc:
        logger.error("读取已答待补充问题失败: %s", exc)
        return []
    return [build_pending_doc(r["question"], r["answer"], r["id"]) for r in rows]


def load_manual_docs(db_path: str) -> list[dict]:
    """读取全部手动/导入问答，生成知识片段列表（同步重建后回灌用）。

    参数：
        db_path: 数据库文件路径。

    返回：
        手动/导入知识片段字典列表；查询失败返回空列表。
    """
    try:
        rows = query(db_path, "SELECT id, title, answer, source FROM manual_qa ORDER BY id")
    except sqlite3.Error as exc:
        logger.error("读取手动问答失败: %s", exc)
        return []
    return [
        build_qa_doc(r["title"], r["answer"], r["source"], f"{r['source']}:{r['id']}")
        for r in rows
    ]


def insert_manual_qa(db_path: str, title: str, answer: str, source: str) -> int:
    """写入一条手动/导入问答到 manual_qa 持久化表，返回新行 id。

    参数：
        db_path: 数据库文件路径。
        title: 问题（标题）。
        answer: 答案。
        source: 来源标记（manual/import）。

    返回：
        新写入行的主键 id。
    """
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.execute(
                "INSERT INTO manual_qa (title, answer, source) VALUES (?, ?, ?)",
                (title, answer, source),
            )
            return cursor.lastrowid
    except sqlite3.Error as exc:
        logger.error("写入 manual_qa 失败: %s", exc)
        raise
    finally:
        conn.close()


def update_manual_qa(db_path: str, manual_id: int, title: str, answer: str) -> int:
    """更新 manual_qa 表指定行的标题与答案。

    参数：
        db_path: 数据库文件路径。
        manual_id: manual_qa 行 id。
        title: 新的问题（标题）。
        answer: 新的答案。

    返回：
        受影响行数。
    """
    sql = (
        "UPDATE manual_qa SET title = ?, answer = ?, "
        "updated_at = datetime('now','localtime') WHERE id = ?"
    )
    try:
        return execute(db_path, sql, (title, answer, manual_id))
    except sqlite3.Error as exc:
        logger.error("更新 manual_qa 失败: %s", exc)
        raise


def insert_qa(db_path: str, title: str, answer: str, source: str, doc_id: str) -> None:
    """构造并写入单条问答到 kb_docs（先按 doc_id 删旧再插入，防重复）。

    参数：
        db_path: 数据库文件路径。
        title: 问题（标题）。
        answer: 答案。
        source: 来源标记。
        doc_id: 片段唯一标识。

    返回：
        无。
    """
    doc = build_qa_doc(title, answer, source, doc_id)
    try:
        execute(db_path, "DELETE FROM kb_docs WHERE doc_id = ?", (doc["doc_id"],))
        execute(
            db_path,
            "INSERT INTO kb_docs (title, content, source, doc_id, raw_content) "
            "VALUES (?, ?, ?, ?, ?)",
            (doc["title"], doc["content"], doc["source"], doc["doc_id"], doc["raw_content"]),
        )
    except sqlite3.Error as exc:
        logger.error("写入 kb_docs 失败: %s", exc)
        raise


def parse_faq_csv(csv_text: str) -> list[dict]:
    """解析 CSV 文本为问答列表，格式为「问题,答案」，首行可为表头。

    参数：
        csv_text: CSV 文本（逗号分隔两列，问题在前、答案在后）。

    返回：
        [{title, answer}, ...]；空行、表头行、缺列行自动跳过。
    """
    items: list[dict] = []
    reader = csv.reader(io.StringIO(csv_text))
    for lineno, row in enumerate(reader, start=1):
        if not row or not any(cell.strip() for cell in row):
            continue
        if lineno == 1 and row[0].strip().lower() in ("question", "问题", "标题", "title"):
            continue
        if len(row) < 2:
            logger.error("CSV 第 %d 行列数不足 2，跳过", lineno)
            continue
        title = row[0].strip()
        answer = row[1].strip()
        if title and answer:
            items.append({"title": title, "answer": answer})
    return items


def sync_and_ingest(config) -> int:
    """顶层入口：同步 → 扫描 → 切分 → 入库，返回入库片段数。

    参数：
        config: 配置对象（Config 实例）。

    返回：
        入库片段数；同步失败返回 0。
    """
    repo_dir = sync_repo(config)
    if not repo_dir:
        return 0
    files = scan_md_files(
        repo_dir,
        config.get("kb.include_patterns", ["*.md"]),
        config.get("kb.exclude_patterns", ["README.md"]),
    )
    chunk_size = config.get("kb.chunk_size", 500)
    chunk_overlap = config.get("kb.chunk_overlap", 50)
    docs = []
    for rel in files:
        if _is_skill_file(rel):
            docs.extend(build_skill_docs(repo_dir, rel, chunk_size, chunk_overlap))
        else:
            docs.extend(build_docs(repo_dir, rel, chunk_size, chunk_overlap))
    # 数据路径基于项目根解析，与启动目录无关
    db_path = resolve_project_path(config.get("storage.db_path", "data/kb.db"))
    # 全量重建会清空 kb_docs，重建后回灌 pending 已答记录与手动/导入问答，防止丢失
    pending_docs = load_pending_docs(db_path)
    manual_docs = load_manual_docs(db_path)
    docs.extend(pending_docs)
    docs.extend(manual_docs)
    count = ingest_docs(db_path, docs)
    logger.info(
        "知识库同步完成，入库 %d 个片段（pending 补充答案 %d 条，手动/导入 %d 条）",
        count,
        len(pending_docs),
        len(manual_docs),
    )
    return count
