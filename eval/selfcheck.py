"""
评测套件的离线自检。

不调用大模型、不访问网络（除加载配置外），只验证：
    1. 结果集比较函数的各种边界行为
    2. 显著性检验的统计性质
    3. 六组策略的图结构与节点组成
    4. 标注集自身的数据完整性

用法：
    python -m eval.selfcheck
"""
from __future__ import annotations

import sys

import eval._boot as boot

boot.init()

from eval.metrics import (  # noqa: E402
    classify_case,
    paired_bootstrap_p_value,
    result_sets_equal,
)
from eval.strategies import (  # noqa: E402
    STRATEGY_ORDER,
    build_graph,
    node_count,
)

DATASET = boot.PROJECT_ROOT / "eval" / "datasets" / "nl2sql_gold.jsonl"

# 期望的节点组成：full 必须与业务图 app/agent/graph.py 的 12 个节点一致
EXPECTED_NODES = {
    "llm_only": 5,
    "col_recall": 8,
    "col_val_recall": 8,
    "full_recall": 8,
    "llm_filter": 10,
    "full": 12,
}

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "通过" if condition else "失败"
    print(f"  [{mark}] {label}" + (f"  {detail}" if detail else ""))
    if not condition:
        _failures.append(label)


# --------------------------------------------------------------------------- #
def test_result_sets_equal() -> None:
    print("\n[1] 结果集比较")

    a = [{"品类": "手机数码", "销售额": 100.0}, {"品类": "服饰", "销售额": 50.0}]
    b = [{"品类": "服饰", "销售额": 50.0}, {"品类": "手机数码", "销售额": 100.0}]
    check("行序不同应判为一致（无序口径）",
          result_sets_equal(a, b))
    check("行序不同应判为不一致（有序口径）",
          not result_sets_equal(a, b, ordered=True))

    check("行数不同应判为不一致",
          not result_sets_equal(a, a[:1]))

    # 列别名不同但值相同：必须判为一致，否则指标毫无意义
    c = [{"category": "手机数码", "total": 100.0}, {"category": "服饰", "total": 50.0}]
    check("列别名不同但值相同应判为一致", result_sets_equal(a, c))

    # 浮点累加噪声
    d = [{"品类": "手机数码", "销售额": 100.00002}, {"品类": "服饰", "销售额": 50.0}]
    check("微小浮点差异应判为一致", result_sets_equal(a, d))
    e = [{"品类": "手机数码", "销售额": 100.5}, {"品类": "服饰", "销售额": 50.0}]
    check("真实数值差异应判为不一致", not result_sets_equal(a, e))

    # 列数不同
    f = [{"品类": "手机数码"}, {"品类": "服饰"}]
    check("列数不同应判为不一致", not result_sets_equal(a, f))

    # 两边都为空：标注集不该出现，但函数行为要有定义
    check("两边空集应判为一致", result_sets_equal([], []))

    # 重复行：贪心排序法也要能正确处理
    g = [{"x": 1}, {"x": 1}, {"x": 2}]
    h = [{"x": 2}, {"x": 1}, {"x": 1}]
    check("含重复行且乱序应判为一致", result_sets_equal(g, h))

    i = [{"x": 1}, {"x": 1}]
    check("重复行数量不同应判为不一致", not result_sets_equal(g, i))


def test_classify() -> None:
    print("\n[2] 单题判定")
    gold = [{"销售额": 100.0}]

    ex, exo, reason = classify_case(gold, [{"total": 100.0}], None)
    check("结果一致 → EX=1", ex == 1 and exo == 1 and reason == "")

    ex, exo, reason = classify_case(gold, [{"total": 200.0}], None)
    check("结果不一致 → EX=0 且归因", ex == 0 and reason == "结果集不一致")

    ex, exo, reason = classify_case(gold, None, "SQL 执行失败")
    check("执行失败 → EX=0 且归因", ex == 0 and reason == "SQL 执行失败")

    ex, exo, reason = classify_case(gold, [], None)
    check("结果集为空 → EX=0", ex == 0 and reason == "结果集为空")


def test_bootstrap() -> None:
    print("\n[3] 配对 bootstrap 显著性检验")

    same = [1.0, 0.0, 1.0, 1.0, 0.0] * 4
    p_same = paired_bootstrap_p_value(same, list(same), n_boot=1000)
    check("完全相同 → p 值不显著", p_same > 0.05, f"p={p_same:.3f}")

    weak = [1.0, 0.0, 1.0, 1.0, 0.0] * 4
    better = [1.0, 1.0, 1.0, 1.0, 1.0] * 4
    p_big = paired_bootstrap_p_value(weak, better, n_boot=1000)
    check("差异明显 → p 值显著", p_big < 0.05, f"p={p_big:.3f}")

    # 20 题里只多对 1 题，不该显著
    base = [1.0] * 10 + [0.0] * 10
    slight = [1.0] * 11 + [0.0] * 9
    p_small = paired_bootstrap_p_value(base, slight, n_boot=1000)
    check("20 题里仅多对 1 题 → 不显著", p_small > 0.05, f"p={p_small:.3f}")

    check("空输入 → p=1.0", paired_bootstrap_p_value([], []) == 1.0)
    check("长度不等 → p=1.0", paired_bootstrap_p_value([1.0], [1.0, 0.0]) == 1.0)


def test_graphs() -> None:
    print("\n[4] 六组策略的图结构")

    for name in STRATEGY_ORDER:
        count = node_count(name)
        expected = EXPECTED_NODES[name]
        check(f"{name} 节点数为 {expected}", count == expected, f"实际 {count}")

    # 逐层累加：每加一层，节点数不能减少
    counts = [node_count(n) for n in STRATEGY_ORDER]
    check("节点数随层数单调不减（除基线切换外）",
          counts[1] <= counts[2] <= counts[3] <= counts[4] <= counts[5],
          f"{counts}")

    # 完整链路必须与生产图一致
    from app.agent.graph import graph as production_graph

    prod_nodes = {
        n for n in production_graph.get_graph().nodes
        if n not in ("__start__", "__end__")
    }
    eval_nodes = {
        n for n in build_graph("full").get_graph().nodes
        if n not in ("__start__", "__end__")
    }
    check("评测的 full 图与生产图节点一致", prod_nodes == eval_nodes,
          f"差异：{prod_nodes ^ eval_nodes}" if prod_nodes != eval_nodes else "")


def test_dataset() -> None:
    print("\n[5] 标注集完整性")

    import json

    items = [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    check("题量不少于 30", len(items) >= 30, f"{len(items)} 题")

    qids = [i["qid"] for i in items]
    check("qid 无重复", len(set(qids)) == len(qids))

    required = {"qid", "level", "kind", "question", "sql"}
    missing = [i["qid"] for i in items if not required.issubset(i.keys())]
    check("每题字段齐全", not missing, f"缺字段：{missing}")

    levels = {i["level"] for i in items}
    check("难度档位齐全", levels == {"A", "B", "C", "D", "E", "F"}, f"{sorted(levels)}")

    from collections import Counter

    dist = Counter(i["level"] for i in items)
    print(f"      难度分布：{dict(sorted(dist.items()))}")

    empty_q = [i["qid"] for i in items if not i["sql"].strip()]
    check("无空 SQL", not empty_q)


def main() -> int:
    print("NL2SQL 评测套件自检")
    test_result_sets_equal()
    test_classify()
    test_bootstrap()
    test_graphs()
    test_dataset()

    print()
    if _failures:
        print(f"自检未通过，{len(_failures)} 项失败：")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("自检全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
