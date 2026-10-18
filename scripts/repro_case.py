"""临时排查脚本：在指定策略下复现单题链路，打印完整异常堆栈。

用于定位 OperationalError 的真实来源 —— 只有当连 SQL 语句本身都能看到时，
才能判断它究竟是「基础设施抖动」还是「模型生成了低效/非法 SQL」。
后者属于策略的真实失败，不该被评测当成基础设施错误放过。

用法：
    python scripts/repro_case.py c01 --strategy llm_only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from pathlib import Path

import eval._boot as boot

boot.init()

from app.agent.context import DataAgentContext  # noqa: E402
from app.clients.embedding_client_manager import embedding_manager  # noqa: E402
from app.clients.es_client_manager import es_client_manager  # noqa: E402
from app.clients.mysql_client_manager import (  # noqa: E402
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager  # noqa: E402
from app.repositories.es.value_es_repository import ValueEsRepository  # noqa: E402
from app.repositories.mysql.dw_mysql_repository import DwMysqlRepository  # noqa: E402
from app.repositories.mysql.meta_mysql_repository import MetaMysqlRepository  # noqa: E402
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository  # noqa: E402
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository  # noqa: E402

from eval.strategies import build_graph, make_initial_state  # noqa: E402

DATASET = boot.PROJECT_ROOT / "eval" / "datasets" / "nl2sql_gold.jsonl"


def load_case(qid: str) -> dict:
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            if item["qid"] == qid:
                return item
    raise SystemExit(f"未找到题目 {qid}")


async def main(qid: str, strategy: str) -> None:
    item = load_case(qid)
    print(f"题目 [{qid}]：{item['question']}")
    print(f"策略：{strategy}")
    print("-" * 70)

    embedding_manager.init()
    qdrant_client_manager.init()
    es_client_manager.init()
    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()

    async with meta_mysql_client_manager.session_factory() as meta_session, \
            dw_mysql_client_manager.session_factory() as dw_session:
        runtime = DataAgentContext(
            embedding_client=embedding_manager.client,
            column_qdrant_repository=ColumnQdrantRepository(qdrant_client_manager.client),
            metric_qdrant_repository=MetricQdrantRepository(qdrant_client_manager.client),
            value_es_repository=ValueEsRepository(es_client_manager.client),
            meta_mysql_repository=MetaMysqlRepository(meta_session),
            dw_mysql_repository=DwMysqlRepository(dw_session),
        )

        graph = build_graph(strategy)
        state = make_initial_state(item["question"])
        last: dict = {}

        try:
            async for chunk in graph.astream(state, context=runtime, stream_mode="values"):
                last = chunk
        except Exception:
            traceback.print_exc()
            print("=" * 70)
            print("异常发生时的 state 快照：")
            for key in ("keywords", "sql", "table_infos", "metric_infos", "column_infos"):
                if key in last and last[key]:
                    val = last[key]
                    text = json.dumps(val, ensure_ascii=False, default=str)
                    print(f"  {key}: {text[:800]}")
        else:
            print("链路成功完成。")
            print("生成的 SQL：")
            print(" ", last.get("sql", "(无)"))
            rows = await DwMysqlRepository(dw_session).execute_sql(last.get("sql") or "")
            print(f"执行结果：{len(rows)} 行")

    await qdrant_client_manager.close()
    await es_client_manager.close()
    await meta_mysql_client_manager.close()
    await dw_mysql_client_manager.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("qid")
    ap.add_argument("--strategy", default="llm_only")
    args = ap.parse_args()
    asyncio.run(main(args.qid, args.strategy))
