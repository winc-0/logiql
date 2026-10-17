from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mysql.column_info_mysql import ColumnInfoMySQL
from app.models.mysql.column_metric_mysql import ColumnMetricMySQL
from app.models.mysql.metric_info_mysql import MetricInfoMySQL
from app.models.mysql.table_info_mysql import TableInfoMySQL


class MetaMysqlRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def purge_all(self):
        """
        清空元数据四张表，供全量重建前调用。

        构建元数据知识库是配置驱动的全量同步：conf/meta_config.yaml 是唯一事实来源。
        若不做清空，重复构建会因主键冲突直接失败（MySQL 8 报 1062 Duplicate entry），
        导致数据仓库数据变化后无法刷新元数据。

        删除顺序按外键依赖倒序：中间表 → 子表 → 父表。
        """
        for table in ("column_metric", "metric_info", "column_info", "table_info"):
            await self.session.execute(text(f"DELETE FROM {table}"))

    async def save_table_infos(self, table_infos: list[TableInfoMySQL]):
        """
        保存表信息到meta数据库
        :param table_infos:
        :return:
        """
        self.session.add_all(table_infos)

    async def save_column_infos(self, column_infos: list[ColumnInfoMySQL]):
        """
        表存字段信息到meta数据库
        :param column_infos:
        :return:
        """
        self.session.add_all(column_infos)

    async def save_metric_infos(self, metric_infos: list[MetricInfoMySQL]):
        """
        保存指标信息到meta数据库
        :param metric_infos:
        :return:
        """
        self.session.add_all(metric_infos)

    async def save_column_metric_infos(self, column_metric_infos: list[ColumnMetricMySQL]):
        """
        保存字段指标中间表信息
        :param column_metric_infos:
        :return:
        """
        self.session.add_all(column_metric_infos)

    async def get_column_info_by_id(self, relevant_column: str):
        """
        根据字段id，查询字段信息
        :param relevant_column:
        :return:
        """
        # 注意：AsyncSession.get() 本身是协程，必须 await，否则返回的是协程对象，
        # 调用方再 await 一次拿到的仍是协程，访问 .id 会报
        # "'coroutine' object has no attribute 'id'"
        return await self.session.get(ColumnInfoMySQL, relevant_column)

    async def get_column_keys_by_table_id(self, table_id: str):
        """
        根据表id查询，所属的主外键信息
        :param table_id:
        :return:
        """
        # 定义sql语句
        sql = """
              select *
              from column_info
              where role in ('primary_key', 'foreign_key')
                and table_id = :table_id 

              """
        # 执行封装结果的结构
        query = select(ColumnInfoMySQL).from_statement(text(sql))
        # 执行sql
        result=await self.session.execute(query,{"table_id":table_id})
        """
        [
            (ColumnInfoMySQL(id=1,name=fact_id)),
            (),
            ()
        ]
           隐式解包
        [
            ColumnInfoMySQL(id=1,name=fact_id)
            ColumnInfoMySQL(id=1,name=fact_id)
        
        ]
        """
        return result.scalars().fetchall()

    async def get_table_info_by_id(self, table_id:str):

        return await self.session.get(TableInfoMySQL,table_id)



