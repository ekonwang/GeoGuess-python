#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import random
from typing import Any, Dict, Optional
from streetview import get_panorama

import requests
import json
from uuid import uuid4
from tqdm import tqdm

from gawc_city import list_gawc_city
from get_panorama import build_parser, request_pano_pipeline
from utils import print_hl

def main():
    args = build_parser().parse_args()

    # 抽取 GAWC 全球排名靠前城市，保证全景图采集来自大城市 
    cities = list_gawc_city(threshold="gamma+", strictly_higher=True)

    num_success = 0
    # 序列 query，避免 Google 封号
    progress_bar = tqdm(total=args.num_query, desc="Querying")
    progress_bar.set_postfix(cur_city='None', success=num_success)

    # 开始 query
    for i in range(args.num_query):
        city = random.choice(cities)
        uid = str(uuid4())
        output = os.path.join(args.batch_out_dir, uid)
        img_output = os.path.join(output, f"panorama-{uid}.png")

        # 更换参数
        args.zoom = 5
        args.city = city
        args.output = img_output
        rst = request_pano_pipeline(args)
        if rst[0] == 0:
            print_hl(f'Panorama saved to {img_output}')
            num_success += 1
            metadata_output = os.path.join(output, f"metadata-{uid}.json")
            rst[1].update({"city": city})
            with open(metadata_output, "w") as f:
                json.dump(rst[1], f, indent=4, ensure_ascii=False)
        else:
            print("Fail")

        progress_bar.update(1)
        progress_bar.set_postfix(cur_city=city, status=rst[0], success=num_success)
    progress_bar.close()
    print(f"Total queries: {args.num_query}, success: {num_success}")

if __name__ == '__main__':
    main()
