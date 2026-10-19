"""校验 NL2SQL 标注集：gold SQL 必须能在 dw 上跑通，且结果集非空。

这一步不能省。Execution Accuracy 是靠比较结果集判定的，如果 gold SQL 本身
返回空集，那么模型随便生成一条同样返回空的 SQL 就会被判为正确，
指标会虚高到没有意义。

用法：
    python -m eval.validate_gold [标注集路径]
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from eval._boot import init

# 必须先引导：配置里的密码走 ${oc.env:MYSQL_PASSWORD} 插值，
# 不加载 .env 会在建连时直接抛 InterpolationResolutionError
init()

from app.clients.mysql_client_manager import dw_mysql_client_manager  # noqa: E402
from app.repositories.mysql.dw_mysql_repository import DwMysqlRepository  # noqa: E402

DEFAULT_DATASET = Path("eval/datasets/nl2sql_gold.jsonl")


def load_dataset(path: Path) -> list[dict]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            items.append(json.loads(line))
    return items


async def validate(path: Path) -> int:
    items = load_dataset(path)
    print(f"标注集：{path}  共 {len(items)} 题\n")

    dw_mysql_client_manager.init()
    passed = 0
    problems: list[str] = []

    async with dw_mysql_client_manager.session_factory() as session:
        repo = DwMysqlRepository(session)

        for item in items:
            qid = item["qid"]
            sql = item["sql"]
            try:
                # 先用 EXPLAIN 验证语法与表/字段是否存在，不实际取数
                await repo.validate_sql(sql)
                rows = await repo.execute_sql(sql)
            except Exception as exc:  # noqa: BLE001 - 校验脚本需要捕获一切
                print(f"  [失败] {qid}  执行异常：{str(exc).splitlines()[0][:120]}")
                problems.append(f"{qid}: 执行异常")
                continue

            if not rows:
                print(f"  [失败] {qid}  结果集为空")
                problems.append(f"{qid}: 结果集为空")
                continue

            # 光非空还不够：聚合结果整行都是 NULL 时（例如筛选条件撞上一个
            # 业务上不存在的组合，SUM 返回 NULL 但仍有 1 行），模型随便生成
            # 一条同样返回 NULL 的 SQL 就会被判为一致，指标照样虚高。
            if all(v is None for v in rows[0].values()):
                print(f"  [失败] {qid}  结果非空但聚合值为 NULL，该题无判定意义")
                problems.append(f"{qid}: 聚合值为 NULL")
                continue

            preview = " | ".join(
                f"{k}={v}" for k, v in list(rows[0].items())[:2]
            )
            flag = "通过" if len(rows) >= 1 else "警告"
            print(f"  [{flag}] {qid}  {len(rows):>3} 行  首行：{preview[:60]}")
            passed += 1

    await dw_mysql_client_manager.close()

    print(f"\n通过 {passed}/{len(items)} 题")
    if problems:
        print("问题清单：")
        for p in problems:
            print(f"  - {p}")
        return 1
    return 0


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DATASET
    return asyncio.run(validate(path))


if __name__ == "__main__":
    sys.exit(main())
