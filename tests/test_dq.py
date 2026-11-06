"""数据质量规则引擎的离线自检：用内存 SQLite 代替数仓，不需要连 MySQL。

    python -m unittest discover -s tests -v
"""
import asyncio
import sqlite3
import unittest
from pathlib import Path

from app.core.sql_guard import is_read_only
from app.governance.dq import (RuleConfigError, build_sql, compare_rows, load_rules, parse_rules,
                               render_markdown, run_rules, summarize)

ROOT = Path(__file__).resolve().parent.parent


def make_db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE dim_date (date_id INT, quarter TEXT);
        INSERT INTO dim_date VALUES (20250101, 'Q1'), (20250102, 'Q1'), (20250103, 'Q5');
        CREATE TABLE fact (id TEXT, date_id INT, amount REAL, status TEXT);
        INSERT INTO fact VALUES ('A', 20250101, 10, 'ok'), ('B', 20250102, 20, 'ok'),
                                ('B', 20250102, 5, 'bad'), ('C', 20250132, NULL, 'ok');
        CREATE TABLE agg (date_id INT, v REAL);
        INSERT INTO agg VALUES (20250101, 10), (20250102, 99);
    """)

    async def query(sql):
        return [dict(r) for r in con.execute(sql).fetchall()]

    return query


def run(rules_yaml: list[dict]):
    rules = parse_rules({"rules": rules_yaml})
    return asyncio.run(run_rules(rules, make_db()))


class TestRuleTypes(unittest.TestCase):
    def test_行数(self):
        r, = run([{"id": "r", "type": "row_count", "table": "fact", "min": 5}])
        self.assertEqual(r.status, "FAIL")
        self.assertIn("4 行", r.observed)

    def test_唯一(self):
        r, = run([{"id": "u", "type": "unique", "table": "fact", "columns": ["id"]}])
        self.assertEqual(r.status, "FAIL")
        self.assertEqual(r.samples[0]["id"], "B")

    def test_非空_容忍比例(self):
        strict, loose = run([
            {"id": "a", "type": "not_null", "table": "fact", "column": "amount"},
            {"id": "b", "type": "not_null", "table": "fact", "column": "amount", "max_rate": 0.3},
        ])
        self.assertEqual(strict.status, "FAIL")
        self.assertEqual(loose.status, "PASS")

    def test_枚举与范围(self):
        enum, rng = run([
            {"id": "e", "type": "enum", "table": "dim_date", "column": "quarter", "values": ["Q1", "Q2", "Q3", "Q4"]},
            {"id": "r", "type": "range", "table": "fact", "column": "amount", "min": 6},
        ])
        self.assertEqual(enum.status, "FAIL")
        self.assertIn("1/3", enum.observed)
        self.assertEqual(rng.status, "FAIL")
        self.assertIn("1/4", rng.observed)  # NULL 不算越界

    def test_外键_带过滤(self):
        all_rows, filtered = run([
            {"id": "a", "type": "reference", "table": "fact", "column": "date_id",
             "ref_table": "dim_date", "ref_column": "date_id"},
            {"id": "b", "type": "reference", "table": "fact", "column": "date_id",
             "ref_table": "dim_date", "ref_column": "date_id", "where": "status = 'ok' AND id <> 'C'"},
        ])
        self.assertEqual(all_rows.status, "FAIL")
        self.assertEqual(all_rows.samples[0]["value"], 20250132)
        self.assertEqual(filtered.status, "PASS")

    def test_覆盖(self):
        r, = run([{"id": "c", "type": "coverage", "table": "dim_date", "column": "date_id",
                   "target_table": "agg", "target_column": "date_id"}])
        self.assertEqual(r.status, "FAIL")
        self.assertIn("缺 1 个", r.observed)
        self.assertIn("20250103", r.observed)

    def test_行级条件(self):
        r, = run([{"id": "c", "type": "row_check", "table": "fact", "check": "status = 'ok'"}])
        self.assertEqual(r.status, "FAIL")
        self.assertEqual(r.samples[0]["status"], "bad")

    def test_对账(self):
        r, = run([{"id": "rec", "type": "reconcile", "scope": "left",
                   "left": "SELECT date_id AS d, v FROM agg",
                   "right": "SELECT date_id AS d, SUM(amount) AS v FROM fact GROUP BY date_id"}])
        self.assertEqual(r.status, "FAIL")
        self.assertIn("不一致 1 组", r.observed)
        self.assertEqual(r.samples[0]["键"], "20250102")

    def test_SQL出错不影响其它规则(self):
        bad, good = run([
            {"id": "x", "type": "row_count", "table": "no_such_table"},
            {"id": "y", "type": "row_count", "table": "fact"},
        ])
        self.assertEqual(bad.status, "ERROR")
        self.assertEqual(good.status, "PASS")


class TestCompareRows(unittest.TestCase):
    def test_范围(self):
        left = [{"k": 1, "v": 1.0}, {"k": 2, "v": 2.0}]
        right = [{"k": 1, "v": 1.0}, {"k": 3, "v": 3.0}]
        self.assertEqual(compare_rows(left, right, scope="both")["bad"], 2)
        self.assertEqual(compare_rows(left, right, scope="left")["bad"], 1)
        self.assertEqual(compare_rows(left, right, scope="common")["bad"], 0)

    def test_相对误差(self):
        left, right = [{"v": 1000.0}], [{"v": 1000.5}]
        self.assertEqual(compare_rows(left, right, abs_tol=0.01)["bad"], 1)
        self.assertEqual(compare_rows(left, right, abs_tol=0.01, rel_tol=0.001)["bad"], 0)

    def test_缺v列报配置错(self):
        with self.assertRaises(RuleConfigError):
            compare_rows([{"x": 1}], [{"x": 1}])


class TestExpectations(unittest.TestCase):
    def test_四种结论(self):
        results = run([
            {"id": "ok", "type": "row_count", "table": "fact"},
            {"id": "new", "type": "row_count", "table": "fact", "min": 100},
            {"id": "known", "type": "row_count", "table": "fact", "min": 100,
             "expect": "fail", "origin": "planted", "known_issue": "演示"},
            {"id": "gone", "type": "row_count", "table": "fact",
             "expect": "fail", "origin": "found", "known_issue": "以前有问题"},
        ])
        self.assertEqual([r.outcome for r in results], ["通过", "新问题", "已知缺陷", "已知缺陷未检出"])
        self.assertEqual([r.unexpected for r in results], [False, True, False, True])
        s = summarize(results)
        self.assertEqual((s["刻意埋设"], s["规则发现"]), (1, 0))
        md = render_markdown(results)
        self.assertIn("已知缺陷（刻意埋设）", md)
        self.assertIn("## 失败明细", md)


class TestConfig(unittest.TestCase):
    def test_配置错误(self):
        cases = [
            {"id": "a", "type": "nope", "table": "t"},
            {"id": "a", "type": "unique", "table": "t"},
            {"id": "a", "type": "row_count", "table": "t; DROP TABLE x"},
            {"id": "a", "type": "range", "table": "t", "column": "c"},
            {"id": "a", "type": "row_count", "table": "t", "expect": "fail"},
            {"id": "a", "type": "row_count", "table": "t", "expect": "fail", "known_issue": "x"},
        ]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(RuleConfigError):
                parse_rules({"rules": [raw]})

    def test_id重复(self):
        with self.assertRaises(RuleConfigError):
            parse_rules({"rules": [{"id": "a", "type": "row_count", "table": "t"}] * 2})

    def test_仓库里的规则文件能解析并编译(self):
        rules = load_rules(ROOT / "conf" / "dq_rules.yaml")
        self.assertGreaterEqual(len(rules), 30)
        for r in rules:
            with self.subTest(rule=r.id):
                for sql in build_sql(r).values():
                    ok, reason = is_read_only(sql)
                    self.assertTrue(ok, f"{sql} -> {reason}")
        self.assertEqual({r.category for r in rules}, {"表级", "字段级", "跨表对账", "口径一致性"})


if __name__ == "__main__":
    unittest.main()
