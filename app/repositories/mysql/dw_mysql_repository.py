import datetime as dt
import decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class DwMysqlRepository:
    def __init__(self,session:AsyncSession):
        self.session=session

    @staticmethod
    def _jsonable(value):
        """把数据库返回的取值转成可 JSON 序列化的类型。

        examples 最终要写进 MySQL 的 JSON 列并同步到 Elasticsearch，而 SQLAlchemy
        会把 DATE/DATETIME 映射成 datetime.date 对象、把 DECIMAL 映射成 Decimal 对象，
        这两者都无法直接 json.dumps，会报
        "Object of type date is not JSON serializable"。

        原有的 dw 数据只有 INT/VARCHAR 字段，所以这个坑一直没暴露出来；
        数仓一旦纳入日期型或金额型字段就会立刻踩到。
        """
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (dt.datetime, dt.date, dt.time)):
            return value.isoformat()
        if isinstance(value, decimal.Decimal):
            return float(value)
        if isinstance(value, (bytes, bytearray)):
            return bytes(value).decode("utf-8", errors="replace")
        if isinstance(value, (str, int, float)):
            return value
        return str(value)


    async def get_column_values(self, table_name:str, column_name:str,limit:int =10):
        """
        查询指定字段的取值

        :param table_name:
        :param column_name:
        :return: ["C001","C002","C003","C004","C005"]
        """
        # 定义sql
        sql = f"select distinct {column_name} from {table_name} limit {limit}"
        # 执行sql
        result =await self.session.execute(text(sql))
        # [row(C001),row(C002),row(C003)]
        # 必须转成 JSON 可序列化类型，否则写 MySQL JSON 列 / 同步 ES 时会炸
        return [self._jsonable(row) for row in result.scalars().fetchall()]

    async def get_column_types(self, table_name:str):
        """
        查询表中字段的类型
        :param table_name:
        :return: 字段 {"字段名":"字段类型"}


        """
        # 定义sql
        sql = f"show columns from {table_name}"
        # 执行sql
        result=await self.session.execute(text(sql))
        #[row(),row(),row()]

        # 获取结果，组装数据
        return {row.Field: row.Type for row in result.fetchall()}

    async def get_db_info(self):
        """
        查询数据库信息
        1.版本信息

        2.数据库方言

        :return:
        """
        # 查询数据的版本
        result=await self.session.execute(text("select version()"))
        # 获取版本
        version = result.scalar()

        # 查询数据库方言
        dialect = self.session.bind.dialect.name

        return {"version":version,"dialect":dialect}

    async def validate_sql(self, sql:str):
        """

        使用explain 关键字校验sql
        :param sql:
        :return:
        """

        await  self.session.execute(text(f"explain {sql}"))

    async def execute_sql(self, sql:str):
        """
        执行sql语句
        :param sql:
        :return:
        """
        # [(),(),()]
        result =await self.session.execute(text(sql))
        # 调整格式 [{RowMapping对象（key:value）},{}]
        return [dict(row) for row in result.mappings().fetchall()]


