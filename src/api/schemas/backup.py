"""运行时数据备份端点的请求 / 响应 schema。"""

from pydantic import BaseModel, Field


class CreateBackupRequest(BaseModel):
    # 要备份的类别（{A,B,C,E,F,K} 子集）；默认全选。空列表非法（路由校验）
    categories: list[str] = Field(default_factory=lambda: ["A", "B", "C", "E", "F", "K"])


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
