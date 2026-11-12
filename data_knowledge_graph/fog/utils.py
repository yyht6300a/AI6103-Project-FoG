# -*- coding: utf-8 -*-
"""通用工具：读写 JSON/JSONL、进度、计时、哈希、日志。"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


# ----------------------------------------------------------------------------- 日志
class Logger:
    """极简 logger：带耗时前缀，同时写 stdout 与日志文件。"""

    def __init__(self, name: str = "fog", logfile: Path | None = None) -> None:
        self.name = name
        self._t0 = time.time()
        self._fh = None
        if logfile is not None:
            logfile.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(logfile, "a", encoding="utf-8")

    def __call__(self, msg: str = "") -> None:
        line = f"[{time.time() - self._t0:7.2f}s] {msg}"
        print(line, flush=True)
        if self._fh is not None:
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


@contextmanager
def timer(label: str, log: Logger | None = None) -> Iterator[dict]:
    box: dict[str, Any] = {"label": label}
    t0 = time.perf_counter()
    try:
        yield box
    finally:
        box["seconds"] = round(time.perf_counter() - t0, 3)
        if log is not None:
            log(f"{label} 用时 {box['seconds']:.2f}s")


# ----------------------------------------------------------------------------- IO
def read_json(path: str | Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: str | Path, obj: Any, indent: int | None = 2) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=indent)
        fh.write("\n")
    os.replace(tmp, path)


def iter_jsonl(path: str | Path, limit: int | None = None) -> Iterator[dict]:
    """流式读 JSONL（自动识别 .gz）。"""
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if limit is not None and i >= limit:
                break
            line = line.strip()
            if line:
                yield json.loads(line)


class JsonlWriter:
    """带 gzip 支持的 JSONL 写入器（上下文管理器）。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = None
        self.n = 0

    def __enter__(self) -> "JsonlWriter":
        if self.path.suffix == ".gz":
            self._fh = gzip.open(self.path, "wt", encoding="utf-8", compresslevel=6)
        else:
            self._fh = open(self.path, "w", encoding="utf-8")
        return self

    def write(self, obj: Any) -> None:
        assert self._fh is not None
        self._fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.n += 1

    def __exit__(self, *exc) -> None:
        assert self._fh is not None
        self._fh.close()
        self._fh = None


# ----------------------------------------------------------------------------- 杂项
def human_bytes(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TiB"


def human_int(n: float) -> str:
    return f"{int(n):,}"


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Progress:
    """无依赖的进度打印（每 N 条或每 T 秒一次）。"""

    def __init__(self, total: int | None, every: int = 200, label: str = "", log=print) -> None:
        self.total = total
        self.every = every
        self.label = label
        self.log = log
        self.i = 0
        self.t0 = time.time()

    def tick(self, n: int = 1) -> None:
        self.i += n
        if self.total and (self.i % self.every == 0 or self.i == self.total):
            dt = max(time.time() - self.t0, 1e-6)
            pct = 100.0 * self.i / self.total
            self.log(f"  {self.label} {self.i:,}/{self.total:,} ({pct:5.1f}%) "
                     f"{self.i / dt:,.0f} it/s")

    def done(self) -> None:
        dt = max(time.time() - self.t0, 1e-6)
        self.log(f"  {self.label} 完成 {self.i:,} 条，用时 {dt:.1f}s")


def ensure_dir(p: str | Path) -> Path:
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def relpath(p: str | Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(project_root()))
    except Exception:
        return str(p)


def eprint(*args, **kwargs) -> None:
    print(*args, file=sys.stderr, **kwargs)
