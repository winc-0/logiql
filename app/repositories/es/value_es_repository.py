from elasticsearch import AsyncElasticsearch
from sqlalchemy import column

from app.models.es.value_info_es import ValueInfoEs


class ValueEsRepository:
    es_index_name = "zhishu-values"
    es_index_mappings = {
        "dynamic": False,
        "properties": {
            "id": {"type": "keyword"},
            "value": {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_max_word"},
            "type": {"type": "keyword"},
            "column_id": {"type": "keyword"},
            "column_name": {"type": "keyword"},
            "table_id": {"type": "keyword"},
            "table_name": {"type": "keyword"},
        }
    }

    def __init__(self, client: AsyncElasticsearch):
        self.client = client

    async def ensure_index(self):
        """
        确保字段取值存储集合存在

        :return:
        """
        # 判断指定索引是否存在
        if not await self.client.indices.exists(index=self.es_index_name):
            # 不存在，则创建
            await self.client.indices.create(
                index=self.es_index_name,
                mappings=self.es_index_mappings
            )

    async def recreate_index(self):
        """
        删除并重建索引，供元数据全量重建前调用。

        字段取值是从数据仓库实时采样出来的，数仓数据变化后旧取值会失效。
        取值文档虽然用的是确定性 id，但被删除的取值不会自动消失，
        继续残留在索引里会让三路召回收到已经不存在的取值，进而生成错误的 WHERE 条件。
        """
        if await self.client.indices.exists(index=self.es_index_name):
            await self.client.indices.delete(index=self.es_index_name)
        await self.ensure_index()

    async def upsert_values(self, value_infos: list[ValueInfoEs], batch_size: int = 20):
        """
        保存数据到es
        :param value_infos:
        :return:
        """
        # 遍历取值列表
        for i in range(0, len(value_infos), batch_size):
            # 获取批次数据
            batch_value_infos = value_infos[i:i + batch_size]

            # 定义存储结构列表
            operations = []
            # 遍历取值数据列表
            for value_info in batch_value_infos:
                operations.append({
                    "index": {
                        "_index": self.es_index_name
                    }
                })

                operations.append(value_info)
            # 保存批次数据
            await self.client.bulk(
                operations=operations,
            )

    async def search_value(self, keyword: str):
        """
        根据关键字匹配字段取值
        :param keyword:
        :return:
        """
        # 检索结果
        resp = await self.client.search(
            index=self.es_index_name,
            query={
                "match": {
                    "value": keyword
                }
            },
        )

        # 处理结果
        return [re["_source"] for re in resp["hits"]["hits"]]
