"""运行数仓数据质量规则，输出报告。

用法（在项目根目录）：
    python scripts/dq_check.py                      # 跑全部规则
    python scripts/dq_check.py --only dws_          # 只跑 id 以 dws_ 开头的规则
    python scripts/dq_check.py --out other.md       # 报告写到别处

数据库连接读取 .env（和评测脚本一样）。全部是只读查询。
返回值：出现新问题、已知缺陷未检出或执行出错时返回 1，否则返回 0。
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval._boot import init  # noqa: E402

init()

from app.clients.mysql_client_manager import dw_mysql_client_manager  # noqa: E402
from app.core.sql_guard import assert_read_only  # noqa: E402
from app.governance.dq import load_rules, render_console, render_markdown, run_rules  # noqa: E402
from app.repositories.mysql.dw_mysql_repository import DwMysqlRepository  # noqa: E402


async def main_async(args) -> int:
    rules = load_rules(args.rules)
    if args.only:
        rules = [r for r in rules if r.id.startswith(args.only)]
    print(f"规则文件：{args.rules}  本次运行 {len(rules)} 条\n")

    dw_mysql_client_manager.init()
    try:
        async with dw_mysql_client_manager.session_factory() as session:
            repo = DwMysqlRepository(session)

            async def query(sql: str) -> list[dict]:
                assert_read_only(sql)  # 规则文件里手写的对账 SQL 也要过只读守卫
                return await repo.execute_sql(sql)

            results = await run_rules(rules, query)
    finally:
        await dw_mysql_client_manager.close()

    print(render_console(results))
    if not args.only:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        args.out.write_text(render_markdown(results, generated_at=stamp), encoding="utf-8")
        print(f"\n报告已写入 {args.out}")
    return 1 if any(r.unexpected for r in results) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="运行数仓数据质量规则")
    ap.add_argument("--rules", type=Path, default=ROOT / "conf" / "dq_rules.yaml")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "governance" / "dq_report.md")
    ap.add_argument("--only", help="只跑 id 以此开头的规则（不写报告）")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
