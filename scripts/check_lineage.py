"""校验字段血缘、用评测集反向检查覆盖率，并支持溯源和影响分析。不需要连数据库。

用法（在项目根目录）：
    python scripts/check_lineage.py                                   # 校验并写报告
    python scripts/check_lineage.py --column dws_outbound_daily.daily_outbound_amount
    python scripts/check_lineage.py --impact dim_cargo.cargo_category

返回值：血缘自检有问题、或评测集有字段追不到源系统时返回 1。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.governance.dq import load_rules  # noqa: E402
from app.governance.lineage import (columns_in_sql, format_tree, gold_coverage, load_lineage,  # noqa: E402
                                    load_schema, render_report, trace_down, trace_up, validate)

DDL_FILES = [ROOT / "docker" / "mysql" / "dw.sql", ROOT / "docker" / "mysql" / "dw_large.sql"]
GOLD = ROOT / "eval" / "datasets" / "nl2sql_gold.jsonl"


def _load_gold() -> list[dict]:
    return [json.loads(line) for line in GOLD.read_text(encoding="utf-8").splitlines() if line.strip()]


def _check_ref(ref: str, schema: dict[str, list[str]]) -> None:
    table, _, col = ref.partition(".")
    if table not in schema or col not in schema[table]:
        raise SystemExit(f"字段 {ref} 不存在，写成「表名.字段名」")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="校验字段血缘")
    ap.add_argument("--lineage", type=Path, default=ROOT / "conf" / "lineage.yaml")
    ap.add_argument("--rules", type=Path, default=ROOT / "conf" / "dq_rules.yaml")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "governance" / "lineage_report.md")
    ap.add_argument("--column", help="往上追：这个字段从哪来")
    ap.add_argument("--impact", help="往下追：改这个字段会影响哪些字段和评测题")
    args = ap.parse_args()

    schema = load_schema(DDL_FILES)
    lineage = load_lineage(args.lineage)
    gold = _load_gold()

    if args.column:
        _check_ref(args.column, schema)
        print("\n".join(format_tree(trace_up(lineage, args.column), lineage.systems)))
        return 0

    if args.impact:
        _check_ref(args.impact, schema)
        affected = trace_down(lineage, args.impact)
        print(f"{args.impact} 的下游字段（{len(affected)} 个）：")
        for ref in affected:
            print(f"  - {ref}")
        hit = [g["qid"] for g in gold if {args.impact, *affected} & columns_in_sql(g["sql"], schema)]
        print(f"\n受影响的评测题（{len(hit)} 道）：{', '.join(hit) if hit else '无'}")
        return 0

    rule_ids = {r.id for r in load_rules(args.rules)}
    problems = validate(lineage, schema, rule_ids)
    cov = gold_coverage(lineage, schema, gold)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_report(lineage, schema, gold, problems, generated_at=stamp), encoding="utf-8")

    total = sum(len(c) for c in schema.values())
    print(f"表 {len(schema)} 张，字段 {total} 个；加工关系 {len(lineage.edges())} 条")
    print(f"评测集用到字段 {len(cov['columns'])} 个，可追溯 {len(cov['traceable'])} 个；"
          f"字段全部可追溯的题 {len(cov['questions_ok'])}/{cov['questions_total']}")
    if problems:
        print(f"\n血缘自检发现 {len(problems)} 个问题：")
        for p in problems:
            print(f"  - {p}")
    if cov["missing"]:
        print(f"\n评测集里追不到源系统的字段：{', '.join(cov['missing'])}")
    print(f"\n报告已写入 {args.out}")
    return 1 if problems or cov["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
