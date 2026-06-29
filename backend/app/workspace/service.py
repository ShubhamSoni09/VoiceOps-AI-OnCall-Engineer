from __future__ import annotations

import re
import shutil
import subprocess
from hashlib import sha1
from pathlib import Path

from app.cache import JsonTTLCache
from app.config import Settings
from app.workspace.models import (
    CodeQueryResponse,
    WorkspaceFile,
    WorkspaceFileResponse,
    WorkspaceSearchMatch,
    WorkspaceSearchResponse,
    WorkspaceTreeResponse,
)
from app.workspace.tools import WorkspaceError, get_workspace_root, read_file, resolve_workspace_path

SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".voiceops_cache",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
}
SKIP_PATHS = {
    ("backend", "data"),
}
SKIP_FILENAMES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
}
SKIP_FILE_SUFFIXES = {
    ".DS_Store",
    ".lock",
    ".log",
    ".map",
    ".pyc",
    ".sqlite",
    ".sqlite3",
    ".sqlite3-shm",
    ".sqlite3-wal",
}
TEXT_EXTENSIONS = {
    ".css",
    ".env",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
LANGUAGE_BY_EXT = {
    ".css": "css",
    ".html": "html",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascript",
    ".md": "markdown",
    ".py": "python",
    ".sh": "shell",
    ".sql": "sql",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".yaml": "yaml",
    ".yml": "yaml",
}
STOP_WORDS = {
    "about",
    "after",
    "class",
    "const",
    "def",
    "does",
    "file",
    "find",
    "function",
    "from",
    "import",
    "interface",
    "into",
    "let",
    "show",
    "that",
    "the",
    "this",
    "type",
    "var",
    "what",
    "when",
    "where",
    "which",
    "with",
}


class WorkspaceCodeService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache = JsonTTLCache(settings.voiceops_cache_path)

    def tree(self, *, limit: int = 120, use_cache: bool = True) -> WorkspaceTreeResponse:
        root = get_workspace_root(self._settings.voiceops_workspace)
        cache_key = _cache_key(root, f"tree:{limit}")
        source_token = _workspace_source_token(root)
        if use_cache:
            cached = self._cache.get(cache_key, source_token=source_token)
            if cached:
                return WorkspaceTreeResponse.model_validate(cached)
        files: list[WorkspaceFile] = []
        total_files = 0
        truncated = False
        for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root)).lower()):
            if _should_skip(path, root) or not path.is_file():
                continue
            total_files += 1
            if len(files) >= limit:
                truncated = True
                continue
            relative = _relative(path, root)
            files.append(
                WorkspaceFile(
                    path=relative,
                    size=path.stat().st_size,
                    language=_language(path),
                )
            )
        response = WorkspaceTreeResponse(
            root=root.name,
            files=files,
            total_files=total_files,
            truncated=truncated,
        )
        self._cache.set(
            cache_key,
            response.model_dump(mode="json"),
            ttl_seconds=self._settings.workspace_cache_ttl_seconds,
            source_token=source_token,
        )
        return response

    def read(self, path: str, *, max_chars: int = 12000) -> WorkspaceFileResponse:
        target = resolve_workspace_path(path, configured=self._settings.voiceops_workspace)
        if _should_skip(target, get_workspace_root(self._settings.voiceops_workspace)):
            raise WorkspaceError(f"File is not readable through workspace context: {path}")
        content = read_file(path, configured=self._settings.voiceops_workspace)
        truncated = len(content) > max_chars
        return WorkspaceFileResponse(
            path=path,
            content=content[:max_chars],
            size=target.stat().st_size,
            language=_language(target),
            truncated=truncated,
        )

    def search(self, query: str, *, limit: int = 20, use_cache: bool = True) -> WorkspaceSearchResponse:
        terms = _query_terms(query)
        if not terms:
            return WorkspaceSearchResponse(query=query, matches=[])
        root = get_workspace_root(self._settings.voiceops_workspace)
        cache_key = _cache_key(root, f"search:{limit}:{_query_key(query)}")
        source_token = _workspace_source_token(root)
        if use_cache:
            cached = self._cache.get(cache_key, source_token=source_token)
            if cached:
                return WorkspaceSearchResponse.model_validate(cached)
        matches = self._search_with_rg(root, terms, limit=limit)
        if not matches:
            matches = self._search_with_python(root, terms, limit=limit)
        path_matches = self._search_paths(root, terms, limit=max(0, limit - len(matches)))
        seen = {(match.path, match.line) for match in matches}
        matches.extend(match for match in path_matches if (match.path, match.line) not in seen)
        response = WorkspaceSearchResponse(query=query, matches=_rank(matches)[:limit])
        self._cache.set(
            cache_key,
            response.model_dump(mode="json"),
            ttl_seconds=self._settings.workspace_cache_ttl_seconds,
            source_token=source_token,
        )
        return response

    def query(self, question: str, *, limit: int = 8) -> CodeQueryResponse:
        matches = self.search(question, limit=limit).matches
        if not matches:
            return CodeQueryResponse(
                answer="I could not find matching code references in the connected workspace.",
                references=[],
            )
        first = matches[0]
        files = []
        for match in matches:
            if match.path not in files:
                files.append(match.path)
        answer = (
            f"I found {len(matches)} relevant code reference"
            f"{'' if len(matches) == 1 else 's'} across {len(files)} file"
            f"{'' if len(files) == 1 else 's'}. Start with {first.path}:{first.line}: "
            f"{first.snippet}"
        )
        return CodeQueryResponse(answer=answer, references=matches)

    def invalidate_cache(self) -> None:
        root = get_workspace_root(self._settings.voiceops_workspace)
        self._cache.delete_prefix(_cache_prefix(root))

    def _search_with_rg(self, root: Path, terms: list[str], *, limit: int) -> list[WorkspaceSearchMatch]:
        if not shutil.which("rg"):
            return []
        matches: list[WorkspaceSearchMatch] = []
        seen: set[tuple[str, int]] = set()
        candidate_limit = max(limit * 8, 40)
        for term in terms:
            command = [
                "rg",
                "--ignore-case",
                "--line-number",
                "--no-heading",
                "--color",
                "never",
                "--glob",
                "!{.git,node_modules,dist,build,target,__pycache__,.pytest_cache}/**",
                "--",
                term,
            ]
            try:
                result = subprocess.run(
                    command,
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
            except (OSError, subprocess.TimeoutExpired):
                return matches
            if result.returncode not in {0, 1}:
                continue
            for line in result.stdout.splitlines():
                parsed = _parse_rg_line(line)
                if parsed is None:
                    continue
                path, line_no, snippet = parsed
                target = root / path
                if _should_skip(target, root):
                    continue
                key = (path, line_no)
                if key in seen:
                    continue
                seen.add(key)
                matches.append(
                    WorkspaceSearchMatch(
                        path=path,
                        line=line_no,
                        snippet=_clean_snippet(snippet),
                        score=_score(snippet, terms, path=path),
                    )
                )
                if len(matches) >= candidate_limit:
                    return _rank(matches)
        return _rank(matches)

    def _search_with_python(self, root: Path, terms: list[str], *, limit: int) -> list[WorkspaceSearchMatch]:
        matches: list[WorkspaceSearchMatch] = []
        for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root)).lower()):
            if len(matches) >= limit:
                break
            if _should_skip(path, root) or not path.is_file() or not _looks_text(path):
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for index, line in enumerate(lines, start=1):
                lower = line.lower()
                if not any(term in lower for term in terms):
                    continue
                matches.append(
                    WorkspaceSearchMatch(
                        path=_relative(path, root),
                        line=index,
                        snippet=_clean_snippet(line),
                        score=_score(line, terms, path=_relative(path, root)),
                    )
                )
                if len(matches) >= limit:
                    break
        return _rank(matches)

    def _search_paths(self, root: Path, terms: list[str], *, limit: int) -> list[WorkspaceSearchMatch]:
        if limit <= 0:
            return []
        matches: list[WorkspaceSearchMatch] = []
        for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root)).lower()):
            if len(matches) >= limit:
                break
            if _should_skip(path, root) or not path.is_file() or not _looks_text(path):
                continue
            relative = _relative(path, root)
            lower = relative.lower()
            if not any(term in lower for term in terms):
                continue
            matches.append(
                WorkspaceSearchMatch(
                    path=relative,
                    line=1,
                    snippet=f"File path match: {relative}",
                    score=_score(relative, terms, path=relative),
                )
            )
        return _rank(matches)


def _parse_rg_line(line: str) -> tuple[str, int, str] | None:
    parts = line.split(":", 2)
    if len(parts) != 3:
        return None
    path, line_no, snippet = parts
    try:
        return path, int(line_no), snippet
    except ValueError:
        return None


def _cache_prefix(root: Path) -> str:
    digest = sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"workspace:{digest}:"


def _cache_key(root: Path, name: str) -> str:
    return f"{_cache_prefix(root)}{name}"


def _workspace_source_token(root: Path) -> str:
    digest = sha1()
    for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root)).lower()):
        if _should_skip(path, root) or not path.is_file():
            continue
        stat = path.stat()
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(str(stat.st_size).encode("utf-8"))
        digest.update(str(stat.st_mtime_ns).encode("utf-8"))
    return digest.hexdigest()


def _query_key(query: str) -> str:
    normalized = " ".join(query.lower().split())
    return sha1(normalized.encode("utf-8")).hexdigest()[:16]


def _query_terms(query: str) -> list[str]:
    words = re.findall(r"[\w./-]+", query.lower())
    terms = []
    for word in words:
        if len(word) < 2 or word in STOP_WORDS:
            continue
        if word not in terms:
            terms.append(word)
    return terms[:8]


def _score(snippet: str, terms: list[str], *, path: str = "") -> int:
    lower = snippet.lower()
    path_lower = path.lower()
    score = 0
    for term in terms:
        if term in lower:
            score += 10
        if re.search(rf"\b{re.escape(term)}\b", lower):
            score += 4
        if term in path_lower:
            score += 3
    stripped = lower.strip()
    definition_name = _definition_name(stripped)
    if definition_name and any(_normalize_symbol(definition_name) == _normalize_symbol(term) for term in terms):
        score += 45
    elif definition_name and any(term in lower for term in terms):
        score += 8
    if stripped.startswith(("import ", "from ")):
        score -= 6
    return score


def _definition_name(line: str) -> str | None:
    match = re.match(
        r"^(export\s+)?(?:async\s+def|def|class|function|const|let|var|interface|type)\s+([\w$]+)",
        line,
    )
    return match.group(2) if match else None


def _normalize_symbol(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _rank(matches: list[WorkspaceSearchMatch]) -> list[WorkspaceSearchMatch]:
    return sorted(matches, key=lambda item: (item.score, -len(item.path), -item.line), reverse=True)


def _clean_snippet(snippet: str, *, limit: int = 180) -> str:
    clean = " ".join(snippet.strip().split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


def _should_skip(path: Path, root: Path) -> bool:
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError:
        return True
    if any(part in SKIP_DIRS for part in relative_parts):
        return True
    if any(relative_parts[: len(skip_path)] == skip_path for skip_path in SKIP_PATHS):
        return True
    if path.name in SKIP_FILENAMES:
        return True
    if path.name in SKIP_FILE_SUFFIXES:
        return True
    if path.suffix in SKIP_FILE_SUFFIXES:
        return True
    try:
        return path.is_file() and path.stat().st_size > 768_000
    except OSError:
        return True


def _looks_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTENSIONS or path.name in {"Dockerfile", "Makefile"}


def _language(path: Path) -> str | None:
    return LANGUAGE_BY_EXT.get(path.suffix.lower())


def _relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")
