# -*- coding: utf-8 -*-
"""
抓取“中国城市 Top 100”并返回一个 list[str]。
优先数据源：
  1) WorldPopulationReview: https://worldpopulationreview.com/countries/cities/china
  2) Wikipedia（兜底）：尝试“List of cities in China by urban population”相关表格

依赖:
    pip install requests beautifulsoup4
"""

import re
import sys
import requests
from bs4 import BeautifulSoup, Tag

WPR_URL = "https://worldpopulationreview.com/countries/cities/china"
WIKI_URLS = [
    # 候选兜底页面（不同页面结构可能变化；我们做尽量稳健的解析）
    "https://en.wikipedia.org/wiki/List_of_cities_in_China_by_urban_population",
    "https://en.wikipedia.org/wiki/List_of_cities_in_China_by_population",
    "https://en.wikipedia.org/wiki/List_of_urban_areas_in_China",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CN-Top100-Crawler/1.0; +https://example.com)"
}

def _clean_text(s: str) -> str:
    if not s:
        return s
    # 去除脚注 [1] [a]
    s = re.sub(r"\[\s*[^\]]+\s*\]", "", s)
    # 去除多余空白
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _extract_cells(tr: Tag) -> list[str]:
    cells = []
    for td in tr.find_all(["td", "th"], recursive=False):
        txt = td.get_text(" ", strip=True)
        txt = _clean_text(txt)
        cells.append(txt)
    return cells

def fetch_wpr_top(limit: int = 100, debug: bool = True) -> list[str]:
    if debug:
        print(f"[WPR] 请求页面: {WPR_URL}")
    resp = requests.get(WPR_URL, headers=HEADERS, timeout=30)
    if debug:
        print("[WPR] HTTP 状态码：", resp.status_code)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # 寻找可能的主表：包含 City/Name 等列头
    candidate_tables = soup.find_all("table")
    best_table = None
    for tbl in candidate_tables:
        thead = tbl.find("thead")
        header_text = ""
        if thead:
            header_text = thead.get_text(" ", strip=True).lower()
        else:
            # 某些表没有 thead，用第一行 th 做头
            first_tr = tbl.find("tr")
            if first_tr:
                header_text = first_tr.get_text(" ", strip=True).lower()
        if any(k in header_text for k in ("city", "name")):
            best_table = tbl
            break

    if not best_table:
        if debug:
            print("[WPR] 未找到包含 City/Name 的表格。")
        return []

    # 找到数据行
    rows = best_table.find_all("tr")
    if not rows:
        return []

    # 识别 City 列索引
    header_cells = _extract_cells(rows[0])
    col_city_idx = None
    for i, h in enumerate(header_cells):
        h_low = h.lower()
        if any(k in h_low for k in ("city", "name")):
            col_city_idx = i
            break

    results = []
    for tr in rows[1:]:
        cells = tr.find_all(["td", "th"], recursive=False)
        if not cells:
            continue
        if col_city_idx is None:
            # 兜底：优先取包含链接的最后一个单元格文本
            texts = [c.get_text(" ", strip=True) for c in cells]
            name = _clean_text(texts[-1] if texts else "")
        else:
            if col_city_idx >= len(cells):
                continue
            cell = cells[col_city_idx]
            # 优先取 <a> 文本
            a = cell.find("a")
            name = _clean_text(a.get_text(" ", strip=True) if a else cell.get_text(" ", strip=True))
        if not name:
            continue
        if name not in results:
            results.append(name)
        if len(results) >= limit:
            break

    if debug:
        print(f"[WPR] 拿到城市数：{len(results)}  示例：{results[:5]}")
    return results

def _pick_wiki_table(tables: list[Tag]) -> Tag | None:
    # 优先选择同时包含 City/Name 与人口或行政区列的表；若有 Rank 列则更优
    best = None
    best_score = -1
    for tbl in tables:
        # 汇总前几行的头部文本，避免遇到 Legend 等占位行
        head_rows = tbl.find_all("tr", limit=3)
        headers: list[str] = []
        for r in head_rows:
            headers.extend([c.get_text(" ", strip=True).lower() for c in r.find_all(["th", "td"], recursive=False)])
        if not headers:
            continue

        has_city = any(("city" in h) or ("name" in h) for h in headers)
        if not has_city:
            continue

        score = 1
        if any("rank" in h for h in headers):
            score += 2
        if any(("province" in h) or ("municipality" in h) or ("prefecture" in h) for h in headers):
            score += 1
        if any(("population" in h) or ("census" in h) or ("2020" in h) or ("2010" in h) for h in headers):
            score += 2

        if score > best_score:
            best_score = score
            best = tbl
    return best

def fetch_wiki_top(limit: int = 100, debug: bool = True) -> list[str]:
    for url in WIKI_URLS:
        try:
            if debug:
                print(f"[WIKI] 请求页面: {url}")
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if debug:
                print("[WIKI] HTTP 状态码：", resp.status_code)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            tables = soup.find_all("table", class_=lambda x: x and "wikitable" in x)
            if not tables:
                if debug:
                    print("[WIKI] 未找到 wikitable。尝试所有 table 兜底。")
                tables = soup.find_all("table")

            tbl = _pick_wiki_table(tables)
            if not tbl:
                if debug:
                    print("[WIKI] 未找到含 Rank/City 列的表，跳过该页面。")
                continue

            rows = tbl.find_all("tr")
            if not rows:
                continue

            # 寻找真正的表头行（含 City/Name 的那一行）
            header_row_idx = None
            city_idx = None
            for ri, row in enumerate(rows):
                headers = [c.get_text(" ", strip=True).lower() for c in row.find_all(["th", "td"], recursive=False)]
                for i, h in enumerate(headers):
                    if "city" in h or "name" in h:
                        header_row_idx = ri
                        city_idx = i
                        break
                if header_row_idx is not None:
                    break
            if header_row_idx is None:
                if debug:
                    print("[WIKI] 未定位到含 City/Name 的表头行，跳过该页面。")
                continue

            results = []
            for tr in rows[header_row_idx + 1:]:
                cells = tr.find_all(["td", "th"], recursive=False)
                if not cells:
                    continue
                if city_idx is None or city_idx >= len(cells):
                    # 兜底：取含链接的最后单元格
                    candidates = [td for td in cells if td.find("a")]
                    cell = candidates[-1] if candidates else cells[-1]
                else:
                    cell = cells[city_idx]
                a = cell.find("a")
                name = _clean_text(a.get_text(" ", strip=True) if a else cell.get_text(" ", strip=True))
                if not name:
                    continue
                if name not in results:
                    results.append(name)
                if len(results) >= limit:
                    break

            if results:
                if debug:
                    print(f"[WIKI] 从 {url} 得到城市数：{len(results)}  示例：{results[:5]}")
                return results

        except Exception as e:
            if debug:
                print(f"[WIKI] 解析 {url} 出错：{e}")

    return []

def list_cn_top_cities(limit: int = 100, debug: bool = True) -> list[str]:
    if limit <= 0:
        return []

    # 先从 WPR 拿
    cities = []
    # try:
    #     cities = fetch_wpr_top(limit=limit, debug=debug)
    # except Exception as e:
    #     if debug:
    #         print(f"[WPR] 抓取失败：{e}")

    # 兜底到 Wikipedia
    if len(cities) < limit:
        remain = limit - len(cities)
        if debug:
            print(f"[info] WPR 返回 {len(cities)} 个，不足 {limit}，尝试 Wikipedia 兜底补齐 {remain} 个。")
        try:
            wiki_cities = fetch_wiki_top(limit=limit*2, debug=debug)
        except Exception as e:
            if debug:
                print(f"[WIKI] 抓取失败：{e}")
            wiki_cities = []

        # 合并去重
        seen = set(cities)
        for c in wiki_cities:
            if len(cities) >= limit:
                break
            if c not in seen:
                seen.add(c)
                cities.append(c)

    if debug:
        print(f"[done] 最终城市数：{len(cities)}")
    return cities[:limit]

if __name__ == "__main__":
    lim = 100
    if len(sys.argv) >= 2:
        try:
            lim = int(sys.argv[1])
        except ValueError:
            pass
    cities = list_cn_top_cities(limit=lim, debug=True)
    print("\n中国 Top 城市列表：", len(cities))
    print(cities)