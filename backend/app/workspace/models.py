from pydantic import BaseModel, Field


class WorkspaceFile(BaseModel):
    path: str
    type: str = "file"
    size: int = 0
    language: str | None = None


class WorkspaceTreeResponse(BaseModel):
    root: str
    files: list[WorkspaceFile] = Field(default_factory=list)
    total_files: int = 0
    truncated: bool = False
    mode: str = "local"


class WorkspaceFileResponse(BaseModel):
    path: str
    content: str
    size: int = 0
    language: str | None = None
    truncated: bool = False


class WorkspaceSearchMatch(BaseModel):
    path: str
    line: int
    snippet: str
    score: int = 1


class WorkspaceSearchResponse(BaseModel):
    query: str
    matches: list[WorkspaceSearchMatch] = Field(default_factory=list)
    mode: str = "local"


class CodeQueryRequest(BaseModel):
    question: str
    limit: int = Field(default=8, ge=1, le=50)


class CodeQueryResponse(BaseModel):
    answer: str
    references: list[WorkspaceSearchMatch] = Field(default_factory=list)
    mode: str = "deterministic"


class GitFileStatus(BaseModel):
    path: str
    status: str


class GitStatusResponse(BaseModel):
    is_git_repo: bool
    branch: str | None = None
    dirty: bool = False
    files: list[GitFileStatus] = Field(default_factory=list)
    warning: str | None = None


class GitDiffResponse(BaseModel):
    branch: str | None = None
    diff: str = ""
    files_changed: list[str] = Field(default_factory=list)


class WorkspaceReadinessCheck(BaseModel):
    id: str
    ready: bool
    severity: str = "ok"
    detail: str


class WorkspaceReadinessResponse(BaseModel):
    ready: bool
    workspace: str | None = None
    root_name: str | None = None
    branch: str | None = None
    dirty: bool = False
    test_command: str | None = None
    checks: list[WorkspaceReadinessCheck] = Field(default_factory=list)
