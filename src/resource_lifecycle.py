"""运行时网络资源的显式回收辅助函数。"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any


logger = logging.getLogger(__name__)

_RESOURCE_ATTRIBUTES = {
    "auth",
    "activities",
    "health",
    "api_client",
    "http_client",
    "session",
    "_api",
    "_auth",
    "_client",
    "_activity_provider",
    "_health_provider",
    "_http_client",
    "_injected_client",
    "_session",
    "_storage",
}


def close_runtime_resources(*roots: Any) -> None:
    """关闭对象树里已创建的 HTTP client/session 等可关闭资源。

    只遍历已存在的实例属性，不访问可能触发网络或懒初始化的 property。
    多次调用安全；单个对象的关闭失败不会阻断其余资源回收。
    """
    stack = list(roots)
    seen: set[int] = set()
    closeables: list[Any] = []

    while stack:
        value = stack.pop()
        if value is None or isinstance(value, (str, bytes, int, float, bool)):
            continue
        marker = id(value)
        if marker in seen:
            continue
        seen.add(marker)

        if isinstance(value, Mapping):
            stack.extend(value.values())
            continue
        if isinstance(value, Iterable) and not hasattr(value, "__dict__"):
            stack.extend(value)
            continue

        close = getattr(value, "close", None)
        if callable(close):
            closeables.append(value)

        attributes = getattr(value, "__dict__", {})
        for name, child in attributes.items():
            if name in _RESOURCE_ATTRIBUTES:
                stack.append(child)

    for resource in reversed(closeables):
        try:
            resource.close()
        except Exception as exc:  # pragma: no cover - 第三方关闭行为不可控
            logger.warning("关闭运行时资源失败 (%s): %s", type(resource).__name__, exc)
