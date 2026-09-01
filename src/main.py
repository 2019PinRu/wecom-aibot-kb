# -*- coding: utf-8 -*-
"""应用入口：装配配置、数据库、企微接入与 Web 路由，供 uvicorn 启动。"""
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from aggregator.buffer import MessageBuffer
from models.db import init_db
from retriever.responder import Responder
from utils.config import Config, resolve_project_path
from wecom.callback import CallbackClient
from wecom.client import ReplyClient
from wecom.dispatcher import Dispatcher
from web.routes import create_router

# 日志基础配置：时间 | 级别 | 模块 | 消息
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def create_app(config_path: str | None = None) -> FastAPI:
    """装配 FastAPI 应用：配置、数据库、企微接入与 Web 路由。

    参数：
        config_path: 配置文件路径；为 None 时使用默认 config.yaml。

    返回：
        装配完成的 FastAPI 应用。
    """
    config = Config(config_path)
    # 数据库路径基于项目根解析，避免从 src 启动时落到 src/data 下
    db_path = resolve_project_path(config.get("storage.db_path", "data/kb.db"))
    init_db(db_path)
    config.overlay_db(db_path)

    # 装配企微接入与检索应答链路
    reply_client = ReplyClient(config.get("aggregate.max_queue", 3))
    responder = Responder(config, reply_client, db_path)
    single_handler = _make_single_handler(responder)
    buffer = _make_buffer(config, responder)
    group_handler = _make_group_handler(buffer)
    dispatcher = Dispatcher(reply_client, single_handler, group_handler)
    callback = CallbackClient(config, dispatcher, reply_client)

    # 装配 FastAPI 应用与生命周期
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """应用生命周期：按凭据是否齐全决定是否启动企微长连接。"""
        if config.get("wecom.bot_id") and config.get("wecom.bot_secret"):
            await callback.start()
            logger.info("企微长连接已启动")
        else:
            logger.warning("未配置 bot_id/bot_secret，仅启动 Web 界面")
        yield
        await callback.stop()

    app = FastAPI(title="wecom-aibot-kb", lifespan=lifespan)
    templates_dir = os.path.join(os.path.dirname(__file__), "web", "templates")
    app.state.templates = Jinja2Templates(directory=templates_dir)
    app.state.config = config
    app.include_router(create_router(config, db_path))
    return app


def _make_single_handler(responder: Responder):
    """构造单聊处理函数：直接提取文本内容并检索回复。"""

    async def single_handler(body: dict, req_id: str) -> None:
        """单聊：用户发消息即视为新问题，进入统一应答入口。"""
        meta = {
            "chattype": "single",
            "userid": (body.get("from") or {}).get("userid"),
            "chatid": None,
        }
        await responder.handle_question(_extract_text(body), req_id, meta)

    return single_handler


def _make_group_handler(buffer: MessageBuffer):
    """构造群聊处理函数：提取会话信息进入聚合缓冲。"""

    async def group_handler(body: dict, req_id: str) -> None:
        """群聊：提取 chatid/userid/content 进入聚合引擎。"""
        chatid = body.get("chatid")
        userid = (body.get("from") or {}).get("userid")
        await buffer.add(chatid, userid, _extract_text(body), req_id)

    return group_handler


def _make_buffer(config: Config, responder: Responder) -> MessageBuffer:
    """构造群聊聚合缓冲区，聚合完成回调进入统一应答入口。"""
    mention_targets = config.get("aggregate.mention_targets", [])
    if not mention_targets:
        mention_targets = [config.get("wecom.bot_id", "")]

    async def on_complete(question: str, req_id: str, meta: dict) -> None:
        """聚合完成：携带群聊元信息进入统一应答入口。"""
        await responder.handle_question(question, req_id, {**meta, "chattype": "group"})

    return MessageBuffer(
        config.get("aggregate.window_seconds", 30),
        mention_targets,
        on_complete,
    )


def _extract_text(body: dict) -> str:
    """提取文本消息内容。

    参数：
        body: 消息回调 body。

    返回：
        消息文本内容。
    """
    if body.get("msgtype") == "text":
        return (body.get("text") or {}).get("content", "")
    return ""


# 模块级应用实例，供 `uvicorn main:app` 使用
app = create_app()


if __name__ == "__main__":
    # 支持 `python -m main` 直接启动
    uvicorn.run(
        "main:app",
        host=app.state.config.get("web.host", "0.0.0.0"),
        port=app.state.config.get("web.port", 8080),
        reload=False,
    )
