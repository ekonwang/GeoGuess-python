#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import random
from typing import Any, Dict, Optional, Tuple
from streetview import get_panorama

import requests
import json
from uuid import uuid4
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from gawc_city import list_gawc_city
from get_panorama import build_parser, request_pano_pipeline
from utils import print_hl


def _clone_args_with_overrides(base_args: argparse.Namespace, **overrides: Any) -> argparse.Namespace:
    cloned = argparse.Namespace(**vars(base_args))
    for k, v in overrides.items():
        setattr(cloned, k, v)
    return cloned


def _prepare_task(args: argparse.Namespace, cities: list[str]) -> Tuple[argparse.Namespace, str, str, str]:
    city = random.choice(cities)
    uid = str(uuid4())
    output_dir = os.path.join(args.batch_out_dir, uid)
    img_output = os.path.join(output_dir, f"panorama-{uid}.png")
    task_args = _clone_args_with_overrides(args, zoom=3, city=city, output=img_output)
    return task_args, city, uid, output_dir


def main():
    parser = build_parser()
    parser.add_argument("--concurrency", type=int, default=1, help="Number of parallel queries. Use 1 to keep original sequential behavior.")
    args = parser.parse_args()

    # 抽取 GAWC 全球排名靠前城市，保证全景图采集来自大城市 
    cities = ['Beijing', 'Dubai', 'Hong Kong', 'Paris', 'Shanghai', 'Singapore', 'Sydney', 'Tokyo', 'Amsterdam', 'Bangkok', 'Chicago', 'Frankfurt', 'Guangzhou', 'Istanbul', 'Jakarta', 'Kuala Lumpur', 'Los Angeles', 'Madrid', 'Mexico City', 'Milan', 'Mumbai', 'São Paulo', 'Seoul', 'Toronto', 'Warsaw', 'Berlin', 'Boston', 'Brussels', 'Buenos Aires', 'Dublin', 'Düsseldorf', 'Houston', 'Johannesburg', 'Lisbon', 'Luxembourg City', 'Melbourne', 'Munich', 'New Delhi', 'Riyadh', 'San Francisco', 'Santiago', 'Shenzhen', 'Stockholm', 'Taipei', 'Vienna', 'Washington, D.C.', 'Zurich', 'Athens', 'Atlanta', 'Auckland', 'Barcelona', 'Bengaluru', 'Bogotá', 'Bucharest', 'Budapest', 'Chengdu', 'Dallas', 'Doha', 'Hamburg', 'Hangzhou', 'Ho Chi Minh City', 'Lima', 'Miami', 'Montreal', 'Prague', 'Rome', 'Tianjin', 'Abu Dhabi', 'Brisbane', 'Cairo', 'Calgary', 'Chongqing', 'Copenhagen', 'Dalian', 'Geneva', 'Hanoi', 'Jinan', 'Kyiv', 'Manama', 'Manila', 'Nairobi', 'Nanjing', 'Oslo', 'Perth', 'Shenyang', 'Suzhou', 'Tel Aviv', 'Wuhan', 'Xiamen', 'Zhengzhou', 'Beirut', 'Belgrade', 'Bratislava', 'Caracas', 'Casablanca', 'Changsha', 'Chennai', 'Denver', 'Hefei', 'Helsinki', 'Karachi', 'Kunming', 'Lagos', 'Lyon', 'Manchester', 'Montevideo', 'Nicosia', 'Panama City', 'Philadelphia', 'Port Louis', 'Qingdao', 'Rio de Janeiro', 'Seattle', 'Sofia', 'Stuttgart', 'Vancouver', "Xi'an", 'Zagreb']

    num_success = 0

    # 并发或序列执行，默认序列，避免流量过大
    if args.concurrency <= 1:
        # 序列 query，避免 Google 封号
        progress_bar = tqdm(total=args.num_query, desc="Querying")
        progress_bar.set_postfix(cur_city='None', success=num_success)

        for i in range(args.num_query):
            task_args, city, uid, output_dir = _prepare_task(args, cities)
            # 轻微抖动，降低突发流量
            time.sleep(random.uniform(0.05, 0.2))
            rst = request_pano_pipeline(task_args)
            if rst[0] == 0:
                print_hl(f'Panorama saved to {task_args.output}')
                num_success += 1
                metadata_output = os.path.join(output_dir, f"metadata-{uid}.json")
                rst[1].update({"city": city})
                os.makedirs(output_dir, exist_ok=True)
                with open(metadata_output, "w") as f:
                    json.dump(rst[1], f, indent=4, ensure_ascii=False)
            else:
                print("Fail")

            progress_bar.update(1)
            progress_bar.set_postfix(cur_city=city, status=rst[0], success=num_success)
        progress_bar.close()
    else:
        # 并发 query，限制并发度，且做好异常保护
        progress_bar = tqdm(total=args.num_query, desc=f"Querying (concurrency={args.concurrency})")
        progress_bar.set_postfix(cur_city='None', success=num_success)

        # 预先生成任务，避免在多线程中修改共享 args
        tasks = [_prepare_task(args, cities) for _ in range(args.num_query)]

        def _run_task(task_args: argparse.Namespace) -> Tuple[int, Dict[str, Any]]:
            # 轻微抖动，降低突发流量
            time.sleep(random.uniform(0.05, 0.2))
            try:
                return request_pano_pipeline(task_args)
            except Exception:
                # 捕获所有异常，不影响主进程
                return 1, {"error": "exception in request_pano_pipeline"}

        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as executor:
            future_map = {}
            for (task_args, city, uid, output_dir) in tasks:
                fut = executor.submit(_run_task, task_args)
                future_map[fut] = (task_args, city, uid, output_dir)

            for fut in as_completed(future_map):
                task_args, city, uid, output_dir = future_map[fut]
                try:
                    rst = fut.result()
                except Exception:
                    rst = (1, {"error": "unhandled future exception"})

                if rst[0] == 0:
                    print_hl(f'Panorama saved to {task_args.output}')
                    num_success += 1
                    metadata_output = os.path.join(output_dir, f"metadata-{uid}.json")
                    try:
                        os.makedirs(output_dir, exist_ok=True)
                        rst[1].update({"city": city})
                        with open(metadata_output, "w") as f:
                            json.dump(rst[1], f, indent=4, ensure_ascii=False)
                    except Exception:
                        # metadata 写入失败不影响主流程
                        pass
                else:
                    print("Fail")

                progress_bar.update(1)
                progress_bar.set_postfix(cur_city=city, status=rst[0], success=num_success)
        progress_bar.close()

    print(f"Total queries: {args.num_query}, success: {num_success}")

if __name__ == '__main__':
    main()
