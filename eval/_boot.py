"""
评测子包的引导模块。

职责（只做四件事，不做业务）：
1. 把项目根目录加入 sys.path，保证 `from app.xxx import ...` 可用，
   无论脚本是 `python eval/run_eval.py` 还是 `python -m eval.run_eval` 启动。
2. 把 stdout/stderr 强制切换为 UTF-8，避免 Windows 控制台默认 GBK 时中文/符号报 UnicodeEncodeError。
3. 显式加载项目根目录下的 .env（而不是依赖当前工作目录）。
4. 把中间件地址加进 NO_PROXY，并压掉会刷屏的 DEBUG 日志。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

# 项目根目录（eval/ 的上一级）
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

_initialized = False
# 已加入 NO_PROXY 的主机名，供自检打印
no_proxy_hosts: list[str] = []


def _host_of(value: str) -> str:
    """从 "http://192.168.217.3:9200" / "192.168.217.3" / "host:6333" 里取出主机名。"""
    if not value:
        return ""
    if "//" in value:
        return urlparse(value).hostname or ""
    return value.split(":")[0].strip()


def relax_proxy_for_infra() -> list[str]:
    """
    把项目依赖的中间件加进 NO_PROXY。

    为什么必须做：
        宿主机上装了代理软件（Clash / v2rayN 之类）并设置了全局代理时，
        连局域网内的中间件也会被塞进代理。表现为服务明明是好的，
        HTTP 客户端却报 proxy handshake failed，非常难排查。
        这里显式声明"这几个地址不要走代理"。

    :return: 本次追加/确认的主机名列表
    """
    hosts = {"localhost", "127.0.0.1", "::1"}
    for var in ("MYSQL_HOST", "QDRANT_HOST", "ES_HOST", "EMBEDDING_HOST"):
        host = _host_of(os.getenv(var) or "")
        if host:
            hosts.add(host)

    existing = {h.strip() for h in (os.getenv("NO_PROXY") or "").split(",") if h.strip()}
    merged = sorted(existing | hosts)
    value = ",".join(merged)
    # 大小写两种都设，不同的 HTTP 客户端读的变量名不一样
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value

    global no_proxy_hosts
    no_proxy_hosts = sorted(hosts)
    return no_proxy_hosts


def apply_log_level(level: str | None = None) -> str:
    """
    压住评测过程中过高等级的日志。

    想恢复完整日志：设环境变量 EVAL_LOG_LEVEL=DEBUG，或调用 apply_log_level("DEBUG")。

    :return: 实际生效的级别名
    """
    import logging

    target = (level or os.getenv("EVAL_LOG_LEVEL") or "WARNING").upper()
    numeric = getattr(logging, target, logging.WARNING)

    # 先导入项目的 logger 模块，触发它配置 logger 的行为，再把级别改回来
    try:
        import app.core.log  # noqa: F401
    except Exception:
        pass

    # 项目用的是 loguru，它不走标准 logging，得单独处理。
    # 且不处理的话，每道题都会把整条 SQL 打出来，40 题 x 6 组会淹没进度信息。
    try:
        from loguru import logger as loguru_logger

        loguru_logger.remove()
        loguru_logger.add(sys.stderr, level=target,
                          format="{time:HH:mm:ss} | {level} | {message}")
    except Exception:
        pass

    logging.getLogger().setLevel(numeric)
    # 这些库比业务日志还吵
    for name in ("httpx", "httpcore", "urllib3", "openai", "langchain",
                 "langchain_openai", "elastic_transport", "elasticsearch",
                 "qdrant_client", "asyncio"):
        logging.getLogger(name).setLevel(max(numeric, logging.WARNING))
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    return target


def detect_proxy() -> dict[str, str]:
    """返回当前进程可见的代理设置，供自检提示用。"""
    found: dict[str, str] = {}
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                 "http_proxy", "https_proxy", "all_proxy"):
        value = os.getenv(name)
        if value:
            found[name] = value
    return found


def init() -> Path:
    """
    初始化评测运行环境。幂等，重复调用无副作用。

    :return: 项目根目录 Path
    """
    global _initialized
    if _initialized:
        return PROJECT_ROOT

    # 1. sys.path —— 让项目内的绝对导入生效
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)

    # 2. 控制台编码 —— Windows 下默认 GBK，中文表格会崩
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    # 3. .env —— 用绝对路径加载，保证从任意目录启动都能读到配置
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_file, override=False)
        except Exception:
            pass

    # 4. 代理绕过（必须在建连之前）
    relax_proxy_for_infra()

    # 5. 压掉 DEBUG 刷屏
    apply_log_level()

    _initialized = True
    return PROJECT_ROOT
