# -*- coding: utf-8 -*-
"""Web UI 路由：概览、知识库管理、待补充问题、系统参数。"""
import asyncio
import logging
import sqlite3

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from kb.git_sync import sync_and_ingest
from models.db import execute, query
from retriever.fts5 import build_match_query
from utils.config import Config

logger = logging.getLogger(__name__)

# 每页展示条数
PAGE_SIZE = 20

# 可编辑动态配置键：点分路径 -> 中文说明
EDITABLE_KEYS = [
    {"key": "reply.prefix", "label": "回复前缀"},
    {"key": "reply.no_answer", "label": "无结果话术"},
    {"key": "aggregate.window_seconds", "label": "群聊聚合时间窗（秒）"},
    {"key": "retriever.score_threshold", "label": "检索置信度阈值"},
    {"key": "retriever.top_k", "label": "召回候选条数"},
]


def create_router(config: Config, db_path: str) -> APIRouter:
    """装配 Web 全部页面路由。

    参数：
        config: 配置对象。
        db_path: SQLite 数据库文件路径。

    返回：
        配置完成的 APIRouter。
    """
    router = APIRouter()

    @router.get("/")
    async def dashboard(request: Request):
        """概览页：知识片段数与待补充问题状态统计。"""
        kb_count = _count(db_path, "SELECT COUNT(*) AS c FROM kb_docs")
        pending_total = _count(db_path, "SELECT COUNT(*) AS c FROM pending_questions")
        pending_open = _count(
            db_path, "SELECT COUNT(*) AS c FROM pending_questions WHERE status='pending'"
        )
        pending_resolved = _count(
            db_path, "SELECT COUNT(*) AS c FROM pending_questions WHERE status='resolved'"
        )
        context = {
            "request": request,
            "kb_count": kb_count,
            "pending_total": pending_total,
            "pending_open": pending_open,
            "pending_resolved": pending_resolved,
        }
        return _render(request, "dashboard.html", context)

    @router.get("/kb")
    async def kb_list(request: Request, page: int = 1, q: str = ""):
        """知识库管理页：分页列表与关键词搜索。"""
        keyword = q.strip()
        match_expr = build_match_query(keyword) if keyword else ""
        current_page = max(page, 1)
        offset = (current_page - 1) * PAGE_SIZE
        if match_expr:
            total = _count(
                db_path,
                "SELECT COUNT(*) AS c FROM kb_docs WHERE kb_docs MATCH ?",
                (match_expr,),
            )
            rows = query(
                db_path,
                "SELECT rowid, title, source, doc_id, substr(raw_content, 1, 100) AS preview "
                "FROM kb_docs WHERE kb_docs MATCH ? ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (match_expr, PAGE_SIZE, offset),
            )
        else:
            total = _count(db_path, "SELECT COUNT(*) AS c FROM kb_docs")
            rows = query(
                db_path,
                "SELECT rowid, title, source, doc_id, substr(raw_content, 1, 100) AS preview "
                "FROM kb_docs ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (PAGE_SIZE, offset),
            )
        context = {
            "request": request,
            "rows": rows,
            "total": total,
            "page": current_page,
            "pages": max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1),
            "q": keyword,
        }
        return _render(request, "kb.html", context)

    @router.post("/kb/sync")
    async def kb_sync():
        """触发知识库全量同步重建。"""
        count = await asyncio.to_thread(sync_and_ingest, config)
        return _redirect(f"/kb?msg=同步完成，入库 {count} 个片段")

    @router.post("/kb/{rowid}/delete")
    async def kb_delete(rowid: int):
        """删除单条知识片段。"""
        try:
            execute(db_path, "DELETE FROM kb_docs WHERE rowid = ?", (rowid,))
        except sqlite3.Error as exc:
            logger.error("删除知识片段失败: %s", exc)
            raise HTTPException(status_code=500, detail="删除失败")
        return _redirect(f"/kb?msg=已删除片段 #{rowid}")

    @router.get("/pending")
    async def pending_list(request: Request, status: str = "pending"):
        """待补充问题页：按状态筛选列表。"""
        valid_status = status if status in ("pending", "resolved") else "pending"
        rows = query(
            db_path,
            "SELECT id, question, asker, chattype, chatid, status, answer, created_at "
            "FROM pending_questions WHERE status = ? ORDER BY id DESC",
            (valid_status,),
        )
        context = {"request": request, "rows": rows, "status": valid_status}
        return _render(request, "pending.html", context)

    @router.post("/pending/{item_id}/resolve")
    async def pending_resolve(item_id: int, answer: str = Form("")):
        """补充答案并标记为已处理。"""
        sql = (
            "UPDATE pending_questions SET status='resolved', answer=?, "
            "updated_at=datetime('now','localtime') WHERE id=?"
        )
        try:
            execute(db_path, sql, (answer.strip(), item_id))
        except sqlite3.Error as exc:
            logger.error("更新待补充问题失败: %s", exc)
            raise HTTPException(status_code=500, detail="更新失败")
        return _redirect(f"/pending?status=resolved&msg=问题 #{item_id} 已处理")

    @router.get("/config")
    async def config_page(request: Request):
        """系统参数页：展示可编辑动态配置键及当前值。"""
        items = [
            {"key": item["key"], "label": item["label"], "value": config.get(item["key"], "")}
            for item in EDITABLE_KEYS
        ]
        context = {"request": request, "items": items}
        return _render(request, "config.html", context)

    @router.post("/config/save")
    async def config_save(request: Request):
        """保存系统参数到 sys_config 表，重启后生效。"""
        form = await request.form()
        saved = 0
        for item in EDITABLE_KEYS:
            key = item["key"]
            value = form.get(key)
            if value is None:
                continue
            _upsert_config(db_path, key, str(value))
            saved += 1
        return _redirect(f"/config?msg=已保存 {saved} 项，重启进程后生效")

    return router


def _render(request: Request, template_name: str, context: dict):
    """渲染模板，注入公共 msg 反馈参数。"""
    context["msg"] = request.query_params.get("msg", "")
    return request.app.state.templates.TemplateResponse(request, template_name, context)


def _redirect(url: str) -> RedirectResponse:
    """生成 303 跳转响应。"""
    return RedirectResponse(url, status_code=303)


def _count(db_path: str, sql: str, params: tuple = ()) -> int:
    """执行 COUNT 查询并返回整数，失败时返回 0。"""
    try:
        rows = query(db_path, sql, params)
    except sqlite3.Error as exc:
        logger.error("统计查询失败: %s", exc)
        return 0
    return int(rows[0]["c"]) if rows else 0


def _upsert_config(db_path: str, key: str, value: str) -> None:
    """UPSERT 写入 sys_config 动态配置键值。"""
    sql = (
        "INSERT INTO sys_config (key, value, updated_at) "
        "VALUES (?, ?, datetime('now','localtime')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at"
    )
    try:
        execute(db_path, sql, (key, value))
    except sqlite3.Error as exc:
        logger.error("保存配置失败: %s", exc)
        raise HTTPException(status_code=500, detail="保存配置失败")
