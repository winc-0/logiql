from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState, TableInfoState, ColumnInfoState, MetricInfoState
import asyncio

from app.core.log import logger
from app.models.es.value_info_es import ValueInfoEs
from app.models.mysql.column_info_mysql import ColumnInfoMySQL
from app.models.mysql.table_info_mysql import TableInfoMySQL
from app.models.qdrant.column_info_qdrant import ColumnInfoQdrant
from app.models.qdrant.metric_info_qdrant import MetricInfoQdrant



async def merge_retrieved_info(state:DataAgentState,runtime:Runtime[DataAgentContext]):
    # 获取流对象
    writer = runtime.stream_writer
    # 输出进度信息
    writer({"stage": "合并召回信息"})

    try:
        # 获取召回的字段信息
        recall_columns:list[ColumnInfoQdrant]=state["recall_columns"]
        # 获取召回的指标信息
        recall_metrics:list[MetricInfoQdrant] = state["recall_metric"]
        # 获取召回的取值信息
        recall_values:list[ValueInfoEs] = state["recall_values"]

        # 创建表信息列表封装数据
        table_infos:list[TableInfoState]=[]


        # 获取repository
        meta_mysql_repository=runtime.context["meta_mysql_repository"]

        # 条件召回字段结构
        retrieved_column_maps:dict[str,ColumnInfoQdrant]={recall_column["id"]: recall_column for recall_column in recall_columns}

        # 1.根据召回指标对应字段，补充涉及的字段信息
        for recall_metric in recall_metrics:
            # 遍历当前指标关联的字段
            for relevant_column in recall_metric["relevant_columns"]:
                # 判断
                if relevant_column not in retrieved_column_maps:
                    # 指标涉及的字段，不存在召回字段中
                    column_info_mysql:ColumnInfoMySQL=await meta_mysql_repository.get_column_info_by_id(relevant_column)
                    # 存储关联字段信息
                    retrieved_column_maps[relevant_column]=convert_column_info_from_mysql_to_qdrant(column_info_mysql)


        # 2.根据召回字段取值，补充涉及到的字段取值对应的字段信息
        for recall_value in recall_values:
            # 获取取值中的字段id
            column_id = recall_value["column_id"]

            # 获取当前召回的字段你取值
            column_value = recall_value["value"]
            # 判断是否存在
            if column_id not in retrieved_column_maps:
                # 根据id查询字段信息
                column_info_mysql: ColumnInfoMySQL = await meta_mysql_repository.get_column_info_by_id(column_id)
                # 存储关联字段信息
                retrieved_column_maps[column_id] = convert_column_info_from_mysql_to_qdrant(column_info_mysql)

            # 字段取值是否存在对应的字段信息中的examples中
            if column_value not in retrieved_column_maps[column_id]["examples"]:
                retrieved_column_maps[column_id]["examples"].append(column_value)

        # 3.划分字段以表为单位的结构
        retrieved_table_maps:dict[str,list[ColumnInfoQdrant]]={}
        # 遍历合并的字段列表
        for column_info in retrieved_column_maps.values():
            # 获取表的id
            table_id = column_info["table_id"]
            # 判断
            if table_id not in retrieved_table_maps:
                retrieved_table_maps[table_id]=[]
            # 添加表信息
            retrieved_table_maps[table_id].append(column_info)
            """
             {
                字段id:字段对象，
                字段id:字段对象，
                字段id:字段对象，
             }
            结果转换：
            {
                表id：字段列表[字段对象,字段对象,主键字段对象，外键字段对象]
                表id：字段列表[字段对象]
            
            }
            
            """
        # 4.遍历表字段结构数据，添加主外键数据
        for table_id, columns_list in retrieved_table_maps.items():

            # 4.1 根据表id查询当前表的主外键
            column_infos:list[ColumnInfoMySQL]=await meta_mysql_repository.get_column_keys_by_table_id(table_id)

            # 4.2 获取当前表以召回所有字段的id
            column_ids:list[str]=[columns["id"] for columns in columns_list]
            # 4.3 判断主外键是否已经存在
            for column_info in column_infos:
                # 获取字段id
                column_id = column_info.id
                # 判读
                if column_id not in column_ids:
                    # 4.4 不存在则条件到目标列表
                    columns_list.append(convert_column_info_from_mysql_to_qdrant(column_info))

            # 根据表id查询表信息
            table_info_mysql:TableInfoMySQL = await meta_mysql_repository.get_table_info_by_id(table_id)

            # 转换字段模型
            columns = [convert_column_info_from_qdrant_to_state(column) for column in columns_list]

            # 封装表信息
            table_info=TableInfoState(
                name=table_info_mysql.name,
                role=table_info_mysql.role,
                description=table_info_mysql.description,
                columns=columns
            )
            table_infos.append(table_info)


        logger.info(f"合并召回表信息成功：{table_infos}")
        # 5.转换指标信息
        metric_infos:list[MetricInfoState]=[convert_metric_info_form_qdrant_to_state(recall_metric) for recall_metric in recall_metrics]
        logger.info(f"合并召回指标信息成功:{metric_infos}")


        return {"table_infos":table_infos,"metric_infos":metric_infos}
    except Exception as e:
        logger.error(f"合并召回信息异常:{str(e)}")
        raise





def convert_column_info_from_qdrant_to_state(column:ColumnInfoQdrant)->ColumnInfoState:
    return ColumnInfoState(
        name=column['name'],
        type=column['type'],
        role=column["role"],
        examples=column["examples"],
        description=column["description"],
        alias=column["alias"]
    )


def convert_metric_info_form_qdrant_to_state(recall_metric:MetricInfoQdrant):
    return MetricInfoState(
        name=recall_metric['name'],
        description=recall_metric["description"],
        relevant_columns=recall_metric["relevant_columns"],
        alias=recall_metric["alias"]
    )


























def convert_column_info_from_mysql_to_qdrant(column_info_mysql:ColumnInfoMySQL)->ColumnInfoQdrant:
    return ColumnInfoQdrant(
        id=column_info_mysql.id,
        name=column_info_mysql.name,
        description=column_info_mysql.description,
        role=column_info_mysql.role,
        type=column_info_mysql.type,
        examples=column_info_mysql.examples,
        table_id=column_info_mysql.table_id,
        alias=column_info_mysql.alias
    )

