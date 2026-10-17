"""
SQL 只读守卫。

背景：需求文档要求「禁止生成写入 / 更新 / 删除类 SQL」，但原来的实现只在
`validata_sql` 里跑一次 `EXPLAIN`——语法合法就放行，`execute_sql` 直接执行。
也就是说 `DELETE FROM ...` 只要语法正确，就会被真的执行到数仓上。

本模块提供一道独立于模型的硬拦截：执行前对 SQL 做静态判断，只放行只读语句。
放在 `app/core/` 是因为它不依赖任何业务对象，是纯函数，可以直接被离线测试覆盖。

设计取舍：
- **白名单判断语句首关键字**，而不是黑名单匹配全文。黑名单容易被
  `SELECT ... ; DROP TABLE` 这类多语句注入绕过，白名单则天然拒掉未知语句类型。
- **全文扫描写关键字作为第二道保险**，用于拦住 `WITH ... DELETE`（MySQL 8 支持，
  首关键字是 with 但本质是写操作）这类情况。
- 扫描前先剥离注释与字符串字面量，避免 `WHERE name = 'delete'` 这类误判。
"""

from __future__ import annotations

import re

__all__ = ["NonReadOnlySQLError", "is_read_only", "assert_read_only"]


class NonReadOnlySQLError(ValueError):
    """SQL 不是只读语句，拒绝执行。"""


# 允许执行的语句首关键字（小写比较）
_ALLOWED_HEADS = frozenset({"select", "with", "show", "describe", "desc", "explain"})

# 元数据类语句后面跟的是对象描述而非可执行语句（`SHOW CREATE TABLE` 里就有 create），
# 这类语句不参与下面的全文写关键字扫描，否则会误判
_META_HEADS = frozenset({"show", "describe", "desc", "explain"})

# 第二道保险：出现在语句任何位置都判为写操作。
# \b 边界保证 `deleted_at` / `update_log` 这类字段名不会被误伤
# （delete 后面紧跟 `_` 属于 \w，\b 不成立）。
_WRITE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|replace|truncate|create|drop|alter|rename|"
    r"grant|revoke|lock|unlock|call|load_file|handler)\b",
    re.IGNORECASE,
)

# SELECT ... INTO OUTFILE / DUMPFILE 会把结果写到数据库服务器磁盘上，
# 是只读外壳下的写操作，单独拦掉
_INTO_FILE = re.compile(r"\binto\s+(outfile|dumpfile)\b", re.IGNORECASE)

# 加锁读：会阻塞其它会话的写入，只读场景不应出现
_LOCKING_READ = re.compile(r"\b(for\s+update|lock\s+in\s+share\s+mode)\b", re.IGNORECASE)

# 行注释 / 块注释 / 字符串字面量，判断前先剥掉，避免注释里的关键字造成误判
# 注意：不能把 `;` 当注释起始符吃掉，否则多语句检测会失效
_LINE_COMMENT = re.compile(r"(--\s|#)[^\n]*", re.IGNORECASE)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_LITERAL = re.compile(r"'(?:''|\\.|[^'\\])*'|\"(?:\"\"|\\.|[^\"\\])*\"")


def _strip_noise(sql: str) -> str:
    """去掉注释与字符串字面量，只留下参与语法判断的部分。"""
    text = _BLOCK_COMMENT.sub(" ", sql)
    text = _STRING_LITERAL.sub(" '' ", text)
    text = _LINE_COMMENT.sub(" ", text)
    return text.strip()


def _split_statements(sql: str) -> list[str]:
    """按分号切分，返回非空语句片段。"""
    return [part.strip() for part in sql.split(";") if part.strip()]


def is_read_only(sql: str) -> tuple[bool, str]:
    """
    判断 SQL 是否为只读语句。

    :return: (是否只读, 拒绝原因)。只读时原因为空字符串。
    """
    if not sql or not sql.strip():
        return False, "SQL 为空"

    cleaned = _strip_noise(sql)

    parts = _split_statements(cleaned)
    # 多语句：典型形态是 `SELECT ...; DROP TABLE x`，单看首关键字放行会中招
    if len(parts) > 1:
        return False, f"检测到 {len(parts)} 条语句，只允许执行单条只读语句"
    if not parts:
        return False, "SQL 为空"

    statement = parts[0]
    head = re.split(r"\s+|\(", statement, maxsplit=1)[0].lower()
    if head not in _ALLOWED_HEADS:
        return False, f"语句类型 `{head}` 不在只读白名单 {sorted(_ALLOWED_HEADS)} 内"

    if _INTO_FILE.search(statement):
        return False, "SELECT ... INTO OUTFILE / DUMPFILE 属于写文件操作，已拒绝"

    if _LOCKING_READ.search(statement):
        return False, "加锁读（FOR UPDATE / LOCK IN SHARE MODE）不属于只读查询，已拒绝"

    # 全文扫描兜底：拦住 `WITH ... DELETE` 这类首关键字合法但本质是写操作的语句。
    # 元数据类语句（SHOW / DESCRIBE / EXPLAIN）跳过——它们不是可执行语句，
    # 且 `SHOW CREATE TABLE` 这种会命中 create 造成误判。
    if head not in _META_HEADS:
        hit = _WRITE_KEYWORDS.search(statement)
        if hit:
            return False, f"语句中包含写操作关键字 `{hit.group(1).upper()}`"

    return True, ""


def assert_read_only(sql: str, *, stage: str = "execute_sql") -> None:
    """
    校验 SQL 只读，不只读则抛 `NonReadOnlySQLError`。

    :param sql: 待执行的 SQL
    :param stage: 调用位置，写进错误信息便于定位是校验阶段还是执行阶段拦下的
    """
    ok, reason = is_read_only(sql)
    if not ok:
        raise NonReadOnlySQLError(f"[{stage}] 拒绝执行非只读 SQL：{reason}")
