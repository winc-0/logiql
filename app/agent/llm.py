import os

from langchain.chat_models import init_chat_model

from app.conf.app_config import app_config

# 统一走 OpenAI 兼容协议，便于切换 LLM 后端（百炼 DashScope / DeepSeek / vLLM / 本地 Ollama）。
# 切换只需改环境变量 LLM_MODEL_NAME / LLM_BASE_URL / LLM_API_KEY，无需改代码。
#
# timeout 必须显式设置：LangChain 底层的 httpx 客户端默认不设超时，
# 一旦上游出现「连接建立但不返回」的情况（云端 LLM 偶发），整个请求会无限期挂起。
# 线上表现为接口永不返回、连接池被慢慢耗干；离线跑评测时会表现为批次卡死不动。
llm = init_chat_model(
    model=app_config.llm.model_name,
    model_provider=app_config.llm.model_provider,
    api_key=app_config.llm.api_key,
    base_url=app_config.llm.base_url,
    temperature=0,
    timeout=float(os.getenv("LLM_TIMEOUT", "120")),
    max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
)

if __name__ == '__main__':
    for chunk in llm.stream("你是什么模型？"):
        print(chunk.text, end="", flush=True)