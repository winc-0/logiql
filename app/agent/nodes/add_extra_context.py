import asyncio
from datetime import datetime

from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState, DateInfoState, DBInfoState
from app.core.log import logger


async def add_extra_context(state:DataAgentState,runtime:Runtime[DataAgentContext]):
    # 获取流对象
    writer=runtime.stream_writer
    # 输出进度信息
    writer({"stage":"添加额外上下文"})
    # 获取repository
    dw_mysql_repository=runtime.context["dw_mysql_repository"]
    try:
        # 1.时间上下文信息

        # 1.1 获取当前时间
        today = datetime.today()
        # 1.2 时间（年月日）
        date = today.strftime("%Y-%m-%d")
        # 1.3 星期
        weekday = today.strftime("%A")
        # 1.4 季度
        month=today.month
        quarter=f"Q{(month-1)//3+1}"
        # 封装数据
        date_info=DateInfoState(
            date=date,
            weekday=weekday,
            quarter=quarter
        )

        # i=1/0

        # 2.数据库相关信息

        # 查询数据库版本和方言
        db_info:DBInfoState =await dw_mysql_repository.get_db_info()

        logger.info(f"添加额外上下信息，时间信息：{date_info},数据库信息：{db_info}")

        return {"date_info":date_info,"db_info":db_info}
    except Exception as e:
        logger.error(f"添加额外上下文信息异常 ：{str(e)}")
        raise
