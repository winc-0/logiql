"""
SQL 只读守卫的离线自检。

纯标准库（unittest），不需要 pytest、不需要连数据库，直接跑：

    python -m unittest discover -s tests -v

覆盖三类场景：正常只读语句必须放行、写操作必须拦下、
「看着像写操作但实际是只读」的语句不能误伤。
"""

import unittest

from app.core.sql_guard import NonReadOnlySQLError, assert_read_only, is_read_only


class TestReadOnlyAccepted(unittest.TestCase):
    def _ok(self, sql):
        ok, reason = is_read_only(sql)
        self.assertTrue(ok, f"应放行但被拒：{sql!r} -> {reason}")

    def test_基本查询(self):
        for sql in (
            "SELECT * FROM orders",
            "select 1",
            "SeLeCt order_id, amount FROM dw.fact_order WHERE dt > '2025-01-01'",
            "SELECT COUNT(*) AS cnt FROM orders GROUP BY region ORDER BY cnt DESC LIMIT 10",
            "SELECT * FROM orders;",  # 结尾分号
            "  \n  SELECT 1  \n ",  # 前后空白
        ):
            with self.subTest(sql=sql):
                self._ok(sql)

    def test_cte(self):
        self._ok("WITH t AS (SELECT region, SUM(amount) AS s FROM orders GROUP BY region) "
                 "SELECT * FROM t ORDER BY s DESC")

    def test_元数据类语句(self):
        for sql in ("SHOW TABLES", "show create table orders", "DESCRIBE orders", "EXPLAIN SELECT 1"):
            with self.subTest(sql=sql):
                self._ok(sql)

    def test_注释与字符串不误伤(self):
        # 字符串字面量里的 delete 不是语句
        self._ok("SELECT * FROM t WHERE name = 'delete'")
        self._ok('SELECT * FROM t WHERE name = "drop table"')
        # 字段名里含写关键字：deleted_at / update_log / is_inserted
        self._ok("SELECT deleted_at, update_log FROM t")
        self._ok("SELECT is_inserted FROM t WHERE update_time > '2025-01-01'")
        # 行注释与块注释
        self._ok("-- 查询订单\nSELECT 1")
        self._ok("# 查询订单\nSELECT 1")


class TestWriteRejected(unittest.TestCase):
    def _no(self, sql, keyword=""):
        ok, reason = is_read_only(sql)
        self.assertFalse(ok, f"应拒绝但放行了：{sql!r}")
        if keyword:
            self.assertIn(keyword, reason)

    def test_dml(self):
        for sql in (
            "DELETE FROM orders",
            "delete from orders where amount < 0",
            "UPDATE orders SET amount = 0",
            "INSERT INTO orders (id) VALUES (1)",
            "REPLACE INTO orders (id) VALUES (1)",
            "TRUNCATE TABLE orders",
        ):
            with self.subTest(sql=sql):
                self._no(sql)

    def test_ddl与其它(self):
        for sql in (
            "DROP TABLE orders",
            "CREATE TABLE t (id INT)",
            "ALTER TABLE orders ADD COLUMN x INT",
            "RENAME TABLE a TO b",
            "GRANT ALL ON *.* TO 'u'@'%'",
            "LOCK TABLES orders WRITE",
            "CALL proc_backup()",
        ):
            with self.subTest(sql=sql):
                self._no(sql)

    def test_多语句注入(self):
        self._no("SELECT 1; DROP TABLE orders", "条语句")
        self._no("SELECT 1;DELETE FROM orders", "条语句")

    def test_cte写操作(self):
        # MySQL 8 支持 WITH ... DELETE，首关键字是 with 但本质是写
        self._no("WITH c AS (SELECT id FROM orders WHERE amount < 0) DELETE FROM orders WHERE id IN (SELECT id FROM c)")

    def test_只读外壳下的写操作(self):
        self._no("SELECT * FROM orders INTO OUTFILE '/tmp/a.csv'", "OUTFILE")
        self._no("SELECT * FROM orders FOR UPDATE", "加锁")

    def test_空语句(self):
        for sql in ("", "   ", "\n\t", ";", "-- 只有注释\n"):
            with self.subTest(sql=sql):
                self._no(sql)


class TestAssertReadOnly(unittest.TestCase):
    def test_只读不抛异常(self):
        self.assertIsNone(assert_read_only("SELECT 1"))

    def test_写操作抛异常且带阶段信息(self):
        with self.assertRaises(NonReadOnlySQLError) as ctx:
            assert_read_only("DROP TABLE orders", stage="execute_sql")
        self.assertIn("execute_sql", str(ctx.exception))
        self.assertIn("拒绝执行非只读", str(ctx.exception))

    def test_异常是ValueError子类(self):
        # 上层按 ValueError 统一兜底也能接住
        self.assertTrue(issubclass(NonReadOnlySQLError, ValueError))


if __name__ == "__main__":
    unittest.main(verbosity=2)
