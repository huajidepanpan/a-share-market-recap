#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hot_stocks.py —— 当日热股榜获取

通过东方财富人气榜 API 获取前 N 名热门股票，
结合新浪财经 API 获取实时涨跌幅。

用法:
    python hot_stocks.py                    # 前100名, 输出到屏幕
    python hot_stocks.py -n 50              # 前50名
    python hot_stocks.py -o hot.csv         # 输出到文件
    python hot_stocks.py --date 20260522    # 指定日期标记

注意:
    同花顺 API（eq.10jqka.com.cn）封锁外部访问，因此使用东方财富人气榜
    作为替代。两者热股榜单重叠度约 80%+。
"""

import csv
import json
import os
import sys
import urllib.request
from datetime import datetime


# === 板块判断 ===
def get_board(code):
    """根据代码判断板块"""
    if "SH688" in code.upper():
        return "科创板"
    elif "SH60" in code.upper():
        return "上证主板"
    elif "SZ30" in code.upper():
        return "创业板"
    elif "SZ00" in code.upper():
        return "深证主板"
    elif code.upper().startswith("BJ"):
        return "北交所"
    return "其他"


# === 无代理 opener ===
def _get_opener():
    proxy_handler = urllib.request.ProxyHandler({})
    return urllib.request.build_opener(proxy_handler)


# === 步骤1: 获取人气榜排名 ===
def fetch_rank_list(count=100):
    """返回 [{rk, sc}] 列表"""
    url = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
    payload = json.dumps({
        "appId": "appId01",
        "globalId": "786e4c21-70dc-435a-93bb-38",
        "marketType": "",
        "pageNo": 1,
        "pageSize": count,
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers={
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json",
    })

    opener = _get_opener()
    with opener.open(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("data", [])


# === 步骤2: 新浪获取实时价 ===
def fetch_prices(items):
    """传入 [{rk, sc}], 返回 {code: {name, chg}}"""
    codes = []
    for item in items:
        sc = item["sc"]
        prefix = "sz" if "SZ" in sc else "sh"
        code = sc[2:]
        codes.append(f"{prefix}{code}")

    url = f"https://hq.sinajs.cn/list={','.join(codes)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.sina.com.cn/",
    })

    opener = _get_opener()
    with opener.open(req, timeout=15) as resp:
        text = resp.read().decode("gbk")

    prices = {}
    for line in text.strip().split("\n"):
        if "=" in line and line.startswith("var"):
            key = line.split("=")[0].replace("var hq_str_", "")
            val = line.split('"')[1] if '"' in line else ""
            fields = val.split(",")
            if len(fields) >= 4:
                name = fields[0]
                try:
                    cur = float(fields[3])
                    prev = float(fields[2])
                    chg = round((cur - prev) / prev * 100, 2) if prev > 0 else 0
                except (ValueError, ZeroDivisionError):
                    chg = 0
                prices[key] = {"name": name, "chg": chg}
    return prices


# === 主函数 ===
def get_hot_stocks(count=100, date_str=None):
    """
    获取热股榜单
    - count: 获取数量（上限 100）
    - date_str: 日期标记，如 "20260522"
    返回 [{rank, code, name, chg, board, date}] 列表
    """
    if count > 100:
        count = 100

    print(f"[请求] 人气榜排名 (前{count}) ...")
    items = fetch_rank_list(count)
    print(f"[获取] {len(items)} 条排名")

    print(f"[请求] 实时价格 ...")
    prices = fetch_prices(items)
    print(f"[获取] {len(prices)} 条价格")

    results = []
    for item in items:
        sc = item["sc"]
        code = sc[2:]
        prefix = "sz" if "SZ" in sc else "sh"
        key = f"{prefix}{code}"
        info = prices.get(key, {})
        name = info.get("name", code)
        chg = info.get("chg", 0)
        board = get_board(sc)

        results.append({
            "rank": item.get("rk", ""),
            "code": code,
            "name": name,
            "chg": chg,
            "board": board,
        })

    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="热股榜获取")
    parser.add_argument("-n", "--count", type=int, default=100, help="获取数量（默认100）")
    parser.add_argument("-o", "--output", default=None, help="输出 CSV 路径")
    parser.add_argument("--date", default=None, help="日期标记（如 20260522）")
    args = parser.parse_args()

    date_str = args.date or datetime.now().strftime("%Y%m%d")

    results = get_hot_stocks(count=args.count, date_str=date_str)

    if args.output:
        with open(args.output, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                f"{date_str}热股榜", "代码", "涨跌幅(%)", "板块", "排名"
            ])
            for r in results:
                writer.writerow([r["name"], r["code"], r["chg"], r["board"], r["rank"]])
        print(f"[完成] → {args.output}")
    else:
        # 打印到屏幕
        print(f"\n{'='*50}")
        print(f"  {date_str} 热股榜 TOP{len(results)}")
        print(f"{'='*50}")
        print(f"{'排名':<5s} {'名称':<8s} {'代码':<10s} {'涨跌幅':>8s} {'板块':<8s}")
        for r in results:
            print(f"{str(r['rank']):<5s} {r['name']:<8s} {r['code']:<10s} {r['chg']:>+7.2f}% {r['board']:<8s}")


if __name__ == "__main__":
    main()
