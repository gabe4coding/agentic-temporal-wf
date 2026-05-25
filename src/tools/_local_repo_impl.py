import hashlib
from pathlib import Path

from src.tools._workdir import safe_join


def read_file(workdir: Path, path: str) -> str:
    return safe_join(workdir, path).read_text()


def list_files(workdir: Path, glob: str = "**/*.py") -> list[str]:
    workdir = workdir.resolve()
    return sorted(
        str(p.relative_to(workdir))
        for p in workdir.glob(glob)
        if p.is_file() and ".git" not in p.parts
    )


def apply_edit(workdir: Path, path: str, new_content: str) -> str:
    target = safe_join(workdir, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_content)
    return hashlib.sha1(new_content.encode()).hexdigest()
