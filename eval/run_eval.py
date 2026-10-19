"""
NL2SQL 评测主程序。

跑法：对标注集里每一道题，让六组策略各自走一遍完整链路，
把生成的 SQL 在数据仓库上真实执行，与 gold SQL 的结果集比对。

用法：
    python -m eval.run_eval                          # 跑全部 6 组 x 40 题
    python -m eval.run_eval --strategies full        # 只跑完整链路
    python -m eval.run_eval --limit 5                # 只跑前 5 题（快速冒烟）
    python -m eval.run_eval --strategies llm_only,full --limit 10
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import eval._boot as boot

boot.init()

from app.clients.embedding_client_manager import embedding_manager  # noqa: E402
from app.clients.es_client_manager import es_client_manager  # noqa: E402
from app.clients.mysql_client_manager import (  # noqa: E402
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager  # noqa: E402
from app.agent.context import DataAgentContext  # noqa: E402
from app.repositories.es.value_es_repository import ValueEsRepository  # noqa: E402
from app.repositories.mysql.dw_mysql_repository import DwMysqlRepository  # noqa: E402
from app.repositories.mysql.meta_mysql_repository import MetaMysqlRepository  # noqa: E402
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository  # noqa: E402
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository  # noqa: E402

from eval.metrics import (  # noqa: E402
    classify_case,
    fmt_delta,
    fmt_p,
    fmt_pct,
    mean,
    paired_bootstrap_p_value,
)
from eval.strategies import (  # noqa: E402
    STRATEGY_DELTA,
    STRATEGY_ORDER,
    build_graph,
    make_initial_state,
)

DEFAULT_DATASET = boot.PROJECT_ROOT / "eval" / "datasets" / "nl2sql_gold.jsonl"
REPORT_DIR = boot.PROJECT_ROOT / "eval" / "reports"

# 模型偶尔会把 SQL 包在 markdown 代码块里
_MARKDOWN_FENCES = ("```sql", "```SQL", "```")


def load_dataset(path: Path, limit: int | None = None) -> list[dict]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            items.append(json.loads(line))
    if limit:
        items = items[:limit]
    return items


def clean_sql(sql: str) -> str:
    """去掉模型输出里可能包裹的 markdown 代码块与首尾空白。"""
    if not sql:
        return ""
    text = sql.strip()
    for fence in _MARKDOWN_FENCES:
        if text.startswith(fence):
            text = text[len(fence):]
            break
    if text.endswith("```"):
        text = text[:-3]
    return text.strip().rstrip(";").strip()


async def measure_schema(meta_repository) -> dict:
    """统计当前元数据知识库的体量。

    这个数字衡量的是 llm_only 基线的输入压力 —— 它不做任何筛选，
    会把 meta 库里登记的全部表结构、字段与指标无一遗漏地交给模型。
    召回类策略只喂命中子集，所以本数据是判断「上下文膨胀」程度的基准。

    token 数是估算值：按 provide_full_schema 节点的口径把元数据拼成文本，
    汉字按 1.5 token、其余字符按 0.3 token 折算。用于量级对比，不是精确计费。
    """
    from sqlalchemy import select

    from app.models.mysql.column_info_mysql import ColumnInfoMySQL
    from app.models.mysql.metric_info_mysql import MetricInfoMySQL
    from app.models.mysql.table_info_mysql import TableInfoMySQL

    session = meta_repository.session
    tables = (await session.execute(select(TableInfoMySQL))).scalars().all()
    columns = (await session.execute(select(ColumnInfoMySQL))).scalars().all()
    metrics = (await session.execute(select(MetricInfoMySQL))).scalars().all()

    parts: list[str] = []
    for table in tables:
        own = [c for c in columns if c.table_id == table.id]
        parts.append(f"{table.name} {table.description}")
        for col in own:
            parts.append(
                f"  {col.name} {col.type} {col.role} {col.description} {col.alias} {col.examples}"
            )
    for metric in metrics:
        parts.append(f"{metric.name} {metric.description}")
    text = "\n".join(parts)

    han = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return {
        "tables": len(tables),
        "columns": len(columns),
        "metrics": len(metrics),
        "schema_chars": len(text),
        "schema_tokens_est": int(han * 1.5 + (len(text) - han) * 0.3),
    }


def _brief(exc: BaseException, limit: int = 200) -> str:
    """从异常里提取一行简述，供事后归因。

    失败原因归类只用异常类名（这样同类原因能聚合成计数），
    但只看类名会丢掉最关键的信息：同样是 OperationalError，
    既可能是数据库连接抖动，也可能是模型写出了 Unknown column 的非法 SQL
    —— 后者是策略的真实失败。把详情单独存下来，核对结果时才说得清。
    """
    return str(exc).strip().replace("\n", " ")[:limit]


async def prepare_gold(dataset: list[dict], dw_repository: DwMysqlRepository) -> dict[str, list[dict]]:
    """预先执行所有 gold SQL 并缓存结果，避免每题重复查库。"""
    gold: dict[str, list[dict]] = {}
    for item in dataset:
        rows = await dw_repository.execute_sql(item["sql"])
        gold[item["qid"]] = rows
    return gold


async def run_case(
    graph,
    runtime: DataAgentContext,
    question: str,
    gold_rows: list[dict],
) -> dict:
    """跑单题：执行链路 → 拿到 SQL → 执行 SQL → 与 gold 结果集比对。

    SQL 一律用 runtime 自带的 repository 执行。这样并发跑题时
    每题各用一个 session，不会互相踩到（AsyncSession 不是并发安全的，
    共享一个实例会产生 "This result object does not return rows" 之类的错）。
    """
    state = make_initial_state(question)
    last: dict = {}
    graph_error: str | None = None

    started = time.perf_counter()
    error_detail = ""
    try:
        async for chunk in graph.astream(state, context=runtime, stream_mode="values"):
            last = chunk
    except Exception as exc:  # noqa: BLE001 - 链路内部异常也要记录下来
        graph_error = f"链路执行异常：{type(exc).__name__}"
        error_detail = _brief(exc)
        # 异常时保留最后一次成功的 state，以便拿到已生成的 SQL
    elapsed = time.perf_counter() - started

    sql = clean_sql(last.get("sql") or "")

    pred_rows: list[dict] | None = None
    pred_error: str | None = None
    if graph_error:
        pred_error = graph_error
    elif not sql:
        pred_error = "未生成 SQL"
    else:
        try:
            # DataAgentContext 是 TypedDict（运行时就是个 dict），不是 dataclass
            pred_rows = await runtime["dw_mysql_repository"].execute_sql(sql)
        except Exception as exc:  # noqa: BLE001 - SQL 执行失败是评测的正常结果之一
            pred_error = "SQL 执行失败"
            # 光记「执行失败」没法排查：它可能是连接断了，也可能是生成了语法错误的 SQL，
            # 前者要修基础设施、后者是策略问题。详情必须留下来
            error_detail = f"执行预测SQL失败：{_brief(exc)}"

    ex, ex_ordered, reason = classify_case(gold_rows, pred_rows, pred_error)

    return {
        "sql": sql,
        "elapsed_s": round(elapsed, 2),
        "ex": ex,
        "ex_ordered": ex_ordered,
        "executable": 0 if pred_error else 1,
        "reason": reason,
        "error_detail": error_detail,
    }


async def evaluate(
    strategies: list[str],
    dataset: list[dict],
    outdir: Path,
    tag: str = "",
    meta_config: Path | None = None,
    concurrency: int = 1,
) -> dict:
    embedding_manager.init()
    qdrant_client_manager.init()
    es_client_manager.init()
    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()

    results: dict[str, list[dict]] = {}
    timings: dict[str, float] = {}

    async with meta_mysql_client_manager.session_factory() as meta_session, \
            dw_mysql_client_manager.session_factory() as dw_session:
        meta_repository = MetaMysqlRepository(meta_session)
        dw_repository = DwMysqlRepository(dw_session)

        runtime = DataAgentContext(
            embedding_client=embedding_manager.client,
            column_qdrant_repository=ColumnQdrantRepository(qdrant_client_manager.client),
            metric_qdrant_repository=MetricQdrantRepository(qdrant_client_manager.client),
            value_es_repository=ValueEsRepository(es_client_manager.client),
            meta_mysql_repository=meta_repository,
            dw_mysql_repository=dw_repository,
        )

        print("预执行 gold SQL ...")
        gold = await prepare_gold(dataset, dw_repository)
        print(f"  完成 {len(gold)} 题\n")

        schema = await measure_schema(meta_repository)
        print(
            f"元数据规模：{schema['tables']} 表 / {schema['columns']} 字段 / "
            f"{schema['metrics']} 指标，全量 schema 约 {schema['schema_tokens_est']} tokens"
        )
        print(f"评测标签：{tag or '(未指定)'}，并发 {concurrency}\n")

        @asynccontextmanager
        async def case_runtime():
            """每题一套独立 session。

            外层那个 runtime 是串行的，只用来预跑 gold 和量 schema；
            并发跑题时必须每题各开一个，否则多个链路共用同一个
            AsyncSession 会互相踩踏（并发下 session 不是线程/协程安全的）。
            """
            async with meta_mysql_client_manager.session_factory() as m_session, \
                    dw_mysql_client_manager.session_factory() as d_session:
                yield DataAgentContext(
                    embedding_client=embedding_manager.client,
                    column_qdrant_repository=ColumnQdrantRepository(qdrant_client_manager.client),
                    metric_qdrant_repository=MetricQdrantRepository(qdrant_client_manager.client),
                    value_es_repository=ValueEsRepository(es_client_manager.client),
                    meta_mysql_repository=MetaMysqlRepository(m_session),
                    dw_mysql_repository=DwMysqlRepository(d_session),
                )

        sem = asyncio.Semaphore(max(1, concurrency))

        async def run_one(graph, item):
            async with sem, case_runtime() as rt:
                return await run_case(graph, rt, item["question"], gold[item["qid"]])

        for strategy in strategies:
            graph = build_graph(strategy)
            started = time.perf_counter()

            outcomes = await asyncio.gather(*(run_one(graph, item) for item in dataset))

            elapsed = time.perf_counter() - started
            timings[strategy] = elapsed

            # 并发后按题号归位打印，保证输出顺序与串行时一致、便于比对
            records: list[dict] = []
            for item, record in zip(dataset, outcomes):
                record["qid"] = item["qid"]
                record["level"] = item["level"]
                record["kind"] = item["kind"]
                records.append(record)

                mark = "✓" if record["ex"] else "✗"
                tail = record["reason"] or "一致"
                print(
                    f"  [{strategy}] {len(records):>2}/{len(dataset)} {mark} "
                    f"{item['qid']} {tail}",
                    flush=True,
                )

            results[strategy] = records

            ex_mean = mean([r["ex"] for r in records])
            print(
                f"  → {strategy} 完成：EX {fmt_pct(ex_mean)}，"
                f"耗时 {elapsed / 60:.1f} 分钟\n",
                flush=True,
            )

    await qdrant_client_manager.close()
    await es_client_manager.close()
    await meta_mysql_client_manager.close()
    await dw_mysql_client_manager.close()

    outdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    payload = {
        "stamp": stamp,
        "tag": tag,
        "meta_config": str(meta_config) if meta_config else "",
        "dataset": str(DEFAULT_DATASET.name),
        "n_questions": len(dataset),
        "schema": schema,
        "strategies": {k: v for k, v in results.items()},
        "timings": timings,
    }
    outfile = outdir / f"nl2sql_{stamp}.json"
    outfile.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"原始结果已写入 {outfile.relative_to(boot.PROJECT_ROOT)}")

    return payload


def _ex_of(records: list[dict], level: str | None = None) -> list[float]:
    if level is None:
        return [r["ex"] for r in records]
    return [r["ex"] for r in records if r["level"] == level]


def build_report(payload: dict) -> str:
    """渲染评测报告。"""
    strategies = list(payload["strategies"].keys())
    n = payload["n_questions"]
    lines: list[str] = []
    add = lines.append

    tag = payload.get("tag") or ""
    schema = payload.get("schema") or {}
    add("# NL2SQL 评测报告")
    add("")
    if tag:
        add(f"- 评测场景：**{tag}**")
    if schema:
        add(
            f"- 元数据规模：**{schema['tables']} 张表 / {schema['columns']} 个字段 / "
            f"{schema['metrics']} 个指标**"
        )
    add(f"- 标注集：{payload['dataset']}，共 {n} 题，按 A–F 六档难度分组")
    add("- 主指标：**Execution Accuracy（执行准确率）**")
    add(f"- 生成时间：{payload['stamp']}")
    add("")

    # ---- 逐层累加对比表
    add("## 1. 逐层累加消融")
    add("")
    add("| 策略 | 相对上一层新增 | EX | EX（含行序） | 可执行率 | ΔEX | p 值 |")
    add("|---|---|---:|---:|---:|---:|---:|")

    previous: list[float] | None = None
    for name in strategies:
        records = payload["strategies"][name]
        ex_values = _ex_of(records)
        ex = mean(ex_values)
        ex_ordered = mean([r["ex_ordered"] for r in records])
        executable = mean([r["executable"] for r in records])

        if previous is None:
            delta, p_text = "—", "—"
        else:
            delta = fmt_delta(ex - mean(previous))
            p_text = fmt_p(paired_bootstrap_p_value(previous, ex_values))

        add(
            f"| `{name}` | {STRATEGY_DELTA.get(name, '')} "
            f"| {fmt_pct(ex)} | {fmt_pct(ex_ordered)} | {fmt_pct(executable)} "
            f"| {delta} | {p_text} |"
        )
        previous = ex_values

    add("")
    add(
        "> Δ 与 p 值只对**相邻两层**计算，保证每一分提升都能干净归因到一个组件。"
        "p 值用配对 bootstrap 检验（同一批问题逐题配对、2000 次重采样、双侧），"
        "p < 0.05 才认为提升不太可能来自随机波动。"
    )
    add("")

    # ---- 与裸 LLM 基线的直接对比
    if "llm_only" in payload["strategies"]:
        base = _ex_of(payload["strategies"]["llm_only"])
        add("## 2. 各层相对裸 LLM 基线")
        add("")
        add("| 策略 | EX | 相对基线 | p 值 |")
        add("|---|---:|---:|---:|")
        for name in strategies[1:]:
            values = _ex_of(payload["strategies"][name])
            add(
                f"| `{name}` | {fmt_pct(mean(values))} "
                f"| {fmt_delta(mean(values) - mean(base))} "
                f"| {fmt_p(paired_bootstrap_p_value(base, values))} |"
            )
        add("")

    # ---- 分难度拆解
    levels = ["A", "B", "C", "D", "E", "F"]
    add("## 3. 分难度档拆解")
    add("")
    add("| 难度档 | 题数 | " + " | ".join(f"`{s}`" for s in strategies) + " |")
    add("|---|---:|" + "---:|" * len(strategies))
    level_names = {
        "A": "单表聚合", "B": "单维分组", "C": "TopN 排序",
        "D": "精确取值", "E": "时间对比/指标", "F": "多表交叉/子查询/窗口函数",
    }
    for level in levels:
        ref = payload["strategies"][strategies[0]]
        count = len([r for r in ref if r["level"] == level])
        if not count:
            continue
        cells = " | ".join(
            fmt_pct(mean(_ex_of(payload["strategies"][s], level)))
            for s in strategies
        )
        add(f"| {level} {level_names[level]} | {count} | {cells} |")
    add("")

    # ---- 每组相对基线的输入规模与延迟
    add("## 5. 延迟代价")
    add("")
    add("| 策略 | 平均单题耗时 | 相对基线 | 总耗时 |")
    add("|---|---:|---:|---:|")
    per_case: dict[str, float] = {}
    for name in strategies:
        records = payload["strategies"][name]
        elapseds = [r.get("elapsed_s", 0.0) for r in records if "elapsed_s" in r]
        per_case[name] = mean(elapseds) if elapseds else 0.0
    base_latency = per_case.get(strategies[0], 0.0)
    for name in strategies:
        avg = per_case[name]
        # 旧版本的结果里没有 elapsed_s 字段，补零会让表格看着像真的测出 0 秒
        avg_text = f"{avg:.1f}s" if avg else "—"
        ratio = f"{avg / base_latency:.2f}x" if avg and base_latency else "—"
        total = payload.get("timings", {}).get(name)
        total_text = f"{total / 60:.1f} 分钟" if total else "—"
        add(f"| `{name}` | {avg_text} | {ratio} | {total_text} |")
    add("")
    if not any(per_case.values()):
        add("> 本次结果由旧版评测产出，未记录逐题耗时。重跑后本节会填充。")
        add("")

    # ---- 归因
    add("## 6. 完整链路失败归因")
    add("")
    full = payload["strategies"].get("full")
    if full:
        from collections import Counter

        add("| 失败原因 | 题数 |")
        add("|---|---:|")
        for reason, count in Counter(r["reason"] for r in full if not r["ex"]).most_common():
            add(f"| {reason} | {count} |")
        add("")

        failed = [r for r in full if not r["ex"]]
        if failed:
            add("失败题清单：")
            add("")
            for r in failed:
                detail = r.get("error_detail", "")
                tail = f" — {detail}" if detail else ""
                add(
                    f"- `{r['qid']}`（{level_names.get(r['level'], r['level'])} 档）："
                    f"{r['reason']}{tail}"
                )
            add("")

    # ---- 结论
    add("## 7. 结论")
    add("")
    add(_build_conclusion(payload))
    add("")

    return "\n".join(lines)


def _build_conclusion(payload: dict) -> str:
    """从数据里自动归纳结论，避免报告与数据脱节。"""
    strategies = list(payload["strategies"].keys())
    parts: list[str] = []

    # 找出相邻层中显著的那几层
    significant = []
    previous: list[float] | None = None
    for name in strategies:
        values = _ex_of(payload["strategies"][name])
        if previous is not None:
            p = paired_bootstrap_p_value(previous, values)
            if p < 0.05:
                significant.append((name, mean(values) - mean(previous), p))
        previous = values

    if significant:
        for name, diff, p in significant:
            parts.append(
                f"- **{STRATEGY_DELTA.get(name, '')}** 是本次评测中唯一带来显著提升的环节："
                f"{fmt_delta(diff)}，p={fmt_p(p)}。"
            )
    else:
        parts.append("- 本次评测中没有任何一层带来统计上显著的提升。")

    # 单独召回是否反而有害
    n_tables = (payload.get("schema") or {}).get("tables", 0)
    scale = f"{n_tables} 张表" if n_tables else "当前规模"
    if "llm_only" in payload["strategies"] and "full_recall" in payload["strategies"]:
        base = _ex_of(payload["strategies"]["llm_only"])
        recall = _ex_of(payload["strategies"]["full_recall"])
        if mean(recall) < mean(base):
            parts.append(
                f"- 三路召回**单独**使用时反而低于裸 LLM 基线"
                f"（{fmt_pct(mean(recall))} vs {fmt_pct(mean(base))}）："
                "召回在缩小 schema 的同时也会带入无关字段，"
                f"在 {scale} 的数仓上，这些噪声的代价超过了筛选带来的收益。"
            )

    # 难题档上的表现
    if "llm_only" in payload["strategies"]:
        base_f = _ex_of(payload["strategies"]["llm_only"], "F")
        if base_f:
            best = max(strategies, key=lambda s: mean(_ex_of(payload["strategies"][s], "F")))
            best_f = mean(_ex_of(payload["strategies"][best], "F"))
            parts.append(
                f"- 差距主要来自 F 档难题：`llm_only` {fmt_pct(mean(base_f))}，"
                f"`{best}` {fmt_pct(best_f)}。"
                "查询越复杂，交给模型的 schema 就越需要精准——"
                "这正是精筛环节的用武之地。"
            )

    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="NL2SQL 评测")
    parser.add_argument(
        "--strategies",
        default=",".join(STRATEGY_ORDER),
        help="要跑的策略，逗号分隔",
    )
    parser.add_argument("--limit", type=int, help="只跑前 N 题")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="同时跑几道题。评测耗时几乎全在等 LLM 与数据库 IO 上，"
             "串行时 GPU/CPU 都在空转，开到 4~8 可把总耗时压到 1/5 左右。"
             "注意每题会占 2 个 MySQL 连接（meta + dw），别超过连接池上限",
    )
    parser.add_argument(
        "--tag",
        default="",
        help="本次评测的场景标签，写进报告头部，用于区分不同 schema 规模的轮次，例如 --tag 32表大schema",
    )
    parser.add_argument(
        "--meta-config",
        type=Path,
        help="构建元数据所用的配置文件路径，仅记录在报告里说明本次跑的是哪套元数据",
    )
    parser.add_argument("--report", type=Path, help="报告输出路径")
    parser.add_argument(
        "--from-json",
        dest="from_json",
        help="基于已有结果 JSON 重新渲染报告，不重跑评测（调报告格式时用）",
    )
    args = parser.parse_args()

    if args.from_json:
        payload = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        report = build_report(payload)
        print(report)
        out = args.report or (REPORT_DIR / "nl2sql_report.md")
        out.write_text(report, encoding="utf-8")
        print(f"\n报告已写入 {out}")
        return 0

    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    unknown = [s for s in strategies if s not in STRATEGY_ORDER]
    if unknown:
        print(f"未知策略：{unknown}")
        return 2

    dataset = load_dataset(args.dataset, args.limit)
    print(f"策略：{strategies}")
    print(f"题目：{len(dataset)} 题\n")

    payload = asyncio.run(
        evaluate(
            strategies,
            dataset,
            REPORT_DIR,
            tag=args.tag,
            meta_config=args.meta_config,
            concurrency=args.concurrency,
        )
    )

    report = build_report(payload)
    report_path = args.report or (REPORT_DIR / "nl2sql_report.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    print()
    print(report)
    print(f"\n报告已写入 {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
