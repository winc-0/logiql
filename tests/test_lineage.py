"""字段血缘的离线自检，不需要连数据库。

    python -m unittest discover -s tests -v
"""
import json
import unittest
from pathlib import Path

from app.governance.dq import load_rules
from app.governance.lineage import (columns_in_sql, find_cycles, gold_coverage, load_lineage, load_schema,
                                    parse_ddl, parse_lineage, roots, trace_down, trace_up, traceable, validate)

ROOT = Path(__file__).resolve().parent.parent

SCHEMA = {
    "src": ["id", "amount", "day"],
    "dim": ["id", "name"],
    "agg": ["day", "total"],
}


def lineage(tables: dict, systems=None):
    return parse_lineage({"systems": systems or {"ERP": "业务系统"}, "tables": tables})


GOOD = {
    "src": {"layer": "DWD", "system": "ERP"},
    "dim": {"layer": "DIM", "system": "ERP"},
    "agg": {"layer": "DWS", "columns": {
        "day": {"from": "src.day"},
        "total": {"from": "src.amount", "transform": "求和", "verified_by": "rule_a"},
    }},
}


class TestParseDDL(unittest.TestCase):
    def test_取字段并忽略约束行(self):
        ddl = """
        CREATE TABLE t1 (
          a INT PRIMARY KEY COMMENT 'x(1)',
          b DECIMAL(12,2) COMMENT '金额',
          PRIMARY KEY (a, b)
        ) ENGINE=InnoDB COMMENT='说明';
        CREATE TABLE `t2` (`c` VARCHAR(20));
        """
        self.assertEqual(parse_ddl(ddl), {"t1": ["a", "b"], "t2": ["c"]})


class TestValidate(unittest.TestCase):
    def test_完整的血缘没有问题(self):
        self.assertEqual(validate(lineage(GOOD), SCHEMA, {"rule_a"}), [])

    def test_能发现各种缺口(self):
        bad = {
            "src": {"layer": "DWD", "system": "ERP"},
            "agg": {"layer": "XXX", "columns": {
                "day": {"from": "src.nope"},
                "ghost": {"system": "ERP"},
                "total": {"from": "src.amount", "verified_by": "no_such_rule"},
            }},
            "extra": {"layer": "DIM", "system": "CRM"},
        }
        text = "\n".join(validate(lineage(bad), SCHEMA, {"rule_a"}))
        for expected in ("表 dim 没有血缘声明", "layer 不合法", "上游 src.nope 不存在",
                         "agg.ghost 在建表语句里不存在", "不存在的质量规则 no_such_rule",
                         "表 extra 在建表语句里不存在"):
            self.assertIn(expected, text)

    def test_没写来源的字段(self):
        tables = dict(GOOD, agg={"layer": "DWS", "columns": {"day": {"from": "src.day"}}})
        self.assertIn("字段 agg.total 没有血缘", "\n".join(validate(lineage(tables), SCHEMA)))

    def test_环(self):
        tables = {"src": {"layer": "DWD", "columns": {
            "id": {"from": "src.amount"}, "amount": {"from": "src.id"}, "day": {"system": "ERP"}}}}
        self.assertTrue(find_cycles(lineage(tables)))


class TestTrace(unittest.TestCase):
    def test_溯源与影响(self):
        lin = lineage(GOOD)
        tree = trace_up(lin, "agg.total")
        self.assertEqual(tree["verified_by"], ["rule_a"])
        self.assertEqual(tree["upstream"][0]["ref"], "src.amount")
        self.assertEqual(roots(tree), {"ERP"})
        self.assertTrue(traceable(lin, "agg.total"))
        self.assertEqual(trace_down(lin, "src.amount"), ["agg.total"])
        self.assertEqual(trace_down(lin, "dim.name"), [])

    def test_上游断了就追不到(self):
        lin = lineage({"agg": {"layer": "DWS", "columns": {"total": {"from": "src.amount"}}}})
        self.assertFalse(traceable(lin, "agg.total"))


class TestColumnsInSQL(unittest.TestCase):
    def test_别名与无前缀字段(self):
        sql = ("SELECT d.name AS 名称, SUM(amount) AS 合计 FROM src s JOIN dim d ON s.id = d.id "
               "WHERE d.name = 'day' GROUP BY d.name")
        self.assertEqual(columns_in_sql(sql, SCHEMA), {"dim.name", "src.amount", "src.id", "dim.id"})

    def test_有歧义的无前缀字段不算(self):
        # day 在 src 和 agg 里都有，没写前缀就不知道是哪张表的，不算；total 只有 agg 有
        sql = "SELECT day, total FROM src JOIN agg ON src.id = agg.total"
        self.assertEqual(columns_in_sql(sql, SCHEMA), {"src.id", "agg.total"})


class TestRepoLineage(unittest.TestCase):
    """仓库里的血缘配置必须和建表语句、质量规则、评测集保持一致。"""

    @classmethod
    def setUpClass(cls):
        cls.schema = load_schema([ROOT / "docker/mysql/dw.sql", ROOT / "docker/mysql/dw_large.sql"])
        cls.lineage = load_lineage(ROOT / "conf/lineage.yaml")
        cls.rule_ids = {r.id for r in load_rules(ROOT / "conf/dq_rules.yaml")}
        lines = (ROOT / "eval/datasets/nl2sql_gold.jsonl").read_text(encoding="utf-8").splitlines()
        cls.gold = [json.loads(x) for x in lines if x.strip()]

    def test_血缘自检无问题(self):
        self.assertEqual(validate(self.lineage, self.schema, self.rule_ids), [])

    def test_评测集字段全部可追溯(self):
        cov = gold_coverage(self.lineage, self.schema, self.gold)
        self.assertEqual(cov["missing"], [])
        self.assertEqual(len(cov["questions_ok"]), len(self.gold))
        self.assertIn("fact_outbound.outbound_amount", cov["columns"])

    def test_验证边引用的规则都是预期通过的(self):
        failing = {r.id for r in load_rules(ROOT / "conf/dq_rules.yaml") if r.expect == "fail"}
        for src, dst, cl in self.lineage.edges():
            with self.subTest(edge=f"{src} -> {dst}"):
                self.assertFalse(set(cl.verified_by) & failing)


if __name__ == "__main__":
    unittest.main()
