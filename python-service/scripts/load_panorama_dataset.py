#!/usr/bin/env python3
from __future__ import annotations

import os
import json
from typing import Any, Dict, List, Optional


def load(dataset_dir: str) -> List[Dict[str, Any]]:
    """
    加载由 `batch_panorama.py` 生成的数据集。

    目录结构（每条样本一个子目录，目录名为 uid）：
        <dataset_dir>/
            <uid>/
                panorama-<uid>.png
                metadata-<uid>.json   # 包含抓取到的元信息以及 city 字段

    参数:
        dataset_dir: 数据集根目录路径（即 --batch_out_dir）

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

    for entry_name in os.listdir(dataset_dir):
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
            continue

        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata: Dict[str, Any] = json.load(f)
        except Exception:
            # 元数据损坏，跳过该样本
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

    return records


if __name__ == "__main__":
    # 简单自测：打印样本数量
    import argparse

    parser = argparse.ArgumentParser(description="Load panorama dataset and print stats")
    parser.add_argument("--dataset_dir", type=str, help="Path to dataset root directory")
    args = parser.parse_args()

    items = load(args.dataset_dir)
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