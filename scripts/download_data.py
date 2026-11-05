"""Download WebQSP and CWQ data for the FoG reproduction.

Sources:
    https://huggingface.co/datasets/rmanluo/RoG-webqsp
    https://huggingface.co/datasets/rmanluo/RoG-cwq

Usage:
    python scripts/download_data.py                 # committed sample
    python scripts/download_data.py --full          # complete parquet files
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, ProxyHandler, build_opener, urlopen

DATASETS = {
    "webqsp": "rmanluo/RoG-webqsp",
    "cwq": "rmanluo/RoG-cwq",
}
SPLITS = ["train", "test", "validation"]


def fetch_rows(dataset: str, split: str, length: int, offset: int = 0) -> list[dict]:
    params = urlencode(
        {"dataset": dataset, "config": "default", "split": split, "offset": offset, "length": length}
    )
    url = "https://datasets-server.huggingface.co/rows?" + params
    req = Request(url, headers={"User-Agent": "codex-reproduction"})
    opener = build_opener(ProxyHandler({}))  # bypass ambient proxies
    with opener.open(req, timeout=120) as resp:
        payload = json.load(resp)
    return [row["row"] for row in payload.get("rows", [])]


def normalize(row: dict) -> dict:
    """Parse the stringified graph field into a list of triples."""
    graph = row.get("graph")
    if isinstance(graph, str):
        try:
            graph = json.loads(graph)
        except json.JSONDecodeError:
            graph = []
    row = dict(row)
    row["graph"] = graph
    return row


def download_sample(raw_dir: Path, sample_size: int) -> None:
    for name, dataset in DATASETS.items():
        out: dict[str, list[dict]] = {}
        for split in ("train", "test"):
            rows = fetch_rows(dataset, split, sample_size)
            out[split] = [normalize(r) for r in rows]
            print(f"[sample] {name}/{split}: {len(rows)} rows")
        target = raw_dir / name / "sample.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[sample] wrote {target}")


def download_full(raw_dir: Path) -> None:
    try:
        from datasets import load_dataset
    except ImportError:
        sys.exit("The 'datasets' package is required for --full. Run: pip install datasets")
    for name, dataset in DATASETS.items():
        for split in SPLITS:
            data = load_dataset(dataset, split=split)
            target = raw_dir / name / f"{split}.parquet"
            target.parent.mkdir(parents=True, exist_ok=True)
            data.to_parquet(str(target))
            print(f"[full] wrote {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download WebQSP / CWQ for FoG reproduction")
    parser.add_argument("--full", action="store_true", help="download complete parquet files")
    parser.add_argument("--sample-size", type=int, default=100, help="rows per split for the sample")
    parser.add_argument("--raw-dir", type=str, default=None)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    raw_dir = Path(args.raw_dir) if args.raw_dir else root / "data" / "raw"
    if args.full:
        download_full(raw_dir)
    else:
        download_sample(raw_dir, args.sample_size)


if __name__ == "__main__":
    main()

