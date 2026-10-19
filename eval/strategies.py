"""
NL2SQL 链路的消融策略组。

为什么单独建一套图，而不是往业务代码里塞 if/else 开关：
    往节点里加 `if os.getenv("EVAL_DISABLE_VALUE_RECALL")` 这类判断，
    会让生产代码里到处是无法验证的分支，改业务逻辑时要时刻提防碰响评测路径。
    这里改为在评测侧按需组装 LangGraph：复用业务节点原函数，
    被关掉的组件用 stub 节点替代（返回空结果），业务代码保持零改动。

六组策略逐层累加，每组只新增一个组件，这样相邻两组的差值才能干净地归因：

    S1 llm_only      基线：不做任何召回，把全部表结构与指标一股脑交给模型
    S2 +col          加字段向量召回，只把语义相关的字段交给模型
    S3 +val          加取值召回，让模型看到库里真实存的字符串
    S4 +metric       加指标召回，让模型拿到口径定义
    S5 +filter       加 LLM 精筛，剔除召回噪声里的无关表和指标
    S6 +correct      加 SQL 校验与纠错，跑不通的 SQL 让模型改一遍再执行
"""
from __future__ import annotations

from langgraph.constants import END, START
from langgraph.graph import StateGraph
from langgraph.runtime import Runtime
from sqlalchemy import select

from app.agent.context import DataAgentContext
from app.agent.nodes.add_extra_context import add_extra_context
from app.agent.nodes.correct_sql import correct_sql
from app.agent.nodes.execute_sql import execute_sql
from app.agent.nodes.extract_keywords import extract_keywords
from app.agent.nodes.filter_metric import filter_metric
from app.agent.nodes.filter_table import filter_table
from app.agent.nodes.generate_sql import generate_sql
from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
from app.agent.nodes.recall_column import recall_column
from app.agent.nodes.recall_metric import recall_metric
from app.agent.nodes.recall_value import recall_value
from app.agent.nodes.validata_sql import validata_sql
from app.agent.state import (
    ColumnInfoState,
    DataAgentState,
    MetricInfoState,
    TableInfoState,
)
from app.models.mysql.column_info_mysql import ColumnInfoMySQL
from app.models.mysql.metric_info_mysql import MetricInfoMySQL
from app.models.mysql.table_info_mysql import TableInfoMySQL

# --------------------------------------------------------------------------- #
# 策略定义
# --------------------------------------------------------------------------- #
STRATEGY_ORDER = [
    "llm_only",
    "col_recall",
    "col_val_recall",
    "full_recall",
    "llm_filter",
    "full",
]

# 每个策略逐层累加的开关
SPECS: dict[str, dict[str, bool]] = {
    "llm_only":       dict(full_schema=True,  recall_column=False, recall_value=False, recall_metric=False, use_filter=False, use_correct=False),
    "col_recall":     dict(full_schema=False, recall_column=True,  recall_value=False, recall_metric=False, use_filter=False, use_correct=False),
    "col_val_recall": dict(full_schema=False, recall_column=True,  recall_value=True,  recall_metric=False, use_filter=False, use_correct=False),
    "full_recall":    dict(full_schema=False, recall_column=True,  recall_value=True,  recall_metric=True,  use_filter=False, use_correct=False),
    "llm_filter":     dict(full_schema=False, recall_column=True,  recall_value=True,  recall_metric=True,  use_filter=True,  use_correct=False),
    "full":           dict(full_schema=False, recall_column=True,  recall_value=True,  recall_metric=True,  use_filter=True,  use_correct=True),
}

# 每一层相对于上一层的增量说明，用于报告
STRATEGY_DELTA = {
    "llm_only": "基线：全量元数据直接灌输",
    "col_recall": "改为按语义召回字段",
    "col_val_recall": "补充字段真实取值",
    "full_recall": "补充指标口径定义",
    "llm_filter": "加 LLM 精筛表与指标",
    "full": "加 SQL 校验与纠错",
}


# --------------------------------------------------------------------------- #
# 评测专用节点
# --------------------------------------------------------------------------- #
async def provide_full_schema(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """
    基线节点：不做召回，把 meta 库里登记的全部表结构与指标交给模型。

    这是 NL2SQL 里常见的 full-schema prompting 基线。它拿到的信息其实"不少"，
    但正因为没有筛选，模型要在 5 张表 24 个字段里自己判断该用哪些，
    同时也看不到字段在库里的真实取值。
    """
    meta_repository = runtime.context["meta_mysql_repository"]

    table_rows = (
        await meta_repository.session.execute(select(TableInfoMySQL))
    ).scalars().all()

    table_infos: list[TableInfoState] = []
    for table in table_rows:
        column_rows = (
            await meta_repository.session.execute(
                select(ColumnInfoMySQL).where(ColumnInfoMySQL.table_id == table.id)
            )
        ).scalars().all()
        columns = [
            ColumnInfoState(
                name=c.name,
                type=c.type,
                role=c.role,
                examples=c.examples,
                description=c.description,
                alias=c.alias,
            )
            for c in column_rows
        ]
        table_infos.append(
            TableInfoState(
                name=table.name,
                role=table.role,
                description=table.description,
                columns=columns,
            )
        )

    metric_rows = (
        await meta_repository.session.execute(select(MetricInfoMySQL))
    ).scalars().all()
    metric_infos = [
        MetricInfoState(
            name=m.name,
            description=m.description,
            relevant_columns=m.relevant_columns,
            alias=m.alias,
        )
        for m in metric_rows
    ]

    return {"table_infos": table_infos, "metric_infos": metric_infos}


def _empty_recall(key: str):
    """
    生成一个占位节点，替代被关掉的召回路。

    必须返回空列表而不是直接跳过节点：merge_retrieved_info 会无条件读取
    三路召回的结果，缺 key 会直接 KeyError。用占位节点保证 state 结构完整。
    """

    async def _node(state: DataAgentState, runtime: Runtime[DataAgentContext]) -> dict:
        return {key: []}

    _node.__name__ = f"empty_{key}"
    return _node


def _route_after_validate(state: DataAgentState) -> str:
    """SQL 校验通过就去执行，不通过就先纠错。"""
    return "execute_sql" if state.get("error") is None else "correct_sql"


# --------------------------------------------------------------------------- #
# 图组装
# --------------------------------------------------------------------------- #
def build_graph(strategy: str):
    """
    按策略组装一条 NL2SQL 链路。

    :param strategy: STRATEGY_ORDER 中的策略名
    :return: 编译好的 LangGraph 图
    """
    if strategy not in SPECS:
        raise ValueError(
            f"未知策略 {strategy!r}，可选：{', '.join(STRATEGY_ORDER)}"
        )
    spec = SPECS[strategy]

    builder = StateGraph(state_schema=DataAgentState, context_schema=DataAgentContext)

    # 1. 意图理解：所有策略共有
    builder.add_node("extract_keywords", extract_keywords)

    # 2. schema 供给：全量灌输 或 逐路召回后合并
    if spec["full_schema"]:
        builder.add_node("full_schema", provide_full_schema)
        builder.add_edge(START, "extract_keywords")
        builder.add_edge("extract_keywords", "full_schema")
        builder.add_edge("extract_keywords", "add_extra_context")
        schema_tail = "full_schema"
    else:
        builder.add_node("recall_column", recall_column)
        builder.add_node(
            "recall_metric",
            recall_metric if spec["recall_metric"] else _empty_recall("recall_metric"),
        )
        builder.add_node(
            "recall_value",
            recall_value if spec["recall_value"] else _empty_recall("recall_value"),
        )
        builder.add_node("merge_retrieved_info", merge_retrieved_info)

        builder.add_edge(START, "extract_keywords")
        builder.add_edge("extract_keywords", "recall_column")
        builder.add_edge("extract_keywords", "recall_metric")
        builder.add_edge("extract_keywords", "recall_value")
        builder.add_edge("recall_column", "merge_retrieved_info")
        builder.add_edge("recall_metric", "merge_retrieved_info")
        builder.add_edge("recall_value", "merge_retrieved_info")
        schema_tail = "merge_retrieved_info"

    # 3. LLM 精筛（可选）：剔除召回里的无关表与指标
    if spec["use_filter"]:
        builder.add_node("filter_metric", filter_metric)
        builder.add_node("filter_table", filter_table)
        builder.add_edge(schema_tail, "filter_metric")
        builder.add_edge(schema_tail, "filter_table")
        builder.add_edge("filter_metric", "add_extra_context")
        builder.add_edge("filter_table", "add_extra_context")
    else:
        builder.add_edge(schema_tail, "add_extra_context")

    # 4. 补充时间与数据库上下文
    builder.add_node("add_extra_context", add_extra_context)

    # 5. 生成 SQL
    builder.add_node("generate_sql", generate_sql)
    builder.add_edge("add_extra_context", "generate_sql")

    # 6. 执行：可选先校验再纠错
    builder.add_node("execute_sql", execute_sql)
    if spec["use_correct"]:
        builder.add_node("validata_sql", validata_sql)
        builder.add_node("correct_sql", correct_sql)
        builder.add_edge("generate_sql", "validata_sql")
        builder.add_conditional_edges(
            "validata_sql",
            _route_after_validate,
            {"execute_sql": "execute_sql", "correct_sql": "correct_sql"},
        )
        builder.add_edge("correct_sql", "execute_sql")
    else:
        builder.add_edge("generate_sql", "execute_sql")

    builder.add_edge("execute_sql", END)
    return builder.compile()


def make_initial_state(query: str) -> dict:
    """
    构造初始 state。

    显式给全部字段，缺失字段在 LangGraph 里读 state[key] 会 KeyError，
    而这些 key 里有一部分（如 error）要到最后才被写入。
    """
    return {
        "error": None,
        "query": query,
        "keywords": [],
        "recall_columns": [],
        "recall_metric": [],
        "recall_values": [],
        "table_infos": [],
        "metric_infos": [],
        "date_info": {},
        "db_info": {},
        "sql": "",
    }


def node_count(strategy: str) -> int:
    """返回该策略图中的业务节点数，用于报告里说明链路规模。"""
    graph = build_graph(strategy)
    return len([n for n in graph.get_graph().nodes if n not in (START, END)])
