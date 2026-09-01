# -*- coding: utf-8 -*-
"""配置加载模块：读取 config.yaml 与默认配置合并，密钥支持环境变量覆盖。"""
import logging
import os
from copy import deepcopy

import yaml

# 日志器
logger = logging.getLogger(__name__)

# 默认配置：字段缺失时兜底，保证服务可启动
DEFAULT_CONFIG = {
    "wecom": {"bot_id": "", "bot_secret": ""},
    "reply": {
        "prefix": "[AI自动回复]：",
        "no_answer": "抱歉，暂时没有找到相关答案，已记录您的问题，管理员会尽快补充。",
    },
    "aggregate": {"window_seconds": 30, "max_queue": 3},
    "retriever": {"score_threshold": 0.3, "top_k": 5},
    "storage": {"db_path": "data/kb.db", "log_dir": "logs"},
    "web": {"host": "0.0.0.0", "port": 8080},
}

# 密钥类配置的环境变量映射：key_path -> 环境变量名
_ENV_OVERRIDES = {
    "wecom.bot_id": "WECOM_BOT_ID",
    "wecom.bot_secret": "WECOM_BOT_SECRET",
}


def _deep_merge(base: dict, overlay: dict) -> None:
    """将 overlay 递归合并进 base，覆盖已有键。"""
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _apply_env_overrides(config: dict) -> None:
    """用环境变量覆盖密钥类配置项。"""
    for key_path, env_name in _ENV_OVERRIDES.items():
        env_value = os.environ.get(env_name)
        if env_value:
            _set_by_path(config, key_path, env_value)


def _set_by_path(config: dict, key_path: str, value) -> None:
    """按点分键路径写入配置值。"""
    keys = key_path.split(".")
    node = config
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value


def _get_by_path(config: dict, key_path: str, default=None):
    """按点分键路径读取配置值，键不存在时返回默认值。"""
    node = config
    for key in key_path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def load_config(config_path: str | None = None) -> dict:
    """加载配置文件并与默认配置合并，返回完整配置字典。

    参数：
        config_path: 配置文件路径；为 None 时使用默认路径 config.yaml。

    返回：
        合并后的配置字典。
    """
    config = deepcopy(DEFAULT_CONFIG)
    path = config_path or "config.yaml"
    try:
        with open(path, encoding="utf-8") as f:
            file_config = yaml.safe_load(f) or {}
        _deep_merge(config, file_config)
        logger.info("配置文件加载成功: %s", path)
    except FileNotFoundError:
        logger.warning("配置文件不存在，使用默认配置: %s", path)
    except yaml.YAMLError as exc:
        logger.error("配置文件解析失败，使用默认配置: %s", exc)
    _apply_env_overrides(config)
    return config


class Config:
    """配置容器：加载后按点分键路径读取配置项。"""

    def __init__(self, config_path: str | None = None) -> None:
        """初始化并加载配置。"""
        self._data = load_config(config_path)

    def get(self, key_path: str, default=None):
        """读取配置项，键不存在时返回默认值。"""
        return _get_by_path(self._data, key_path, default)

    @property
    def data(self) -> dict:
        """返回完整配置字典。"""
        return deepcopy(self._data)
