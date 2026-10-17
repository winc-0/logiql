import anyio
from fastapi import APIRouter
from fastapi.params import Depends
from starlette.responses import StreamingResponse

from app.api.dependencies import get_query_service
from app.api.schemas.query_schema import QuerySchema
from app.services.query_service import QueryService

query_router = APIRouter()


# # 视频流模拟函数--生成器函数
# async def fake_video_streamer():
#     for i in range(10):
#         yield f"data: stage:{i} \n\n"
#         await anyio.sleep(1)
#
#
# @query_router.get("/")
# async def main():
#     return StreamingResponse(fake_video_streamer(),media_type="text/event-stream")
#

@query_router.post("/api/query")
async def query(query: QuerySchema,service:QueryService=Depends(get_query_service)):

    return StreamingResponse(service.query(query.query), media_type="text/event-stream")