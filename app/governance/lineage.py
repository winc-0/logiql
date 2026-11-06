"""字段级血缘：每个字段从哪来、经过什么加工、被谁用。

血缘写在 conf/lineage.yaml 里，这里负责：

1. 校验血缘本身：每张表、每个字段都要有来源，引用的字段必须真实存在，不能有环；
   标注了 verified_by 的边，引用的数据质量规则必须存在（这条边由对账规则用数据验证过）。
2. 用 56 道评测题的 gold SQL 反向检查：题目里用到的字段是否都能追到源系统。
3. 查询：某个字段往上追到哪些源头（溯源），往下影响哪些字段和哪些评测题（影响分析）。

表结构从 docker/mysql/*.sql 的建表语句里解析，不需要连数据库。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

LAYERS = ("ODS", "DWD", "DWS", "ADS", "DIM")


# ------------------------------------------------------------------ 表结构


def parse_ddl(text: str) -> dict[str, list[str]]:
    """从建表语句里取出 {表名: [字段名]}，忽略 PRIMARY KEY 等约束行。"""
    schema: dict[str, list[str]] = {}
    for m in re.finditer(r"CREATE TABLE\s+`?(\w+)`?\s*\(", text, re.I):
        depth, i = 1, m.end()
        while depth and i < len(text):
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            i += 1
        body = text[m.end(): i - 1]
        cols = []
        for line in body.splitlines():
            cm = re.match(r"\s*`?(\w+)`?\s+[A-Za-z]", line)
            if cm and cm.group(1).upper() not in ("PRIMARY", "KEY", "UNIQUE", "INDEX", "CONSTRAINT", "FOREIGN"):
                cols.append(cm.group(1))
        schema[m.group(1)] = cols
    return schema


def load_schema(paths: list[Path]) -> dict[str, list[str]]:
    schema: dict[str, list[str]] = {}
    for p in paths:
        schema.update(parse_ddl(Path(p).read_text(encoding="utf-8")))
    return schema


# ------------------------------------------------------------------ 血缘模型


@dataclass
class ColumnLineage:
    sources: list[str] = field(default_factory=list)  # 上游字段 "表.字段"
    system: str | None = None  # 直接来自哪个源系统
    transform: str = ""
    verified_by: list[str] = field(default_factory=list)


@dataclass
class TableLineage:
    name: str
    layer: str
    system: str | None
    note: str
    columns: dict[str, ColumnLineage]


@dataclass
class Lineage:
    systems: dict[str, str]
    tables: dict[str, TableLineage]

    def column(self, ref: str) -> ColumnLineage | None:
        """取某个字段的血缘。没单独写的字段，继承表级的源系统。"""
        table, _, col = ref.partition(".")
        t = self.tables.get(table)
        if not t:
            return None
        if col in t.columns:
            return t.columns[col]
        if t.system:
            return ColumnLineage(system=t.system, transform="原样同步")
        return None

    def edges(self) -> list[tuple[str, str, ColumnLineage]]:
        """所有显式写出的「上游字段 -> 下游字段」边。"""
        out = []
        for t in self.tables.values():
            for col, cl in t.columns.items():
                for src in cl.sources:
                    out.append((src, f"{t.name}.{col}", cl))
        return out


def _as_list(v) -> list[str]:
    if v is None:
        return []
    return [v] if isinstance(v, str) else list(v)


def parse_lineage(data: dict) -> Lineage:
    tables = {}
    for name, raw in (data.get("tables") or {}).items():
        raw = raw or {}
        cols = {}
        for col, c in (raw.get("columns") or {}).items():
            c = c or {}
            cols[col] = ColumnLineage(
                sources=_as_list(c.get("from")),
                system=c.get("system"),
                transform=c.get("transform", ""),
                verified_by=_as_list(c.get("verified_by")),
            )
        tables[name] = TableLineage(name=name, layer=raw.get("layer", ""), system=raw.get("system"),
                                    note=raw.get("note", ""), columns=cols)
    return Lineage(systems=dict(data.get("systems") or {}), tables=tables)


def load_lineage(path: str | Path) -> Lineage:
    return parse_lineage(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


# ------------------------------------------------------------------ 校验


def validate(lineage: Lineage, schema: dict[str, list[str]], dq_rule_ids: set[str] | None = None) -> list[str]:
    problems: list[str] = []
    for name in schema:
        if name not in lineage.tables:
            problems.append(f"表 {name} 没有血缘声明")
    for name, t in lineage.tables.items():
        if name not in schema:
            problems.append(f"血缘里的表 {name} 在建表语句里不存在")
            continue
        if t.layer not in LAYERS:
            problems.append(f"表 {name} 的 layer 不合法：{t.layer!r}")
        if t.system and t.system not in lineage.systems:
            problems.append(f"表 {name} 的源系统 {t.system} 没有在 systems 里登记")
        for col in schema[name]:
            if lineage.column(f"{name}.{col}") is None:
                problems.append(f"字段 {name}.{col} 没有血缘（既没有上游字段，也没有源系统）")
        for col, cl in t.columns.items():
            ref = f"{name}.{col}"
            if col not in schema[name]:
                problems.append(f"血缘里的字段 {ref} 在建表语句里不存在")
            if not cl.sources and not cl.system:
                problems.append(f"字段 {ref} 既没有 from 也没有 system")
            if cl.system and cl.system not in lineage.systems:
                problems.append(f"字段 {ref} 的源系统 {cl.system} 没有在 systems 里登记")
            for src in cl.sources:
                st, _, sc = src.partition(".")
                if st not in schema or sc not in schema[st]:
                    problems.append(f"字段 {ref} 的上游 {src} 不存在")
            if dq_rule_ids is not None:
                for rid in cl.verified_by:
                    if rid not in dq_rule_ids:
                        problems.append(f"字段 {ref} 的 verified_by 引用了不存在的质量规则 {rid}")
    problems += [f"血缘有环：{' -> '.join(c)}" for c in find_cycles(lineage)]
    return problems


def find_cycles(lineage: Lineage) -> list[list[str]]:
    graph: dict[str, list[str]] = {}
    for src, dst, _ in lineage.edges():
        graph.setdefault(dst, []).append(src)
    cycles, state = [], {}

    def dfs(node: str, path: list[str]):
        state[node] = 1
        for nxt in graph.get(node, []):
            if state.get(nxt) == 1:
                cycles.append(path[path.index(nxt):] + [nxt] if nxt in path else [node, nxt])
            elif not state.get(nxt):
                dfs(nxt, path + [nxt])
        state[node] = 2

    for n in list(graph):
        if not state.get(n):
            dfs(n, [n])
    return cycles


# ------------------------------------------------------------------ 溯源与影响分析


def trace_up(lineage: Lineage, ref: str, _seen: frozenset = frozenset()) -> dict:
    """往上追：返回 {ref, system, transform, verified_by, upstream: [...]} 的树。"""
    cl = lineage.column(ref)
    node = {"ref": ref, "system": None, "transform": "", "verified_by": [], "upstream": []}
    if cl is None:
        node["missing"] = True
        return node
    node.update(system=cl.system, transform=cl.transform, verified_by=cl.verified_by)
    if ref in _seen:
        return node
    node["upstream"] = [trace_up(lineage, s, _seen | {ref}) for s in cl.sources]
    return node


def roots(tree: dict) -> set[str]:
    """一棵溯源树最终落到的源系统。"""
    out = {tree["system"]} if tree.get("system") else set()
    for up in tree["upstream"]:
        out |= roots(up)
    return out


def traceable(lineage: Lineage, ref: str) -> bool:
    """字段能不能一路追到源系统（每条分支都要到头）。"""
    def ok(node: dict) -> bool:
        if node.get("missing"):
            return False
        if node["upstream"]:
            return all(ok(u) for u in node["upstream"]) or bool(node["system"])
        return bool(node["system"])
    return ok(trace_up(lineage, ref))


def trace_down(lineage: Lineage, ref: str) -> list[str]:
    """往下追：所有直接或间接用到这个字段的下游字段。"""
    children: dict[str, list[str]] = {}
    for src, dst, _ in lineage.edges():
        children.setdefault(src, []).append(dst)
    out, stack = [], [ref]
    while stack:
        for nxt in children.get(stack.pop(), []):
            if nxt not in out:
                out.append(nxt)
                stack.append(nxt)
    return out


def format_tree(tree: dict, systems: dict[str, str], indent: int = 0) -> list[str]:
    pad = "    " * indent
    head = f"{pad}{'└─ ' if indent else ''}{tree['ref']}"
    extra = []
    if tree.get("missing"):
        extra.append("没有血缘声明")
    if tree["transform"]:
        extra.append(tree["transform"])
    if tree["system"]:
        extra.append(f"源系统：{tree['system']}（{systems.get(tree['system'], '?')}）")
    if tree["verified_by"]:
        extra.append(f"已由质量规则验证：{', '.join(tree['verified_by'])}")
    lines = [head + (f"  [{'；'.join(extra)}]" if extra else "")]
    for up in tree["upstream"]:
        lines += format_tree(up, systems, indent + 1)
    return lines


# ------------------------------------------------------------------ 从 SQL 里取字段


_SQL_WORDS = {
    "select", "from", "join", "on", "where", "group", "by", "order", "having", "limit", "and", "or",
    "as", "in", "between", "not", "null", "is", "desc", "asc", "sum", "count", "avg", "max", "min",
    "round", "distinct", "over", "partition", "row_number", "lag", "case", "when", "then", "else",
    "end", "left", "right", "inner", "outer", "with", "union", "all", "like",
}


def columns_in_sql(sql: str, schema: dict[str, list[str]]) -> set[str]:
    """粗略解析 SQL 用到的「表.字段」。

    够用于评测集这类规整的查询：先按 FROM / JOIN 建立别名表，再解析 alias.col；
    没写别名前缀的字段，在本条 SQL 涉及的表里唯一匹配到才算。不认识的名字（中文别名、
    子查询别名）直接忽略。
    """
    body = re.sub(r"'(?:[^'\\]|\\.|'')*'", "''", sql)  # 去掉字符串常量
    alias: dict[str, str] = {}
    for m in re.finditer(r"\b(?:from|join)\s+(\w+)", body, re.I):
        # 别名单独往后看一眼，不能放进同一个正则：否则「FROM a JOIN b」里的 JOIN
        # 会被当成 a 的别名吃掉，后面的 b 就匹配不到了
        nxt = re.match(r"\s+(?:as\s+)?(\w+)", body[m.end():], re.I)
        table, al = m.group(1), nxt.group(1) if nxt else None
        if table in schema:
            alias[table] = table
            if al and al.lower() not in _SQL_WORDS:
                alias[al] = table
    used: set[str] = set()
    for m in re.finditer(r"\b(\w+)\.(\w+)\b", body):
        table = alias.get(m.group(1))
        if table and m.group(2) in schema[table]:
            used.add(f"{table}.{m.group(2)}")
    tables = set(alias.values())
    stripped = re.sub(r"\b\w+\.\w+\b", " ", body)
    for word in set(re.findall(r"\b([A-Za-z_]\w*)\b", stripped)):
        if word.lower() in _SQL_WORDS:
            continue
        owners = [t for t in tables if word in schema[t]]
        if len(owners) == 1:
            used.add(f"{owners[0]}.{word}")
    return used


# ------------------------------------------------------------------ 报告


def gold_coverage(lineage: Lineage, schema: dict[str, list[str]], gold: list[dict]) -> dict:
    per_column: dict[str, list[str]] = {}
    for item in gold:
        for ref in columns_in_sql(item["sql"], schema):
            per_column.setdefault(ref, []).append(item["qid"])
    ok_cols = {c for c in per_column if traceable(lineage, c)}
    ok_questions = [i["qid"] for i in gold
                    if all(c in ok_cols for c in columns_in_sql(i["sql"], schema))]
    return {
        "columns": per_column,
        "traceable": ok_cols,
        "missing": sorted(set(per_column) - ok_cols),
        "questions_ok": ok_questions,
        "questions_total": len(gold),
    }


def render_report(lineage: Lineage, schema: dict[str, list[str]], gold: list[dict],
                  problems: list[str], *, generated_at: str | None = None) -> str:
    total_cols = sum(len(c) for c in schema.values())
    declared = sum(1 for t, cols in schema.items() for c in cols if lineage.column(f"{t}.{c}"))
    cov = gold_coverage(lineage, schema, gold)
    edges = lineage.edges()
    verified = [e for e in edges if e[2].verified_by]

    lines = ["# 字段血缘报告", ""]
    if generated_at:
        lines += [f"生成时间：{generated_at}", ""]
    lines += [
        "## 概况",
        "",
        "| 项 | 结果 |",
        "|---|---|",
        f"| 表 | {len(schema)} 张 |",
        f"| 字段有血缘声明 | {declared}/{total_cols}（{declared / total_cols:.1%}） |",
        f"| 字段间的加工关系（边） | {len(edges)} 条，其中 {len(verified)} 条由质量规则用数据验证 |",
        f"| 评测集用到的字段 | {len(cov['columns'])} 个，能追到源系统 {len(cov['traceable'])} 个 |",
        f"| 评测题全部字段可追溯 | {len(cov['questions_ok'])}/{cov['questions_total']} 题 |",
        f"| 血缘自检问题 | {len(problems)} 个 |",
        "",
    ]
    if problems:
        lines += ["## 自检问题", ""] + [f"- {p}" for p in problems] + [""]

    lines += ["## 分层", "", "| 层 | 表 |", "|---|---|"]
    for layer in LAYERS:
        names = sorted(n for n, t in lineage.tables.items() if t.layer == layer)
        lines.append(f"| {layer} | {', '.join(names) if names else '（暂无）'} |")
    lines += ["", "## 源系统", "", "| 系统 | 说明 | 直接来源的表 |", "|---|---|---|"]
    for code, desc in lineage.systems.items():
        names = sorted(n for n, t in lineage.tables.items()
                       if t.system == code or any(c.system == code for c in t.columns.values()))
        lines.append(f"| {code} | {desc} | {', '.join(names)} |")

    lines += ["", "## 加工关系", "", "| 下游字段 | 上游字段 | 加工方式 | 验证规则 |", "|---|---|---|---|"]
    by_dst: dict[str, tuple[list[str], ColumnLineage]] = {}
    for src, dst, cl in edges:
        by_dst.setdefault(dst, ([], cl))[0].append(src)
    for dst, (srcs, cl) in by_dst.items():
        rules = ", ".join(f"`{r}`" for r in cl.verified_by) or "-"
        lines.append(f"| {dst} | {'<br>'.join(srcs)} | {cl.transform} | {rules} |")

    lines += ["", "## 评测集反向校验", "",
              "解析 56 道评测题 gold SQL 用到的字段，逐个检查能否追到源系统。", "",
              "| 字段 | 用到的题数 | 源系统 |", "|---|---|---|"]
    for ref in sorted(cov["columns"], key=lambda r: (-len(cov["columns"][r]), r)):
        src = "、".join(sorted(roots(trace_up(lineage, ref)))) or "**追不到**"
        lines.append(f"| {ref} | {len(cov['columns'][ref])} | {src} |")
    return "\n".join(lines).rstrip() + "\n"
