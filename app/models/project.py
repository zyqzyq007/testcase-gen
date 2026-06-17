from pydantic import BaseModel
from typing import List, Optional, Dict

class FileInfo(BaseModel):
    file_id: str
    path: str
    content: Optional[str] = None

class FunctionInfo(BaseModel):
    function_id: str
    name: str
    start_line: int
    end_line: int
    signature: str
    language: Optional[str] = None
    qualified_name: Optional[str] = None
    class_name: Optional[str] = None
    is_method: Optional[bool] = False
    is_async: Optional[bool] = False
    target_kind: Optional[str] = "function"

class FileStructure(BaseModel):
    file_id: str
    path: str
    file_type: Optional[str] = "c"   # "c", "h", "py", "json", etc.
    language: Optional[str] = None
    functions: List[FunctionInfo]

class ProjectStructure(BaseModel):
    project_id: str
    language: Optional[str] = None
    test_framework: Optional[str] = None
    files: List[FileStructure]

class UploadResponse(BaseModel):
    project_id: str
    project_name: str
    file_count: int
    status: str
    source: Optional[str] = None  # "local" = 私有卷上传; "uniportal" = UniPortal 共享卷
    language: Optional[str] = None
    test_framework: Optional[str] = None
    dependency_manager: Optional[str] = None
    env_source: Optional[str] = None  # "conda_pack" = 已检测到离线 conda 环境; "none" = 无

class TestTargetFunctionInfo(BaseModel):
    function_id: str
    name: str
    start_line: int
    end_line: int
    signature: str
    source_file: str
    category: str
    header_file: Optional[str] = None
    declared_in_header: Optional[bool] = None
    reason: Optional[str] = None
    language: Optional[str] = None
    qualified_name: Optional[str] = None
    class_name: Optional[str] = None
    is_method: Optional[bool] = False
    is_async: Optional[bool] = False
    target_kind: Optional[str] = "function"

class TestTargetStats(BaseModel):
    must_test_count: int
    optional_static_count: int
    skipped_count: int

class ExtractTestTargetsResponse(BaseModel):
    project_id: str
    must_test: List[TestTargetFunctionInfo]
    optional_static: List[TestTargetFunctionInfo]
    skipped: List[TestTargetFunctionInfo]
    stats: TestTargetStats
