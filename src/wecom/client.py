# -*- coding: utf-8 -*-
"""企微被动回复与主动推送客户端，承载消息发送与会话并发控制。"""
import asyncio
import json
import logging
import uuid

logger = logging.getLogger(__name__)

# 企微回复/推送命令常量（以官方文档为准）
CMD_RESPOND_MSG = "aibot_respond_msg"
CMD_SEND_MSG = "aibot_send_msg"

# 日志展示的帧内容长度上限，防止超长消息刷屏
LOG_FRAME_LIMIT = 300


def _format_frame_payload(frame: dict, limit: int = LOG_FRAME_LIMIT) -> str:
    """将发送帧转 JSON 字符串并按长度截断，用于日志展示。

    参数：
        frame: 发送帧字典。
        limit: 截断长度。

    返回：
        截断后的 JSON 字符串。
    """
    text = json.dumps(frame, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "..."


class ReplyClient:
    """企微回复客户端：通过长连接发送被动回复与主动推送。"""

    def __init__(self, max_concurrency: int = 3) -> None:
        """初始化回复客户端。

        参数：
            max_concurrency: 同一会话同时处理的消息数上限（企微硬限制为 3）。
        """
        self._ws = None
        self._send_lock = asyncio.Lock()
        self._max_concurrency = max_concurrency
        self._session_slots: dict[str, asyncio.Semaphore] = {}

    def bind(self, ws) -> None:
        """绑定当前长连接用于发送；断线时传入 None 解除绑定。

        参数：
            ws: websockets 连接对象或 None。
        """
        self._ws = ws

    def acquire_slot(self, chatid: str, userid: str) -> asyncio.Semaphore:
        """获取会话并发信号量，控制同一会话在途消息不超过 3 条。

        参数：
            chatid: 群聊会话 id（单聊为空）。
            userid: 消息发送者 userid。

        返回：
            会话对应的信号量（由调用方 acquire/release）。
        """
        key = f"{chatid or 'single'}:{userid}"
        if key not in self._session_slots:
            self._session_slots[key] = asyncio.Semaphore(self._max_concurrency)
        return self._session_slots[key]

    async def _send_frame(self, cmd: str, req_id: str, body: dict) -> bool:
        """发送一条 JSON 帧到长连接。

        参数：
            cmd: 命令类型。
            req_id: 请求唯一标识。
            body: 消息体。

        返回：
            True 发送成功；False 连接不可用或发送失败。
        """
        if self._ws is None:
            logger.error("长连接未就绪，无法发送 %s", cmd)
            return False
        frame = {"cmd": cmd, "headers": {"req_id": req_id}, "body": body}
        payload = json.dumps(frame, ensure_ascii=False)
        try:
            async with self._send_lock:
                await self._ws.send(payload)
            logger.info("【发】%s", _format_frame_payload(frame))
            return True
        except Exception as exc:
            logger.error("发送 %s 失败: %s", cmd, exc)
            return False

    async def reply(self, req_id: str, content: str) -> bool:
        """被动回复文本消息（aibot_respond_msg），透传回调 req_id。

        参数：
            req_id: 消息回调 headers.req_id，须透传。
            content: 回复文本内容。

        返回：
            True 发送成功；False 失败。
        """
        # 官方回复普通消息：普通文本以 stream + finish=true 一次性发送完成
        body = {
            "msgtype": "stream",
            "stream": {"id": uuid.uuid4().hex, "finish": True, "content": content},
        }
        return await self._send_frame(CMD_RESPOND_MSG, req_id, body)

    async def push_markdown(self, chatid: str, content: str, chat_type: int = 0) -> bool:
        """主动向会话推送 markdown 消息（aibot_send_msg，官方仅支持 markdown/template_card）。

        参数：
            chatid: 目标会话 id（单聊填 userid，群聊填 chatid）。
            content: 推送内容（markdown 格式）。
            chat_type: 会话类型，1 单聊 / 2 群聊 / 0 兼容（官方建议显式指定）。

        返回：
            True 发送成功；False 失败。
        """
        body = {
            "chatid": chatid,
            "chat_type": chat_type,
            "msgtype": "markdown",
            "markdown": {"content": content},
        }
        return await self._send_frame(CMD_SEND_MSG, uuid.uuid4().hex, body)
