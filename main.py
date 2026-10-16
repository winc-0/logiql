# 导入FastAPI核心类
import os
import uuid
from pathlib import Path

from fastapi import FastAPI,Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routers.query_router import query_router
from app.core.context import request_id_ctx_var
from app.core.lifespan import lifespan

# 创建FastAPI应用实例
app = FastAPI(lifespan=lifespan)

# 整合跨域中间件。前端若单独用 dev server 起在别的端口（如 Vite 的 5173），
# 浏览器会因为协议+域名+端口不一致拦截 SSE 请求，所以这里要显式放开。
# 默认只放开本机来源；部署到别的域名时用 CORS_ORIGINS 逗号分隔追加
_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
# "null" 是 file:// 打开页面时浏览器带的 origin。放开它意味着本机任意
# HTML 文件都能调后端——本地开发可以接受，公网部署时必须关掉
# （设置 ALLOW_FILE_ORIGIN=false）
if os.getenv("ALLOW_FILE_ORIGIN", "true").lower() not in ("0", "false", "no"):
    _origins.append("null")
_origins.extend(o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip())
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    # 本机任意端口都放行，省得每次换端口都要改代码
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(query_router)

# 托管前端页面。放在路由注册之后，StaticFiles 只在 /api 未命中时兜底，
# 这样访问 http://127.0.0.1:8010/ 即可直接用，无需另起前端服务。
# 优先用 Vue 构建产物（frontend/dist），没构建过时回退到零依赖的原生页
_WEB_DIR = Path(__file__).parent / "web"
_DIST_DIR = Path(__file__).parent / "frontend" / "dist"
_STATIC_DIR = _DIST_DIR if (_DIST_DIR / "index.html").is_file() else _WEB_DIR
if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="web")

# 整合中间件->设置异步协程上下文变量
@app.middleware("http")
async def add_request_cxt_var(request: Request, call_next):
    # 设置异步协程上下文变量
    request_id_ctx_var.set(uuid.uuid4())
    return await call_next(request)
