import logging
from fastapi import Request, HTTPException, status
from core.config import Config
from core.db import DatabaseManager
from api.netease import NeteaseMusicAPI
from api.qq import QQMusicAPI
from api.kuwo import KuwoMusicAPI
from api.mvsep_api import MVSepAPI

class EndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # 如果日志内容里包含我们轮询的接口路径，就返回 False (屏蔽该日志)
        return record.getMessage().find("/api/instrumental/queue_status") == -1

# 将过滤器挂载到 Uvicorn 的访问日志记录器上
logging.getLogger("uvicorn.access").addFilter(EndpointFilter())

# 实例化所有API客户端
db_manager = DatabaseManager(Config.DATABASE_FILE)
netease_api = NeteaseMusicAPI(
    Config.NETEASE_USERS,
    db_manager,
    Config.MASTER_DIRECTORY,
    Config.FLAC_DIRECTORY,
    Config.LOSSY_DIRECTORY,
)
qq_api = QQMusicAPI(
    db_manager, Config.MASTER_DIRECTORY, Config.FLAC_DIRECTORY, Config.LOSSY_DIRECTORY
)
kuwo_api = KuwoMusicAPI()
mvsep_api = MVSepAPI(getattr(Config, "MVSEP_API_KEY", ""))

def verify_api_key(request: Request):
    provided_key = request.headers.get("X-API-Key") or request.query_params.get(
        "api_key"
    )
    if (
        not hasattr(Config, "API_SECRET_KEY")
        or not provided_key
        or provided_key != Config.API_SECRET_KEY
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="认证失败：无效的API密钥。"
        )
    return True
