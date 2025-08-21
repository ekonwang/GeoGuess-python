#!/usr/bin/env python3
from __future__ import annotations

import os
import json
from typing import Any, Dict, List, Optional


def load(dataset_dir: str, debug: bool = False, progress_every: int = 200) -> List[Dict[str, Any]]:
    """
    加载由 `batch_panorama.py` 生成的数据集。

    目录结构（每条样本一个子目录，目录名为 uid）：
        <dataset_dir>/
            <uid>/
                panorama-<uid>.png
                metadata-<uid>.json   # 包含抓取到的元信息以及 city 字段

    参数:
        dataset_dir: 数据集根目录路径（即 --batch_out_dir）
        debug: 是否输出调试信息（进度、跳过原因、错误等）。默认 False。
        progress_every: debug 模式下每处理多少个条目输出一次进度。默认 200。

    返回:
        一个字典列表，每个字典包含：
        - uid: str
        - image_path: str
        - metadata_path: str
        - city: Optional[str]
        - metadata: Dict[str, Any]
    """
    if not os.path.isdir(dataset_dir):
        raise NotADirectoryError(f"dataset_dir is not a directory: {dataset_dir}")

    records: List[Dict[str, Any]] = []

    if debug:
        print(f"[load] Scanning dataset_dir: {dataset_dir}", flush=True)

    try:
        entries = os.listdir(dataset_dir)
    except Exception as e:
        if debug:
            print(f"[load] os.listdir failed: {e}", flush=True)
        raise

    total_entries = len(entries)
    if debug:
        print(f"[load] Found {total_entries} entries (directories expected)", flush=True)

    for idx, entry_name in enumerate(entries):
        entry_path = os.path.join(dataset_dir, entry_name)
        if not os.path.isdir(entry_path):
            continue

        uid = entry_name
        image_filename = f"panorama-{uid}.png"
        meta_filename = f"metadata-{uid}.json"

        image_path = os.path.join(entry_path, image_filename)
        metadata_path = os.path.join(entry_path, meta_filename)

        if not os.path.isfile(image_path) or not os.path.isfile(metadata_path):
            # 跳过不完整样本
            if debug and (idx % progress_every == 0):
                print(f"[load] Incomplete sample skipped at idx={idx}, uid={uid}", flush=True)
            continue

        if debug and (idx % progress_every == 0):
            print(f"[load] Progress {idx}/{total_entries} | reading metadata for uid={uid} | collected={len(records)}", flush=True)

        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata: Dict[str, Any] = json.load(f)
        except Exception as e:
            # 元数据损坏，跳过该样本
            if debug:
                print(f"[load] Failed to read metadata for uid={uid}: {e}", flush=True)
            continue

        record: Dict[str, Any] = {
            "uid": uid,
            "image_path": image_path,
            "metadata_path": metadata_path,
            "city": metadata.get("city"),
            "metadata": metadata,
        }
        records.append(record)

    # 为了可重复性，按 uid 排序
    records.sort(key=lambda r: r["uid"])

    if debug:
        print(f"[load] Completed. Returned records={len(records)} (unique uids) out of {total_entries} entries", flush=True)

    return records


if __name__ == "__main__":
    # 简单自测：打印样本数量
    import argparse

    parser = argparse.ArgumentParser(description="Load panorama dataset and print stats")
    parser.add_argument("--dataset_dir", type=str, help="Path to dataset root directory")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    parser.add_argument("--progress_every", type=int, default=200, help="Progress print frequency when debug is on")
    args = parser.parse_args()

    items = load(args.dataset_dir, debug=args.debug, progress_every=args.progress_every)
    print(f"Loaded records: {len(items)}")
    if items:
        example = items[0]
        print("Example:")
        print(json.dumps({
            "uid": example["uid"],
            "image_path": example["image_path"],
            "city": example["city"],
            "metadata_keys": list(example["metadata"].keys()),
        }, indent=2, ensure_ascii=False)) 