# -*- coding: utf-8 -*-
"""数据模型层：SQLite 连接管理与表结构定义。"""
import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

# 建表 SQL：知识库 FTS5 表、待补充问题表、系统配置表（幂等）
_SCHEMA_SQL = """
-- 知识库文档表（FTS5 全文检索虚拟表）
CREATE VIRTUAL TABLE IF NOT EXISTS kb_docs USING fts5(
    title,
    content,
    source,
    doc_id,
    tokenize = 'unicode61'
);

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

-- 系统配置表（Web 动态配置）
CREATE TABLE IF NOT EXISTS sys_config (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);
"""


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
