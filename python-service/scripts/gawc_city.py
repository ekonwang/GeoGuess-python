# -*- coding: utf-8 -*-
"""
从 Wikipedia 的 “Globalization and World Cities Research Network” 页面抓取
“2024 city classification” 的城市列表，并返回“高于 threshold (默认: Beta-)”的城市列表。
包含丰富的 debug 打印，便于定位问题。

依赖:
    pip install requests beautifulsoup4
"""

import re
import sys
import requests
from bs4 import BeautifulSoup, Tag

WIKI_URL = "https://en.wikipedia.org/wiki/Globalization_and_World_Cities_Research_Network"

# 等级从高到低的“标准写法”（全部用 ASCII '-'）
LEVELS = [
    "Alpha ++", "Alpha +", "Alpha", "Alpha -",
    "Beta +", "Beta", "Beta -",
    "Gamma +", "Gamma", "Gamma -",
    "High sufficiency", "Sufficiency",
]

MAIN_SECTIONS = {"Alpha", "Beta", "Gamma", "Sufficiency"}  # h3 主段

def normalize_dashes(s: str) -> str:
    """把各种 dash/minus 统一成 ASCII '-'，并压缩空格，使 'Alpha−' / 'Alpha–' 等能对齐到 'Alpha -'。"""
    if not s:
        return s
    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
    # 'Alpha-' / 'Alpha  -' -> 'Alpha -'
    s = re.sub(r"\s*-\s*", " - ", s)
    # 'Alpha+' -> 'Alpha +'
    s = re.sub(r"\s*\+\s*", " + ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def canonical_level(raw: str) -> str:
    """把抓到的标题文本规范化到 LEVELS 中的写法；不匹配则返回空串。"""
    x = normalize_dashes(raw or "")
    for lv in LEVELS:
        if x.lower() == lv.lower():
            return lv
    return ""

def find_h2_2024(soup: BeautifulSoup, debug=True) -> Tag | None:
    """
    找到“2024 city classification”的 h2。
    常见结构：<h2><span id="2024_city_classification">2024 city classification</span></h2>
    """
    span = soup.find("span", id="2024_city_classification")
    if span and span.parent and span.parent.name == "h2":
        if debug:
            print("[debug] 找到 h2 via span#2024_city_classification")
        return span.parent

    # 文本兜底
    h2 = soup.find(lambda t: isinstance(t, Tag) and t.name == "h2" and
                   "2024 city classification" in t.get_text(strip=True).lower())
    if h2 and debug:
        print("[debug] 找到 h2 via 文本匹配：", h2.get_text(strip=True))
    return h2

def first_ul_after(header: Tag) -> Tag | None:
    """
    从给定标题（通常是 h4）开始沿文档流（.next_elements）向后扫描，
    在遇到下一个 h2/h3/h4 之前，返回遇到的第一个 <ul>（城市列表常被包在 div.div-col 里）。
    """
    for el in header.next_elements:
        if isinstance(el, Tag):
            if el is header:
                continue
            if el.name in ("h2", "h3", "h4"):
                # 到了下一段/下个子标题，停止
                return None
            if el.name == "ul":
                return el
    return None

def extract_city_from_li(li: Tag) -> str:
    """
    尽量从 <li> 中提取“城市名”，策略：
      1) 取最后一个非 'Image:' 的 <a> 文本（Wiki 列表常是国家链接 + 城市链接）；
      2) 若没有 <a>，则取 li 的纯文本，去掉脚注/括号中的上升下降标记，但不要拆逗号（保留 'Washington, D.C.'）。
    """
    anchors = li.find_all("a")
    candidates = []
    for a in anchors:
        t = a.get_text(strip=True)
        if not t or t.lower().startswith("image:"):
            continue
        candidates.append(t)
    if candidates:
        return candidates[-1]

    text = li.get_text(" ", strip=True)
    # 去掉诸如 "(1)" "(2)" 的标记
    text = re.sub(r"\(\d+\)\s*$", "", text).strip()
    # 去掉文末引用 [1] [2]
    text = re.sub(r"\[\d+\]\s*$", "", text).strip()
    return text

def scrape_2024(debug=True) -> dict[str, list[str]]:
    """
    抓取 '2024 city classification' 段落内的所有等级与城市。
    返回: { level: [city, ...], ... }  其中 level ∈ LEVELS
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; GaWC-Crawler/1.0; +https://example.com)"
    }
    print("[1/7] 请求页面：", WIKI_URL)
    resp = requests.get(WIKI_URL, headers=headers, timeout=30)
    print("[2/7] HTTP 状态码：", resp.status_code)
    resp.raise_for_status()
    print("[3/7] HTML 长度：", len(resp.text))

    soup = BeautifulSoup(resp.text, "html.parser")
    h2 = find_h2_2024(soup, debug=debug)
    if not h2:
        raise RuntimeError("未找到 '2024 city classification' 的 h2 标题；页面结构可能变化。")

    print("[4/7] 锚点标题：", h2.get_text(strip=True))

    by_level: dict[str, list[str]] = {lv: [] for lv in LEVELS}
    current_main = None
    h4_count = 0

    # 用“文档流遍历”而非“兄弟节点”，更稳健
    for el in h2.next_elements:
        if not isinstance(el, Tag):
            continue
        # 走到下一个 h2 就停
        if el.name == "h2" and el is not h2:
            break

        if el.name == "h3":
            t = normalize_dashes(el.get_text(strip=True))
            # 只认 Alpha/Beta/Gamma/Sufficiency 这四个主段
            if any(t.lower().startswith(m.lower()) for m in MAIN_SECTIONS):
                current_main = t
                if debug:
                    print(f"[debug] 进入主段（h3）：{current_main}")
            else:
                current_main = None
            continue

        if el.name == "h4" and current_main:
            raw = el.get_text(strip=True)
            lv = canonical_level(raw)
            if not lv:
                if debug:
                    print(f"[debug] 跳过 h4（非子等级）：{normalize_dashes(raw)}")
                continue

            h4_count += 1
            ul = first_ul_after(el)
            if not ul:
                if debug:
                    print(f"[debug] '{lv}' 后未找到 <ul>，可能页面结构调整。")
                continue

            # li 可能被 div.div-col 包裹，但我们已经拿到 ul，直接取所有 li
            lis = ul.find_all("li")
            cities = []
            for li in lis:
                name = extract_city_from_li(li)
                if name:
                    cities.append(name)

            # 去重保序
            deduped, seen = [], set()
            for c in cities:
                if c not in seen:
                    seen.add(c)
                    deduped.append(c)

            by_level[lv].extend(deduped)
            print(f"[5/7] 抓到子等级: {lv:<18} 城市数: {len(deduped):>3}  示例: {deduped[:5]}")

    total = sum(len(v) for v in by_level.values())
    print(f"[6/7] 抓取完毕。累计 h4 子等级: {h4_count}  总城市数: {total}")
    for lv in LEVELS:
        print(f"         {lv:<18} -> {len(by_level[lv])}")

    print("[7/7] 解析完成。")
    return by_level

def list_gawc_city(threshold: str = "Beta-", strictly_higher: bool = True, debug: bool = True) -> list[str]:
    """
    返回“高于 threshold”的城市列表（默认阈值 Beta-，且 **严格高于**）。
    如果你想“含阈值”，把 strictly_higher=False。

    threshold 可选值（不区分大小写，减号会自动规整）：
        Alpha ++ / Alpha + / Alpha / Alpha -
        Beta + / Beta / Beta -
        Gamma + / Gamma / Gamma -
        High sufficiency / Sufficiency
    """
    th_norm = normalize_dashes(threshold)
    th_canon = ""
    for lv in LEVELS:
        if th_norm.lower() == lv.lower():
            th_canon = lv
            break
    if not th_canon:
        raise ValueError(f"无效 threshold: {threshold!r}（可选：{LEVELS}）")

    print(f"[debug] 归一化阈值：{threshold!r} -> {th_canon!r}")

    by_level = scrape_2024(debug=debug)

    idx = LEVELS.index(th_canon)
    # LEVELS 从高到低；高于阈值 => 取 [0:idx]，含阈值 => 取 [0:idx+1]
    keep = LEVELS[:idx] if strictly_higher else LEVELS[:idx+1]
    print(f"[debug] 参与筛选的等级（高->低）：{keep}")

    merged, seen = [], set()
    for lv in keep:
        for city in by_level.get(lv, []):
            if city not in seen:
                seen.add(city)
                merged.append(city)

    print(f"[debug] 最终筛选城市数：{len(merged)}")
    return merged


if __name__ == "__main__":
    # 用法：
    #   python list_gawc_city.py
    #   python list_gawc_city.py "Alpha"
    thr = "gamma+"
    if len(sys.argv) >= 2:
        thr = sys.argv[1]
    cities = list_gawc_city(threshold=thr, strictly_higher=True, debug=True)
    print(f"\n城市列表（高于 {thr}）：共 {len(cities)} 个")
    # for c in cities:
    #     print(c)
    print(cities)
