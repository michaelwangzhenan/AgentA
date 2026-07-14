"""Knowledge Base 端点请求 / 响应模型"""

from pydantic import BaseModel, Field


class KBDocument(BaseModel):
    """单个文档的聚合元数据（list / upload 返回值都用这个）"""

    doc_id: str = Field(..., description="文档 ID（基于相对路径 SHA1 前 16 位）")
    filename: str = Field(..., description="文件名（不含目录）")
    source: str = Field("", description="相对 docs_dir / web_upload_dir 的路径")
    ext: str = Field("", description="扩展名，例如 .md / .pdf")
    lang: str = Field("", description="语种判断（zh / en / mixed）")
    mtime: float = Field(0.0, description="文件修改时间（unix timestamp）；来自磁盘 stat")
    ingested_at: float = Field(0.0, description="入库时间（unix timestamp）；老数据为 0")
    chunks: int = Field(0, description="切分后入库的 chunk 数")
    total_chars: int = Field(0, description="所有 chunks 文本总字符数")
    golden_total: int = Field(0, description="该文档关联的 golden 候选总数")
    golden_pending: int = Field(0, description="该文档关联的 golden 待审候选数")


class KBDocumentListResponse(BaseModel):
    documents: list[KBDocument]
    total: int = Field(0, description="满足筛选条件的文档总数")
    page: int = Field(1, description="当前页码（1 基）")
    page_size: int = Field(20, description="每页条数")


class KBCollection(BaseModel):
    """库列表（L1）单项：一个 embedding 别名对应一个 collection。"""

    alias: str = Field(..., description="embedding 别名（en / zh / m3）")
    model: str = Field(..., description="模型名称，如 BAAI/bge-m3")
    collection: str = Field(..., description="对应的 Chroma collection 名，如 kb_m3")
    doc_count: int = Field(0, description="该库内文档数（按 doc_id 去重）")
    chunk_count: int = Field(0, description="该库内 chunk 总数")
    is_default: bool = Field(False, description="是否为 .env 配置的默认入库库")
    supports_api: bool = Field(
        False, description="该模型是否有云端版（有则入库可选 api-<alias> 走云端编码）"
    )


class KBCollectionListResponse(BaseModel):
    collections: list[KBCollection]
    default_ingest_alias: str = Field(
        ...,
        description="当前配置的默认入库别名（含 api-m3 等云端别名；与 is_default 按 collection 比对互补）",
    )


class KBDeleteResponse(BaseModel):
    deleted: bool = Field(..., description="是否实际找到并删除了 doc_id")
    chunks_removed: int = Field(0, description="Chroma 中移除的 chunk 数")


class KBClearAllResponse(BaseModel):
    """DELETE /api/kb/documents（清空整个 KB）返回值"""

    docs_removed: int = Field(0, description="删除的文档数")
    chunks_removed: int = Field(0, description="Chroma 中移除的 chunk 总数")
    files_removed: int = Field(0, description="web_uploads 目录中物理删除的文件数")


class KBCancelUploadResponse(BaseModel):
    """POST /api/kb/upload/cancel 返回值"""

    cancelled: bool = Field(..., description="是否找到对应 ingest_id 并已置取消标志")
