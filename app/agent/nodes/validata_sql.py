import asyncio
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger
from app.core.sql_guard import NonReadOnlySQLError, assert_read_only


async def validata_sql(state:DataAgentState,runtime:Runtime[DataAgentContext]):

    # 获取流对象
    writer = runtime.stream_writer
    # 输出进度信息
    # 原来这里是「过滤表信息」，从 filter_table 复制过来忘了改，
    # 前端进度条上会连着出现两个「过滤表信息」
    writer({"stage": "校验sql"})
    # 获取sql语句
    sql = state["sql"]
    try:
        # 只读硬拦截放在 EXPLAIN 之前：写操作不该被拿去 EXPLAIN，
        # 而且在这里返回 error 可以让下游 correct_sql 再给模型一次修正机会。
        # execute_sql 节点里还有一道同样的拦截兜底。
        assert_read_only(sql, stage="validata_sql")

        # 获取repository
        dw_mysql_repository= runtime.context["dw_mysql_repository"]
        # 验证sql
        await dw_mysql_repository.validate_sql(sql)

        logger.info(f"sql验证正确：{sql}")
        return {"error":None}
    except NonReadOnlySQLError as e:
        # 只读拦截不算「SQL 写错了」，不能走返回 error 那条路——
        # 那样会进 correct_sql 再调一次模型，而重试也不会把写操作变成只读，
        # 白白多花一次调用。这里直接抛出，错误信息原样透出给用户。
        logger.warning(f"只读拦截命中：{e}")
        writer({"stage": "拒绝执行"})
        raise
    except Exception as e:
        logger.info(f"sql验证失败：{sql}")
        # 原来是 str{str(e)}，会把多余的字面量 "str" 拼进错误信息里
        return {"error": f"校验sql异常：{str(e)}"}

