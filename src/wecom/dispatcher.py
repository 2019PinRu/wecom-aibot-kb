# -*- coding: utf-8 -*-
"""企微消息分流器：按 chattype 分发到单聊直答 / 群聊聚合链路。"""
import logging
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

# 当前支持处理的消息类型（仅 text；mixed/voice 转文本为后续扩展，字段结构待官方文档验证）
SUPPORTED_MSGTYPES = {"text"}


class Dispatcher:
    """企微消息分流器：按 body.chattype 分发消息。"""

    def __init__(
        self,
        reply_client,
        single_handler: Callable[[dict, str], Awaitable[None]] | None = None,
        group_handler: Callable[[dict, str], Awaitable[None]] | None = None,
    ) -> None:
        """初始化分流器。

        参数：
            reply_client: 回复客户端（ReplyClient 实例）。
            single_handler: 单聊处理函数（阶段 5 接入检索应答）。
            group_handler: 群聊处理函数（阶段 4 接入聚合引擎）。
        """
        self._reply_client = reply_client
        self._single_handler = single_handler
        self._group_handler = group_handler

    async def dispatch(self, body: dict, req_id: str) -> None:
        """分流入口：按 chattype 分发消息。

        参数：
            body: 消息回调 body。
            req_id: headers.req_id，回复时透传。
        """
        chattype = body.get("chattype") or ""
        msgtype = body.get("msgtype") or ""
        if msgtype not in SUPPORTED_MSGTYPES:
            logger.info("暂不支持的消息类型 msgtype=%s，忽略", msgtype)
            return
        if chattype == "single":
            await self._dispatch_single(body, req_id)
        elif chattype == "group":
            await self._dispatch_group(body, req_id)
        else:
            logger.warning("未知 chattype=%s，忽略", chattype)

    async def _dispatch_single(self, body: dict, req_id: str) -> None:
        """单聊：用户发消息即视为新问题，直接进入检索应答。"""
        if self._single_handler:
            await self._single_handler(body, req_id)
        else:
            logger.info("单聊消息（阶段 5 检索应答接入前仅记录）: %s", self._extract_content(body))

    async def _dispatch_group(self, body: dict, req_id: str) -> None:
        """群聊：进入聚合引擎做多消息聚合。"""
        if self._group_handler:
            await self._group_handler(body, req_id)
        else:
            logger.info("群聊消息（阶段 4 聚合引擎接入前仅记录）: %s", self._extract_content(body))

    @staticmethod
    def _extract_content(body: dict) -> str:
        """提取文本消息内容。

        参数：
            body: 消息回调 body。

        返回：
            消息文本内容。
        """
        if body.get("msgtype") == "text":
            return (body.get("text") or {}).get("content", "")
        return ""
