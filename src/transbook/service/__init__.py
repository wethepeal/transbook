"""服务层（M5）：FastAPI 接口 + 后台作业 + 共享流水线。"""

from transbook.service import jobs, pipeline
from transbook.service.api import NO_WEB_HINT, create_app, find_web_dist

__all__ = ["NO_WEB_HINT", "create_app", "find_web_dist", "jobs", "pipeline"]
