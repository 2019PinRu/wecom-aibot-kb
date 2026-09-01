# -*- coding: utf-8 -*-
"""数据模型层：SQLite 连接管理与表结构定义。"""
import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

# kb_docs 建表语句（FTS5 虚拟表）：content 存 bigram 切分索引文本，原文存 raw_content
_KB_DOCS_DDL = """
CREATE VIRTUAL TABLE kb_docs USING fts5(
    title UNINDEXED,
    content,
    source UNINDEXED,
    doc_id UNINDEXED,
    raw_content UNINDEXED,
    tokenize = 'unicode61'
);
"""

# 建表 SQL：待补充问题表、系统配置表（幂等）
# kb_docs 虚拟表不支持 IF NOT EXISTS，改由 _create_kb_docs_if_missing 按存在性创建
_SCHEMA_SQL = """
-- 待补充问题表
CREATE TABLE IF NOT EXISTS pending_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    asker TEXT,
    chattype TEXT,
    chatid TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    answer TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_questions(status);

-- 手动/导入问答表（kb_docs 手动条目的持久化来源，同步重建后据此回灌）
CREATE TABLE IF NOT EXISTS manual_qa (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    answer TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_manual_qa_source ON manual_qa(source);

-- 系统配置表（Web 动态配置）
CREATE TABLE IF NOT EXISTS sys_config (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);
"""


def _create_kb_docs_if_missing(conn: sqlite3.Connection) -> None:
    """kb_docs 不存在时创建 FTS5 虚拟表（CREATE VIRTUAL TABLE 不支持 IF NOT EXISTS）。"""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='kb_docs'"
    ).fetchone()
    if row is None:
        conn.execute(_KB_DOCS_DDL)


def _migrate_kb_docs(conn: sqlite3.Connection) -> None:
    """检测 kb_docs 表结构，缺失 raw_content 列时重建（数据由下次同步全量重建）。"""
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(kb_docs)").fetchall()]
    if "raw_content" in columns:
        return
    logger.warning("检测到 kb_docs 旧表结构，重建以新增 raw_content 原文列")
    conn.execute("DROP TABLE IF EXISTS kb_docs")
    conn.execute(_KB_DOCS_DDL)


def get_connection(db_path: str) -> sqlite3.Connection:
    """获取 SQLite 连接，确保父目录存在并启用 WAL 模式。

    参数：
        db_path: 数据库文件路径。

    返回：
        配置好的 sqlite3.Connection。
    """
    parent_dir = os.path.dirname(os.path.abspath(db_path))
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_db(db_path: str) -> None:
    """初始化数据库，创建全部表结构（幂等）。

    参数：
        db_path: 数据库文件路径。

    返回：
        无。
    """
    conn = get_connection(db_path)
    try:
        with conn:
            conn.executescript(_SCHEMA_SQL)
            _create_kb_docs_if_missing(conn)
            _migrate_kb_docs(conn)
        logger.info("数据库初始化完成: %s", db_path)
    except sqlite3.Error as exc:
        logger.error("数据库初始化失败: %s", exc)
        raise
    finally:
        conn.close()


def execute(db_path: str, sql: str, params: tuple = ()) -> int:
    """执行单条写 SQL（参数化），失败时回滚并记录日志。

    参数：
        db_path: 数据库文件路径。
        sql: 参数化 SQL 语句。
        params: SQL 参数元组。

    返回：
        受影响行数。
    """
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.execute(sql, params)
            return cursor.rowcount
    except sqlite3.Error as exc:
        logger.error("SQL 执行失败: %s | SQL: %s", exc, sql)
        raise
    finally:
        conn.close()


def query(db_path: str, sql: str, params: tuple = ()) -> list[dict]:
    """执行查询 SQL（参数化），返回字典列表。

    参数：
        db_path: 数据库文件路径。
        sql: 参数化 SQL 语句。
        params: SQL 参数元组。

    返回：
        查询结果字典列表。
    """
    conn = get_connection(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error as exc:
        logger.error("SQL 查询失败: %s | SQL: %s", exc, sql)
        raise
    finally:
        conn.close()
