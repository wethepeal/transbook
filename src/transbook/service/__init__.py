"""服务层（M5）：FastAPI 接口 + 后台作业 + 共享流水线。"""

from transbook.service import jobs, pipeline
from transbook.service.api import create_app

__all__ = ["create_app", "jobs", "pipeline"]
