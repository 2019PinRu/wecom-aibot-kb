# -*- coding: utf-8 -*-
"""企微智能机器人长连接回调客户端：连接、订阅、心跳、断线重连、接收回调与 msgid 排重。"""
import asyncio
import json
import logging
import uuid
from collections import deque

import websockets

from utils.config import Config

logger = logging.getLogger(__name__)

# 企微长连接命令常量（以官方文档为准）
CMD_SUBSCRIBE = "aibot_subscribe"
CMD_MSG_CALLBACK = "aibot_msg_callback"
CMD_EVENT_CALLBACK = "aibot_event_callback"
CMD_PING = "ping"

# 日志展示的帧内容长度上限，防止超长消息刷屏
LOG_FRAME_LIMIT = 300


def _format_frame_payload(frame: dict, limit: int = LOG_FRAME_LIMIT) -> str:
    """将回调帧转 JSON 字符串并按长度截断，用于日志展示。

    参数：
        frame: 回调帧字典。
        limit: 截断长度。

    返回：
        截断后的 JSON 字符串。
    """
    text = json.dumps(frame, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "..."


class MsgDeduper:
    """基于有界内存的 msgid 排重器，防止网络重试导致重复处理。"""

    def __init__(self, max_size: int = 10000) -> None:
        """初始化排重器。

        参数：
            max_size: 最多缓存的 msgid 数量，超出后淘汰最旧的。
        """
        self._max_size = max_size
        self._seen: set = set()
        self._order: deque = deque()

    def is_duplicate(self, msgid: str) -> bool:
        """判断 msgid 是否重复；首次出现则登记。

        参数：
            msgid: 消息唯一标志。

        返回：
            True 表示已处理过；False 表示首次出现并已登记。
        """
        if msgid in self._seen:
            return True
        self._seen.add(msgid)
        self._order.append(msgid)
        self._prune()
        return False

    def _prune(self) -> None:
        """超过容量上限时淘汰最旧记录，避免内存膨胀。"""
        while len(self._order) > self._max_size:
            old = self._order.popleft()
            self._seen.discard(old)


class CallbackClient:
    """企微智能机器人长连接客户端，负责建连、订阅、接收回调与心跳保活。"""

    def __init__(self, config: Config, dispatcher, reply_client) -> None:
        """初始化长连接客户端。

        参数：
            config: 配置容器。
            dispatcher: 消息分流器（Dispatcher 实例）。
            reply_client: 回复客户端（ReplyClient 实例）。
        """
        self._config = config
        self._dispatcher = dispatcher
        self._reply_client = reply_client
        self._deduper = MsgDeduper(config.get("wecom.dedup_cache_size", 10000))
        self._stop_event = asyncio.Event()
        # 订阅成功事件：认证通过后才启动心跳（对齐官方 SDK 流程）
        self._auth_ok = asyncio.Event()
        self._subscribe_req_id = ""
        self._task: asyncio.Task | None = None
        self._ws = None

    async def start(self) -> None:
        """启动长连接客户端，在后台任务中运行连接循环。"""
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        logger.info("长连接客户端已启动")

    async def stop(self) -> None:
        """停止长连接客户端。"""
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("长连接客户端已停止")

    async def _run_loop(self) -> None:
        """长连接主循环：连接、订阅、接收、心跳；断线按指数退避自动重连。"""
        backoff = 1
        ws_url = self._config.get("wecom.ws_url")
        bot_id = self._config.get("wecom.bot_id")
        secret = self._config.get("wecom.bot_secret")
        if not bot_id or not secret:
            logger.error("未配置 wecom.bot_id / wecom.bot_secret，无法建立长连接")
            return
        reconnect_max = self._config.get("wecom.reconnect_max_seconds", 30)
        while not self._stop_event.is_set():
            self._auth_ok.clear()
            self._subscribe_req_id = ""
            try:
                # 关闭 websockets 内置协议层 ping，改用应用层 aibot ping 命令保活。
                # 企微服务器不回应当前库默认的每条 20 秒协议 ping，会触发
                # keepalive ping timeout / incorrect masking 断开。
                async with websockets.connect(ws_url, ping_interval=None) as ws:
                    self._ws = ws
                    self._reply_client.bind(ws)
                    await self._subscribe(ws)
                    backoff = 1
                    await self._receive_loop(ws)
            except (websockets.ConnectionClosed, OSError) as exc:
                logger.error("长连接断开，准备重连: %s", exc)
            except Exception as exc:
                logger.error("长连接异常，准备重连: %s", exc)
            finally:
                self._ws = None
                self._reply_client.bind(None)
            if not self._stop_event.is_set():
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, reconnect_max)

    async def _subscribe(self, ws) -> None:
        """发送订阅请求 aibot_subscribe 完成身份校验（req_id 带命令前缀便于识别响应）。"""
        # 官方 SDK 约定：订阅响应的 req_id 以 aibot_subscribe 前缀开头，用于识别认证回包
        req_id = f"{CMD_SUBSCRIBE}_{uuid.uuid4().hex}"
        self._subscribe_req_id = req_id
        frame = {
            "cmd": CMD_SUBSCRIBE,
            "headers": {"req_id": req_id},
            "body": {
                "bot_id": self._config.get("wecom.bot_id"),
                "secret": self._config.get("wecom.bot_secret"),
            },
        }
        await ws.send(json.dumps(frame, ensure_ascii=False))
        logger.info("已发送订阅请求 req_id=%s", req_id)

    async def _receive_loop(self, ws) -> None:
        """接收服务端推送，并启动心跳任务保持连接活跃。"""
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))
        try:
            while not self._stop_event.is_set():
                raw = await ws.recv()
                await self._on_message(raw)
        finally:
            heartbeat_task.cancel()

    async def _heartbeat_loop(self, ws) -> None:
        """认证成功后周期发送带 headers 的 ping 心跳（官方建议间隔 30 秒）。"""
        # 等待订阅成功回包后再启动心跳（对齐官方 SDK：认证通过才开始保活）
        await self._auth_ok.wait()
        interval = self._config.get("wecom.heartbeat_seconds", 30)
        while not self._stop_event.is_set():
            await asyncio.sleep(interval)
            try:
                await ws.send(
                    json.dumps(
                        {"cmd": CMD_PING, "headers": {"req_id": f"ping_{uuid.uuid4().hex}"}},
                        ensure_ascii=False,
                    )
                )
                logger.debug("心跳已发送")
            except Exception as exc:
                logger.error("心跳发送失败: %s", exc)
                return

    async def _on_message(self, raw: str) -> None:
        """解析并分发服务端推送帧。

        参数：
            raw: 原始 JSON 字符串帧。
        """
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("回调帧 JSON 解析失败: %s", exc)
            return
        cmd = frame.get("cmd")
        body = frame.get("body") or {}
        req_id = (frame.get("headers") or {}).get("req_id") or ""
        logger.info("【收】%s", _format_frame_payload(frame))
        if cmd == CMD_MSG_CALLBACK:
            await self._handle_msg_callback(body, req_id)
        elif cmd == CMD_EVENT_CALLBACK:
            event = body.get("event") or {}
            logger.info("收到事件回调: %s", event.get("eventtype", "unknown"))
        elif cmd is None:
            # 官方协议：订阅响应/心跳 ack/回复回执均为无 cmd 帧，靠 headers.req_id 前缀识别
            await self._handle_response_frame(frame)
        else:
            logger.debug("收到未处理帧 cmd=%s", cmd)

    async def _handle_response_frame(self, frame: dict) -> None:
        """处理无 cmd 的响应帧：订阅响应、心跳 ack 与回复回执。

        参数：
            frame: 响应帧字典。
        """
        req_id = (frame.get("headers") or {}).get("req_id") or ""
        errcode = frame.get("errcode")
        if req_id == self._subscribe_req_id or req_id.startswith(CMD_SUBSCRIBE):
            if errcode == 0:
                self._auth_ok.set()
                logger.info("【鉴权】订阅成功，机器人接入正常: %s", frame.get("errmsg", "ok"))
            else:
                logger.error(
                    "【鉴权】订阅失败 errcode=%s errmsg=%s，请检查 bot_id / secret 配置",
                    errcode,
                    frame.get("errmsg"),
                )
            return
        if req_id.startswith("ping_"):
            logger.debug("收到心跳 ack")
            return
        logger.info("收到回执帧: %s", _format_frame_payload(frame))

    async def _handle_msg_callback(self, body: dict, req_id: str) -> None:
        """处理消息回调：msgid 排重后转 dispatcher 分流。

        参数：
            body: 消息回调 body。
            req_id: headers.req_id，回复时透传。
        """
        msgid = body.get("msgid") or ""
        if not msgid:
            logger.warning("消息回调缺少 msgid，忽略")
            return
        if self._deduper.is_duplicate(msgid):
            logger.info("msgid 重复，跳过处理: %s", msgid)
            return
        await self._dispatcher.dispatch(body, req_id)
