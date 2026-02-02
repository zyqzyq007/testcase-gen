from pydantic import BaseModel
from typing import List, Optional

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

class FileStructure(BaseModel):
    file_id: str
    path: str
    functions: List[FunctionInfo]

class ProjectStructure(BaseModel):
    project_id: str
    files: List[FileStructure]

class UploadResponse(BaseModel):
    project_id: str
    project_name: str
    file_count: int
    status: str
