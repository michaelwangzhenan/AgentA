"""运行时数据备份端点的请求 / 响应 schema。"""

from pydantic import BaseModel


class CreateBackupRequest(BaseModel):
    skip_vectors: bool = False   # 跳过 C 类向量库 / 索引（体积大）


class BackupSnapshot(BaseModel):
    name: str
    timestamp: str
    created_at: str = ""
    include_vectors: bool | None = None
    file_count: int
    zip_bytes: int
    category_stats: dict = {}


class BackupListResponse(BaseModel):
    items: list[BackupSnapshot]


class RestoreResponse(BaseModel):
    restored: int        # 还原的文件数
    message: str         # 给前端展示的提示（含重启建议）
