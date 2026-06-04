"""Knowledge Base 端点请求 / 响应模型"""

from pydantic import BaseModel, Field


class KBDocument(BaseModel):
    """单个文档的聚合元数据（list / upload 返回值都用这个）"""

    doc_id: str = Field(..., description="文档 ID（基于相对路径 SHA1 前 16 位）")
    filename: str = Field(..., description="文件名（不含目录）")
    source: str = Field("", description="相对 docs_dir / web_upload_dir 的路径")
    ext: str = Field("", description="扩展名，例如 .md / .pdf")
    lang: str = Field("", description="语种判断（zh / en / mixed）")
    mtime: float = Field(0.0, description="文件修改时间（unix timestamp）；用于排序")
    chunks: int = Field(0, description="切分后入库的 chunk 数")
    total_chars: int = Field(0, description="所有 chunks 文本总字符数")


class KBDocumentListResponse(BaseModel):
    documents: list[KBDocument]


class KBUploadResponse(BaseModel):
    """POST /api/kb/upload 同步返回（不分阶段；ingest 完成才返回）"""

    doc_id: str = Field(..., description="入库后的 doc_id")
    filename: str = Field(..., description="保存的文件名")
    chunks: int = Field(..., description="入库 chunk 数（0 = 内容未变化跳过 或 解析失败）")
    skipped_unchanged: bool = Field(False, description="是否因 content_sha1 一致而跳过 re-embed")
    message: str = Field("", description="给用户看的人类友好消息")


class KBDeleteResponse(BaseModel):
    deleted: bool = Field(..., description="是否实际找到并删除了 doc_id")
    chunks_removed: int = Field(0, description="Chroma 中移除的 chunk 数")
