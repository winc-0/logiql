import asyncio
import os
from pathlib import Path

import argparse

from dotenv import load_dotenv

# 本脚本要用三个中间件（MySQL / Qdrant / ES）加一个嵌入服务，配置里的密码与地址
# 全走 ${oc.env:...} 插值，不先加载 .env 会在建连时抛 InterpolationResolutionError。
# uvicorn 可以用 --env-file 带进来，命令行脚本不行，所以在这里显式加载一次。
ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=False)

# 宿主机开了全局代理时，连局域网中间件也会被塞进代理，表现为服务是好的但报
# proxy handshake failed。这里显式声明这几个地址不走代理。
_hosts = {"localhost", "127.0.0.1", "::1"}
for _var in ("MYSQL_HOST", "QDRANT_HOST", "ES_HOST", "EMBEDDING_HOST"):
    if os.getenv(_var):
        _hosts.add(os.getenv(_var).split(":")[0].strip())
_merged = sorted(_hosts | {h.strip() for h in (os.getenv("NO_PROXY") or "").split(",") if h.strip()})
os.environ["NO_PROXY"] = ",".join(_merged)
os.environ["no_proxy"] = ",".join(_merged)

from app.clients.embedding_client_manager import embedding_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import meta_mysql_client_manager, dw_mysql_client_manager
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.repositories.es.value_es_repository import ValueEsRepository
from app.repositories.mysql.dw_mysql_repository import DwMysqlRepository
from app.repositories.mysql.meta_mysql_repository import MetaMysqlRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.services.meta_knowledge_service import MetaKnowledgeService




async def build(config:Path):
    # 初始化meta客户端
    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()
    qdrant_client_manager.init()
    embedding_manager.init()
    es_client_manager.init()


    # 获取session
    async with meta_mysql_client_manager.session_factory() as meta_session,dw_mysql_client_manager.session_factory() as dw_session:
        # 创建repository对象
        meta_mysql_repository = MetaMysqlRepository(meta_session)
        dw_mysql_repository = DwMysqlRepository(dw_session)
        column_qdrant_repository=ColumnQdrantRepository(qdrant_client_manager.client)
        value_es_repository=ValueEsRepository(es_client_manager.client)
        metric_qdrant_repository=MetricQdrantRepository(qdrant_client_manager.client)
        # 创建service对象
        meta_knowledge_service = MetaKnowledgeService(
            meta_mysql_repository=meta_mysql_repository,
            dw_mysql_repository=dw_mysql_repository,
            column_qdrant_repository=column_qdrant_repository,
            embedding_client=embedding_manager.client,
            value_es_repository=value_es_repository,
            metric_qdrant_repository=metric_qdrant_repository
        )
        # 调用build函数
        await meta_knowledge_service.build(config)

    # 释放资源
    await meta_mysql_client_manager.close()
    await dw_mysql_client_manager.close()
    await qdrant_client_manager.close()
    await es_client_manager.close()

if __name__ == '__main__':
    # 1.构建命令行解析对象
    parser = argparse.ArgumentParser()

    # 2.配置参数
    parser.add_argument('-c', '--conf')

    # 3.获取参数
    args = parser.parse_args()
    # 4.转换为Path
    config_path:Path= Path(args.conf)

    asyncio.run(build(config_path))
