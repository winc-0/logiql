from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger
from app.core.sql_guard import NonReadOnlySQLError, assert_read_only


async def execute_sql(state:DataAgentState,runtime:Runtime[DataAgentContext]):
    # 获取流对象
    writer = runtime.stream_writer
    # 输出进度信息
    writer({"stage": "执行sql"})
    try:
    # 获取sql语句
        sql = state["sql"]

        # 兜底拦截：精简策略的图会跳过 validata_sql 直接到这里，
        # correct_sql 纠错后的 SQL 也不再回校验节点，
        # 所以真正碰数据库之前必须再判一次，只放行只读语句。
        assert_read_only(sql, stage="execute_sql")

        # repository
        dw_mysql_repository= runtime.context["dw_mysql_repository"]

        # 执行 [{"销售总额"：41099.5}]
        result=await dw_mysql_repository.execute_sql(sql)
        # 输出结果
        writer({"result":result})
        logger.info(f"执行sql成功：{result}")

    except NonReadOnlySQLError as e:
        # 不记 error 级别：这是拦住了危险操作，属于预期内的防护生效，
        # 记 ERROR 会淹没真正的故障
        logger.warning(f"只读拦截生效，SQL 未下发：{e}")
        writer({"stage": "拒绝执行"})
        raise

    except Exception as e:
        # 原来写成 str()，异常详情永远是空的——排查「到底是数据库断了还是
        # 模型写出了非法 SQL」时最关键的一行信息就这么丢掉了
        logger.error(f"执行sql异常：{str(e)}")
        raise