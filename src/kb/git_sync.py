# -*- coding: utf-8 -*-
"""知识入库流水线：Git MD 文档同步、切分与 FTS5 全量入库。"""
import glob
import logging
import os
import sqlite3
import subprocess

from models.db import get_connection
from retriever.fts5 import segment_bigrams

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
    repo_path = config.get("kb.repo_path", "") or ""
    work_dir = config.get("kb.work_dir", "data/kb_repo")
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
        docs.extend(build_docs(repo_dir, rel, chunk_size, chunk_overlap))
    count = ingest_docs(config.get("storage.db_path", "data/kb.db"), docs)
    logger.info("知识库同步完成，入库 %d 个片段", count)
    return count
