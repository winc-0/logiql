"""数据质量规则引擎。

规则写在 conf/dq_rules.yaml 里，每条规则编译成只读 SQL 去数仓里查，
再按阈值判定通过或失败。规则分四类：

- 表级：行数、主键唯一、日期覆盖（汇总表有没有缺日期）
- 字段级：非空、枚举、取值范围、外键引用、行级条件
- 跨表对账：同一个数在两张表上分别算一次，差额超过阈值就报错
- 口径一致性：同一个指标走两条路径计算，结果应该一致

每条规则可以标注 expect: fail，表示这是已知缺陷（比如造数时刻意埋的坑）。
报告会把结果分成「通过」「已知缺陷」「新问题」三种，只有新问题才让脚本返回非零，
这样规则既能用来演示缺陷，也能当回归检查用。
"""
from __future__ import annotations

import decimal
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

QueryFn = Callable[[str], Awaitable[list[dict]]]

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

CATEGORY_BY_TYPE = {
    "row_count": "表级",
    "unique": "表级",
    "coverage": "表级",
    "not_null": "字段级",
    "enum": "字段级",
    "range": "字段级",
    "reference": "字段级",
    "row_check": "字段级",
    "reconcile": "跨表对账",
}
CATEGORY_ORDER = ["表级", "字段级", "跨表对账", "口径一致性"]

# 每类规则必须提供的字段
_REQUIRED = {
    "row_count": ("table",),
    "unique": ("table", "columns"),
    "coverage": ("table", "column", "target_table", "target_column"),
    "not_null": ("table", "column"),
    "enum": ("table", "column", "values"),
    "range": ("table", "column"),
    "reference": ("table", "column", "ref_table", "ref_column"),
    "row_check": ("table", "check"),
    "reconcile": ("left", "right"),
}

SAMPLE_LIMIT = 5

ORIGINS = {"planted": "刻意埋设", "found": "规则发现"}


class RuleConfigError(ValueError):
    """规则配置写错了（缺字段、类型不认识、标识符不合法）。"""


@dataclass
class Rule:
    id: str
    type: str
    desc: str
    params: dict[str, Any]
    category: str
    severity: str = "error"
    expect: str = "pass"
    known_issue: str | None = None
    origin: str | None = None  # planted 刻意埋设 / found 规则查出、已登记待处理

    @property
    def tables(self) -> list[str]:
        """规则涉及的表，报告里用。reconcile 的表从 SQL 里粗略提取。"""
        p = self.params
        if self.type == "reconcile":
            found = re.findall(r"\b(?:from|join)\s+(\w+)", f"{p['left']} {p['right']}", re.I)
            return sorted(set(found))
        names = [p.get("table"), p.get("ref_table"), p.get("target_table")]
        return [n for n in names if n]


@dataclass
class RuleResult:
    rule: Rule
    status: str  # PASS / FAIL / ERROR
    observed: str
    samples: list[dict] = field(default_factory=list)
    sql: list[str] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        """把实际结果和预期对照，分成四种结论。"""
        if self.status == "ERROR":
            return "执行出错"
        if self.rule.expect == "fail":
            return "已知缺陷" if self.status == "FAIL" else "已知缺陷未检出"
        return "通过" if self.status == "PASS" else "新问题"

    @property
    def unexpected(self) -> bool:
        return self.outcome in ("新问题", "已知缺陷未检出", "执行出错")


# ------------------------------------------------------------------ 加载配置


def _ident(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _IDENT.match(value):
        raise RuleConfigError(f"{where} 不是合法的表名或字段名：{value!r}")
    return value


def parse_rules(data: dict) -> list[Rule]:
    raw_rules = (data or {}).get("rules") or []
    rules: list[Rule] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_rules):
        rid = raw.get("id") or f"#{i + 1}"
        rtype = raw.get("type")
        if rtype not in _REQUIRED:
            raise RuleConfigError(f"规则 {rid}：不认识的类型 {rtype!r}")
        if rid in seen:
            raise RuleConfigError(f"规则 id 重复：{rid}")
        seen.add(rid)
        missing = [k for k in _REQUIRED[rtype] if raw.get(k) in (None, "", [])]
        if missing:
            raise RuleConfigError(f"规则 {rid}：缺少字段 {', '.join(missing)}")

        params = {k: v for k, v in raw.items()
                  if k not in ("id", "type", "desc", "severity", "expect", "known_issue", "category", "origin")}
        for key in ("table", "column", "ref_table", "ref_column", "target_table", "target_column"):
            if key in params:
                _ident(params[key], f"规则 {rid} 的 {key}")
        if rtype == "unique":
            params["columns"] = [_ident(c, f"规则 {rid} 的 columns") for c in params["columns"]]
        if rtype == "range" and params.get("min") is None and params.get("max") is None:
            raise RuleConfigError(f"规则 {rid}：range 至少要写 min 或 max")

        expect = raw.get("expect", "pass")
        if expect not in ("pass", "fail"):
            raise RuleConfigError(f"规则 {rid}：expect 只能是 pass 或 fail")
        if expect == "fail" and not raw.get("known_issue"):
            raise RuleConfigError(f"规则 {rid}：expect: fail 必须写 known_issue 说明是什么缺陷")
        origin = raw.get("origin")
        if expect == "fail" and origin not in ORIGINS:
            raise RuleConfigError(f"规则 {rid}：expect: fail 必须写 origin（planted 或 found）")

        rules.append(Rule(
            id=rid,
            type=rtype,
            desc=raw.get("desc") or "",
            params=params,
            category=raw.get("category") or CATEGORY_BY_TYPE[rtype],
            severity=raw.get("severity", "error"),
            expect=expect,
            known_issue=raw.get("known_issue"),
            origin=origin if expect == "fail" else None,
        ))
    return rules


def load_rules(path: str | Path) -> list[Rule]:
    return parse_rules(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


# ------------------------------------------------------------------ 编译成 SQL


def _quote(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return repr(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def _where(params: dict, prefix: str = "AND") -> str:
    cond = params.get("where")
    return f" {prefix} ({cond})" if cond else ""


def _source(params: dict) -> str:
    """带 where 时先在子查询里过滤，这样 where 里直接写字段名，不用管别名。"""
    t = params["table"]
    return f"(SELECT * FROM {t} WHERE {params['where']})" if params.get("where") else t


def _bad_condition(rule: Rule) -> str:
    """字段级规则里「这一行不合格」的条件。"""
    p = rule.params
    col = p.get("column")
    if rule.type == "not_null":
        return f"{col} IS NULL"
    if rule.type == "enum":
        values = ", ".join(_quote(v) for v in p["values"])
        return f"{col} IS NOT NULL AND {col} NOT IN ({values})"
    if rule.type == "range":
        parts = []
        if p.get("min") is not None:
            parts.append(f"{col} < {_quote(p['min'])}")
        if p.get("max") is not None:
            parts.append(f"{col} > {_quote(p['max'])}")
        return f"{col} IS NOT NULL AND ({' OR '.join(parts)})"
    if rule.type == "row_check":
        return f"NOT ({p['check']})"
    raise AssertionError(rule.type)


def build_sql(rule: Rule) -> dict[str, str]:
    """返回这条规则要执行的 SQL：main 用于判定，sample 用于展示失败样例。"""
    p = rule.params
    t = p.get("table")
    if rule.type == "row_count":
        return {"main": f"SELECT COUNT(*) AS total FROM {t}{_where(p, 'WHERE')}"}

    if rule.type == "unique":
        cols = ", ".join(p["columns"])
        dup = f"SELECT {cols}, COUNT(*) AS dup_count FROM {t}{_where(p, 'WHERE')} GROUP BY {cols} HAVING COUNT(*) > 1"
        return {
            "main": f"SELECT COUNT(*) AS bad, COALESCE(SUM(dup_count), 0) AS bad_rows FROM ({dup}) x",
            "sample": f"{dup} ORDER BY dup_count DESC LIMIT {SAMPLE_LIMIT}",
        }

    if rule.type in ("not_null", "enum", "range", "row_check"):
        bad = _bad_condition(rule)
        return {
            "main": (f"SELECT COUNT(*) AS total, "
                     f"COALESCE(SUM(CASE WHEN {bad} THEN 1 ELSE 0 END), 0) AS bad "
                     f"FROM {t}{_where(p, 'WHERE')}"),
            "sample": f"SELECT * FROM {t} WHERE ({bad}){_where(p)} LIMIT {SAMPLE_LIMIT}",
        }

    if rule.type == "reference":
        c, rt, rc = p["column"], p["ref_table"], p["ref_column"]
        orphan = (f"FROM {_source(p)} s LEFT JOIN (SELECT DISTINCT {rc} AS k FROM {rt}) r ON s.{c} = r.k "
                  f"WHERE s.{c} IS NOT NULL AND r.k IS NULL")
        return {
            "main": f"SELECT COUNT(*) AS bad, COUNT(DISTINCT s.{c}) AS bad_values {orphan}",
            "sample": f"SELECT s.{c} AS value, COUNT(*) AS rows_count {orphan} GROUP BY s.{c} ORDER BY s.{c} LIMIT {SAMPLE_LIMIT}",
        }

    if rule.type == "coverage":
        c, tt, tc = p["column"], p["target_table"], p["target_column"]
        tw = f" WHERE {p['target_where']}" if p.get("target_where") else ""
        missing = (f"SELECT DISTINCT s.{c} AS k FROM {_source(p)} s "
                   f"LEFT JOIN (SELECT DISTINCT {tc} AS k FROM {tt}{tw}) x ON s.{c} = x.k "
                   f"WHERE s.{c} IS NOT NULL AND x.k IS NULL")
        return {
            "main": (f"SELECT COUNT(*) AS bad, MIN(k) AS first_missing, MAX(k) AS last_missing, "
                     f"(SELECT COUNT(DISTINCT {c}) FROM {t}{_where(p, 'WHERE')}) AS total FROM ({missing}) m"),
        }

    if rule.type == "reconcile":
        return {"left": p["left"].strip().rstrip(";"), "right": p["right"].strip().rstrip(";")}

    raise AssertionError(rule.type)


# ------------------------------------------------------------------ 判定


def _num(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, decimal.Decimal):
        return float(v)
    return float(v)


def _plain(row: dict) -> dict:
    return {k: (float(v) if isinstance(v, decimal.Decimal) else v) for k, v in row.items()}


def _pct(part: float, whole: float) -> str:
    return f"{part / whole:.2%}" if whole else "-"


def compare_rows(left: list[dict], right: list[dict], *, abs_tol: float = 0.01,
                 rel_tol: float = 0.0, scope: str = "both") -> dict:
    """对账：两边都是「若干键列 + 一个 v 列」，按键配对后比较 v。

    scope=both   两边的键都必须对得上（缺一边算不一致）
    scope=left   以左边为准，右边多出来的键不管
    scope=common 只比较两边都有的键（覆盖问题交给 coverage 规则）
    """
    def index(rows: list[dict]) -> dict[tuple, float | None]:
        out = {}
        for r in rows:
            if "v" not in r:
                raise RuleConfigError("对账 SQL 必须返回名为 v 的数值列")
            key = tuple(r[k] for k in r if k != "v")
            out[key] = _num(r["v"])
        return out

    li, ri = index(left), index(right)
    only_left = [k for k in li if k not in ri]
    only_right = [k for k in ri if k not in li]
    diffs = []
    for k in li.keys() & ri.keys():
        a, b = li[k], ri[k]
        if a is None or b is None:
            if a != b:
                diffs.append((k, a, b))
            continue
        gap = abs(a - b)
        limit = max(abs_tol, rel_tol * max(abs(a), abs(b)))
        if gap > limit:
            diffs.append((k, a, b))

    bad = len(diffs)
    if scope in ("both", "left"):
        bad += len(only_left)
    if scope == "both":
        bad += len(only_right)
    diffs.sort(key=lambda d: -abs((d[1] or 0) - (d[2] or 0)))
    return {
        "bad": bad,
        "matched": len(li.keys() & ri.keys()),
        "diffs": diffs,
        "only_left": sorted(only_left, key=str),
        "only_right": sorted(only_right, key=str),
        "left_total": sum(v or 0 for v in li.values()),
        "right_total": sum(v or 0 for v in ri.values()),
    }


def _fmt_key(key: tuple) -> str:
    return " / ".join(str(k) for k in key) if key else "(整体)"


def _fmt_num(v: float | None) -> str:
    if v is None:
        return "NULL"
    return f"{v:,.4f}".rstrip("0").rstrip(".")


async def run_rule(rule: Rule, query: QueryFn) -> RuleResult:
    sqls = build_sql(rule)
    p = rule.params
    try:
        if rule.type == "reconcile":
            left, right = await query(sqls["left"]), await query(sqls["right"])
            cmp = compare_rows(left, right, abs_tol=float(p.get("abs_tol", 0.01)),
                               rel_tol=float(p.get("rel_tol", 0.0)), scope=p.get("scope", "both"))
            parts = [f"配对 {cmp['matched']} 组，不一致 {len(cmp['diffs'])} 组"]
            if p.get("scope", "both") in ("both", "left") and cmp["only_left"]:
                parts.append(f"右侧缺 {len(cmp['only_left'])} 组")
            if p.get("scope", "both") == "both" and cmp["only_right"]:
                parts.append(f"左侧缺 {len(cmp['only_right'])} 组")
            if all(len(r) == 1 for r in left + right):  # 没有键列，是两个标量在比
                parts = [f"左 {_fmt_num(cmp['left_total'])}，右 {_fmt_num(cmp['right_total'])}"]
            else:
                parts.append(f"左 {_fmt_num(cmp['left_total'])}，右 {_fmt_num(cmp['right_total'])}")
            samples = [{"键": _fmt_key(k), "左": _fmt_num(a), "右": _fmt_num(b),
                        "差额": _fmt_num((a or 0) - (b or 0))} for k, a, b in cmp["diffs"][:SAMPLE_LIMIT]]
            samples += [{"键": _fmt_key(k), "左": "有", "右": "缺"} for k in cmp["only_left"][:SAMPLE_LIMIT]]
            status = "FAIL" if cmp["bad"] else "PASS"
            return RuleResult(rule, status, "，".join(parts), samples, list(sqls.values()))

        rows = await query(sqls["main"])
        row = _plain(rows[0]) if rows else {}

        if rule.type == "row_count":
            total = int(row.get("total") or 0)
            lo, hi = p.get("min", 1), p.get("max")
            ok = total >= lo and (hi is None or total <= hi)
            want = f"≥ {lo}" + (f" 且 ≤ {hi}" if hi is not None else "")
            return RuleResult(rule, "PASS" if ok else "FAIL", f"{total} 行（要求 {want}）", sql=[sqls["main"]])

        bad = int(row.get("bad") or 0)
        total = int(row.get("total") or 0)
        if rule.type == "unique":
            observed = f"{bad} 组键重复，涉及 {int(row.get('bad_rows') or 0)} 行"
            failed = bad > 0
        elif rule.type == "reference":
            observed = f"{bad} 行找不到对应记录（{int(row.get('bad_values') or 0)} 个不同取值）"
            failed = bad > 0
        elif rule.type == "coverage":
            observed = f"{total} 个取值里缺 {bad} 个"
            if bad:
                observed += f"，范围 {row.get('first_missing')} ~ {row.get('last_missing')}"
            failed = bad > 0
        else:
            max_rate = float(p.get("max_rate", 0))
            observed = f"{bad}/{total} 行不合格（{_pct(bad, total)}）"
            if max_rate:
                observed += f"，允许 {max_rate:.2%}"
            failed = total > 0 and bad / total > max_rate

        samples: list[dict] = []
        if failed and "sample" in sqls:
            samples = [_plain(r) for r in await query(sqls["sample"])]
        return RuleResult(rule, "FAIL" if failed else "PASS", observed, samples, list(sqls.values()))
    except RuleConfigError:
        raise
    except Exception as exc:  # noqa: BLE001 - 单条规则出错不影响其它规则
        msg = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__
        return RuleResult(rule, "ERROR", msg, sql=list(sqls.values()))


async def run_rules(rules: list[Rule], query: QueryFn) -> list[RuleResult]:
    return [await run_rule(r, query) for r in rules]


# ------------------------------------------------------------------ 报告


def summarize(results: list[RuleResult]) -> dict[str, int]:
    out = {"total": len(results)}
    for name in ("通过", "已知缺陷", "新问题", "已知缺陷未检出", "执行出错"):
        out[name] = sum(1 for r in results if r.outcome == name)
    for key, label in ORIGINS.items():
        out[label] = sum(1 for r in results if r.outcome == "已知缺陷" and r.rule.origin == key)
    return out


def _outcome_label(r: RuleResult) -> str:
    if r.outcome == "已知缺陷" and r.rule.origin:
        return f"已知缺陷（{ORIGINS[r.rule.origin]}）"
    return r.outcome


def _md_cell(v: Any) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


def render_markdown(results: list[RuleResult], *, title: str = "数据质量报告",
                    generated_at: str | None = None) -> str:
    s = summarize(results)
    lines = [f"# {title}", ""]
    if generated_at:
        lines += [f"生成时间：{generated_at}", ""]
    lines += [
        f"共 {s['total']} 条规则：通过 {s['通过']}，"
        f"已知缺陷 {s['已知缺陷']}（刻意埋设 {s['刻意埋设']}，规则发现 {s['规则发现']}），"
        f"新问题 {s['新问题']}，已知缺陷未检出 {s['已知缺陷未检出']}，执行出错 {s['执行出错']}。",
        "",
        "- **已知缺陷**：规则失败，但问题已经登记过，规则按预期把它查了出来。"
        "「刻意埋设」是造数时故意放进去、用来检验问答系统会不会选错表的坑；"
        "「规则发现」是规则跑出来的真实问题，已登记、暂不处理。",
        "- **新问题**：没有登记过的失败，需要排查。",
        "- **已知缺陷未检出**：登记过的问题这次没查出来，可能已经修好，也可能规则失效了，需要确认后更新规则。",
        "",
    ]
    by_cat: dict[str, list[RuleResult]] = {}
    for r in results:
        by_cat.setdefault(r.rule.category, []).append(r)
    cats = [c for c in CATEGORY_ORDER if c in by_cat] + [c for c in by_cat if c not in CATEGORY_ORDER]
    for cat in cats:
        lines += [f"## {cat}", "", "| 规则 | 说明 | 涉及表 | 结果 | 观测值 |", "|---|---|---|---|---|"]
        for r in by_cat[cat]:
            lines.append(f"| `{r.rule.id}` | {_md_cell(r.rule.desc)} | {_md_cell(', '.join(r.rule.tables))} "
                         f"| {_outcome_label(r)} | {_md_cell(r.observed)} |")
        lines.append("")

    failed = [r for r in results if r.status != "PASS"]
    if failed:
        lines += ["## 失败明细", ""]
        for r in failed:
            lines += [f"### `{r.rule.id}`：{_outcome_label(r)}", "", f"{r.rule.desc}", "", f"- 观测值：{r.observed}"]
            if r.rule.known_issue:
                lines.append(f"- 已登记的原因：{r.rule.known_issue}")
            if r.samples:
                cols = list(r.samples[0].keys())
                for smp in r.samples[1:]:
                    cols += [k for k in smp if k not in cols]
                lines += ["", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
                for smp in r.samples:
                    lines.append("| " + " | ".join(_md_cell(smp.get(c, "")) for c in cols) + " |")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_console(results: list[RuleResult]) -> str:
    mark = {"通过": "通过", "已知缺陷": "已知", "新问题": "新问题", "已知缺陷未检出": "未检出", "执行出错": "出错"}
    lines = []
    for r in results:
        lines.append(f"  [{mark[r.outcome]:<3}] {r.rule.id:<36} {r.observed}")
    s = summarize(results)
    lines.append("")
    lines.append(f"共 {s['total']} 条：通过 {s['通过']}，已知缺陷 {s['已知缺陷']}，新问题 {s['新问题']}，"
                 f"已知缺陷未检出 {s['已知缺陷未检出']}，执行出错 {s['执行出错']}")
    return "\n".join(lines)
