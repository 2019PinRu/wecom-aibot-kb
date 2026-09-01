# -*- coding: utf-8 -*-
"""群聊多消息聚合引擎：chatid+userid 时间窗缓冲。"""
import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


def strip_mention(content: str, targets: list[str]) -> tuple[str, bool]:
    """判断消息是否 @ 目标，并去除 @ 前缀。

    参数：
        content: 原始消息文本。
        targets: @ 目标列表（机器人名称 / aibotid）。

    返回：
        (去除 @ 后的文本, 是否命中 @)。
    """
    text = content.strip()
    for target in targets:
        if not target:
            continue
        mention = f"@{target}"
        if text.startswith(mention):
            return text[len(mention):].strip(), True
    return text, False


class MessageBuffer:
    """群聊多消息聚合缓冲区，按 chatid+userid 独立聚合，互不干扰。"""

    def __init__(
        self,
        window_seconds: int,
        mention_targets: list[str],
        on_complete: Callable[[str, str, dict], Awaitable[None]],
    ) -> None:
        """初始化聚合引擎。

        参数：
            window_seconds: 时间窗（秒）。
            mention_targets: @ 目标列表。
            on_complete: 聚合完成回调，签名 async (question, req_id, meta) -> None。
        """
        self._window_seconds = window_seconds
        self._mention_targets = mention_targets
        self._on_complete = on_complete
        self._buffers: dict[tuple[str, str], dict] = {}

    async def add(self, chatid: str, userid: str, content: str, req_id: str) -> None:
        """聚合一条群聊消息。

        参数：
            chatid: 群聊会话 id。
            userid: 消息发送者 userid。
            content: 消息文本内容。
            req_id: 消息回调 headers.req_id。
        """
        key = (chatid, userid)
        cleaned, at_robot = strip_mention(content, self._mention_targets)
        if key not in self._buffers:
            if not at_robot:
                logger.debug("首条未 @ 本机器人，丢弃: %s", content)
                return
            # 首条 @ 命中：建立缓冲并启动时间窗
            ctx = {"parts": [cleaned], "first_req_id": req_id, "at_robot": True, "timer": None}
            self._buffers[key] = ctx
            self._start_timer(key, ctx)
            return
        # 已有缓冲：直接拼接，重置时间窗
        ctx = self._buffers[key]
        ctx["parts"].append(cleaned)
        self._start_timer(key, ctx)

    def _start_timer(self, key: tuple[str, str], ctx: dict) -> None:
        """启动或重置时间窗定时器。"""
        if ctx["timer"] is not None:
            ctx["timer"].cancel()
        loop = asyncio.get_running_loop()
        ctx["timer"] = loop.call_later(self._window_seconds, self._on_window_expire, key)

    def _on_window_expire(self, key: tuple[str, str]) -> None:
        """时间窗到期：首条未 @ 则丢弃，否则提交完整问题。"""
        ctx = self._buffers.get(key)
        if ctx is None:
            return
        self._clear(key)
        if not ctx["at_robot"]:
            logger.info("时间窗到期，首条未 @ 本机器人，丢弃缓冲: %s", key)
            return
        question = "".join(ctx["parts"]).strip()
        if not question:
            return
        meta = {"chatid": key[0], "userid": key[1]}
        loop = asyncio.get_running_loop()
        loop.create_task(self._safe_complete(question, ctx["first_req_id"], meta))

    async def _safe_complete(self, question: str, req_id: str, meta: dict) -> None:
        """安全调用聚合完成回调，异常仅记录不影响主流程。"""
        try:
            await self._on_complete(question, req_id, meta)
        except Exception as exc:
            logger.error("聚合完成回调异常: %s", exc)

    def _clear(self, key: tuple[str, str]) -> None:
        """清理缓冲并取消定时器。"""
        ctx = self._buffers.pop(key, None)
        if ctx and ctx["timer"] is not None:
            ctx["timer"].cancel()
