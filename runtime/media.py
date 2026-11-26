"""Где лежит файл шага.

`./static/<файл>` в графе ищется:
  1. в хранилище Студии `data/media/<файл>` (загруженное в редакторе – имя
     файла там = sha256 содержимого);
  2. в `static` запуска bot-engine (`launches/<static_launch>/static/<файл>`) –
     для воронок, импортированных из монорепо.

Телеграму файл уходит БАЙТАМИ (CLAUDE.md bot-engine §29: серверы Telegram не
скачивают картинки с нашего домена и отклоняют всё сообщение целиком). После
первой отправки file_id запоминается в media_cache по (sha256, бот) –
file_id привязан к боту (§30), поэтому ключ включает бота.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MediaRef:
    kind: str                # file / url / file_id / missing
    value: str               # путь / ссылка / file_id / исходная строка
    sha256: str | None = None


def _looks_like_file_id(s: str) -> bool:
    return len(s) > 20 and "/" not in s and "." not in s and " " not in s


class MediaStore:
    def __init__(self, data_dir: Path, repo_root: Path | None):
        self.data_dir = Path(data_dir)
        self.repo_root = Path(repo_root) if repo_root else None
        self._sha: dict[tuple[str, int, int], str] = {}

    def _file_sha(self, p: Path) -> str:
        st = p.stat()
        key = (str(p), st.st_size, int(st.st_mtime_ns))
        sha = self._sha.get(key)
        if sha is None:
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            sha = h.hexdigest()
            self._sha[key] = sha
        return sha

    def candidates(self, rel: str, static_launch: str) -> list[Path]:
        out = [self.data_dir / "media" / rel]
        if self.repo_root and static_launch:
            out.append(self.repo_root / "launches" / static_launch / "static" / rel)
        return out

    def resolve(self, ref: str | None, static_launch: str) -> MediaRef | None:
        if not ref:
            return None
        ref = ref.strip()
        if ref.startswith(("http://", "https://")):
            return MediaRef("url", ref)
        if ref.startswith("./static/") or ref.startswith("static/"):
            rel = ref.split("static/", 1)[1]
            if not rel or ".." in Path(rel).parts or rel.startswith(("/", "\\")):
                return MediaRef("missing", ref)
            for p in self.candidates(rel, static_launch):
                if p.is_file():
                    return MediaRef("file", str(p), self._file_sha(p))
            return MediaRef("missing", ref)
        if _looks_like_file_id(ref):
            return MediaRef("file_id", ref)
        p = Path(ref)
        if p.is_file():
            return MediaRef("file", str(p), self._file_sha(p))
        return MediaRef("missing", ref)
