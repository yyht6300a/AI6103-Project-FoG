#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""下载 FoG 复现（成员 A：数据与知识图谱）所需的全部原始数据。

本脚本只负责"取数"，不做任何预处理。下载内容分三类：

1. QA 标注（官方 split）
   - WebQSP: WebQSP.train.json / WebQSP.test.json
   - CWQ   : ComplexWebQuestions_{train,dev,test}.json
2. 离线 Freebase 子图（RoG 预处理子图的去序列化版本，名字空间三元组）
   - webqsp_subgraph.tsv / cwq_subgraph.tsv

说明
----
* 全量 Freebase（约 30 GB 压缩 / 300 GB 解压）需要 Virtuoso 才可查询，复现成本过高。
  按《复现计划》4.1 与风险表的口径，最小复现（MVR）用"预处理子图"代替全量 Freebase；
  本脚本下载的 TSV 即为该替代物，后续 `build_kg_index.py` 会把它建成可 BFS 的离线索引。
* 所有文件支持断点续传（按 Content-Length 校验），已存在且大小一致则跳过。
* 若下载失败会打印候选镜像地址，方便手工放入 ``data/raw`` 后再跑流水线。

用法::

    python scripts/download_data.py                 # 下载全部
    python scripts/download_data.py --only webqsp   # 只下 WebQSP
    python scripts/download_data.py --check         # 只检查现有文件
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW = PROJECT_ROOT / "data" / "raw"
QA_DIR = RAW / "qa"
KG_DIR = RAW / "kg"

CHUNK = 1 << 20  # 1 MiB

# --------------------------------------------------------------------------------------
# manifest: name -> (远端 URL 列表, 本地相对路径, 期望最小字节数)
# --------------------------------------------------------------------------------------
DROPBOX_CWQ = "https://www.dropbox.com/sh/7pkwkrfnwqhsnpo/"
HF_YF = "https://huggingface.co/datasets/YF0808/CWQ/resolve/main/"
HF_SUB = "https://huggingface.co/datasets/camazlucas/Freebase-WebQSP-CWQ-Subgraph/resolve/main/"

MANIFEST = {
    # ---- WebQSP（官方语义解析标注，含 InferentialChain / Sparql / Answers） ----
    "webqsp_train": {
        "urls": [
            HF_YF + "WebQSP.train.json",
            "https://download.microsoft.com/download/F/5/0/F5012144-A4FB-4084-897F-CFDA99C60BDF/WebQSP.zip",
        ],
        "path": "qa/WebQSP.train.json",
        "min_bytes": 8_000_000,
    },
    "webqsp_test": {
        "urls": [
            HF_YF + "WebQSP.test.json",
            "https://download.microsoft.com/download/F/5/0/F5012144-A4FB-4084-897F-CFDA99C60BDF/WebQSP.zip",
        ],
        "path": "qa/WebQSP.test.json",
        "min_bytes": 4_000_000,
    },
    # ---- CWQ（官方 train/dev/test，含 S-expression） ----
    "cwq_train": {
        "urls": [
            HF_YF + "ComplexWebQuestions_train.json",
            DROPBOX_CWQ + "AAAIHeWX0cPpbpwK6w06BCxva/ComplexWebQuestions_train.json?dl=1",
        ],
        "path": "qa/ComplexWebQuestions_train.json",
        "min_bytes": 30_000_000,
    },
    "cwq_dev": {
        "urls": [
            HF_YF + "ComplexWebQuestions_dev.json",
            DROPBOX_CWQ + "AADH8beLbOUWxwvY_K38E3ADa/ComplexWebQuestions_dev.json?dl=1",
        ],
        "path": "qa/ComplexWebQuestions_dev.json",
        "min_bytes": 4_000_000,
    },
    "cwq_test": {
        "urls": [
            DROPBOX_CWQ + "AABr4ysSy_Tg8Wfxww4i_UWda/ComplexWebQuestions_test.json?dl=1",
        ],
        "path": "qa/ComplexWebQuestions_test.json",
        "min_bytes": 2_000_000,
    },
    # ---- Freebase 子图（名字空间三元组，制表符分隔：head \t relation \t tail） ----
    "webqsp_kg": {
        "urls": [HF_SUB + "webqsp_subgraph.tsv"],
        "path": "kg/webqsp_subgraph.tsv",
        "min_bytes": 200_000_000,
    },
    "cwq_kg": {
        "urls": [HF_SUB + "cwq_subgraph.tsv"],
        "path": "kg/cwq_subgraph.tsv",
        "min_bytes": 400_000_000,
    },
}

GROUPS = {
    "webqsp": ["webqsp_train", "webqsp_test", "webqsp_kg"],
    "cwq": ["cwq_train", "cwq_dev", "cwq_test", "cwq_kg"],
    "qa": ["webqsp_train", "webqsp_test", "cwq_train", "cwq_dev", "cwq_test"],
    "kg": ["webqsp_kg", "cwq_kg"],
    "all": list(MANIFEST),
}


def _human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GiB"


def _download_one(url: str, dst: Path, expect_len: int | None) -> bool:
    """下载单个 URL 到 dst，支持 .part 断点续传。返回是否成功。"""
    part = dst.with_suffix(dst.suffix + ".part")
    offset = part.stat().st_size if part.exists() else 0

    headers = {"User-Agent": "fog-repro/0.1 (+member-A data loader)"}
    if offset:
        headers["Range"] = f"bytes={offset}-"

    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code == 416:  # 已经下完
            part.replace(dst)
            return True
        print(f"    ! HTTP {exc.code}: {exc.reason}")
        return False
    except Exception as exc:  # 网络不可达 / 证书问题
        print(f"    ! {type(exc).__name__}: {exc}")
        return False

    status = getattr(resp, "status", 200)
    if offset and status != 206:  # 服务端不支持 Range，从头写
        offset = 0

    total = resp.headers.get("Content-Length")
    total = int(total) + offset if total else None
    if expect_len and total is None:
        total = expect_len

    mode = "ab" if offset else "wb"
    done = offset
    t0 = time.time()
    last = 0.0
    try:
        with open(part, mode) as fh:
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                now = time.time()
                if now - last > 1.0:
                    last = now
                    speed = (done - offset) / max(now - t0, 1e-6)
                    pct = f"{done * 100 / total:5.1f}%" if total else "  ?  "
                    print(f"      {pct} {_human(done)} / {_human(total) if total else '?'}"
                          f"  {_human(speed)}/s", flush=True)
    except Exception as exc:
        print(f"    ! 传输中断: {type(exc).__name__}: {exc}（保留 .part 以便续传）")
        return False
    finally:
        resp.close()

    if total and done < total:
        print(f"    ! 不完整 {_human(done)}/{_human(total)}，保留 .part")
        return False
    part.replace(dst)
    return True


def _verify_json(path: Path) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
    except Exception as exc:
        return f"JSON 解析失败: {exc}"
    n = len(obj.get("Questions", [])) if isinstance(obj, dict) else len(obj)
    return f"OK, {n} questions"


def _verify_tsv(path: Path) -> str:
    n = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for _ in fh:
            n += 1
            if n >= 200_000:
                break
    return f"OK, >= {n} triples (前 {n} 行)"


def check(verbose: bool = True) -> dict[str, dict]:
    report = {}
    for key, spec in MANIFEST.items():
        p = RAW / spec["path"]
        if not p.exists():
            report[key] = {"present": False}
            continue
        size = p.stat().st_size
        info = {"present": True, "bytes": size, "path": str(p.relative_to(PROJECT_ROOT))}
        if p.suffix == ".json":
            info["check"] = _verify_json(p)
        elif p.suffix == ".tsv":
            info["check"] = _verify_tsv(p)
        if verbose:
            print(f"  [√] {key:14s} {_human(size):>10s}  {info.get('check','')}")
        report[key] = info
    return report


def download(keys: list[str], check_only: bool = False) -> int:
    QA_DIR.mkdir(parents=True, exist_ok=True)
    KG_DIR.mkdir(parents=True, exist_ok=True)

    missing = 0
    for key in keys:
        spec = MANIFEST[key]
        dst = RAW / spec["path"]
        if dst.exists() and dst.stat().st_size >= spec["min_bytes"]:
            print(f"[skip] {key} 已存在 ({_human(dst.stat().st_size)})")
            continue
        if check_only:
            print(f"[miss] {key} 缺少 {spec['path']}")
            missing += 1
            continue

        print(f"[get ] {key} -> {spec['path']}")
        ok = False
        for url in spec["urls"]:
            print(f"    ← {url}")
            if _download_one(url, dst, spec["min_bytes"]):
                ok = True
                break
            print("      换下一个镜像")
        if not ok:
            missing += 1
            print(f"    × {key} 全部镜像失败。可手工下载 {spec['path']} 放入 data/raw/{Path(spec['path']).parent}/")
            continue
        size = dst.stat().st_size
        if size < spec["min_bytes"]:
            print(f"    ! {key} 体积异常 {_human(size)}")
            missing += 1
        else:
            print(f"    √ {key} {_human(size)}")

    print("\n== 校验 ==")
    check()
    return missing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="下载 FoG 复现所需原始数据")
    ap.add_argument("--only", choices=sorted(GROUPS), default="all", help="只下载某个分组")
    ap.add_argument("--check", action="store_true", help="只校验，不下载")
    ap.add_argument("--list", action="store_true", help="列出 manifest")
    args = ap.parse_args(argv)

    if args.list:
        for key, spec in MANIFEST.items():
            print(f"{key:14s} {spec['path']:38s} {spec['urls'][0]}")
        return 0

    print(f"项目根目录: {PROJECT_ROOT}")
    print(f"数据目录  : {RAW}\n")
    keys = GROUPS[args.only]
    missing = download(keys, check_only=args.check)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
