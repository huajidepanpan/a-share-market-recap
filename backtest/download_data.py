#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
download_data.py —— 下载A股全量个股日K线数据

数据来源: akshare (东方财富)
输出: backtest/data/ 目录下，每只股票一个 .pkl 文件

用法:
    python download_data.py                          # 下载全部A股
    python download_data.py --start 20250101         # 指定起始日期
    python download_data.py --workers 20             # 并发数
    python download_data.py --retry                  # 仅重试失败的

依赖:
    pip install akshare pandas tqdm
"""

import argparse
import os
import pickle
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import pandas as pd

# ========== 配置 ==========
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STOCK_LIST_FILE = os.path.join(DATA_DIR, "stock_list.pkl")
FAILED_FILE = os.path.join(DATA_DIR, "failed.txt")

DEFAULT_START = "20250101"  # 默认从2025年开始（覆盖回测所需范围）
DEFAULT_END = "20260602"


SECTOR_MAP_FILE = os.path.join(DATA_DIR, "sector_map.pkl")


def get_stock_list():
    """获取A股股票列表"""
    import akshare as ak
    print("[1/4] 获取股票列表...")
    df = ak.stock_zh_a_spot_em()
    # 保留需要的列（兼容不同版本akshare列名差异）
    cols_map = {
        "代码": "code", "名称": "name", "最新价": "price",
        "涨跌幅": "chg_pct", "总市值": "mcap",
        "市盈率-动态": "pe", "换手率": "turnover",
    }
    # akshare新版本列名可能不同
    if "代码" in df.columns:
        df = df.rename(columns=cols_map)
    else:
        # 尝试英文列名
        en_cols = ["code", "name", "price", "chg_pct", "mcap", "pe", "turnover"]
        for c in en_cols:
            if c not in df.columns:
                df[c] = None
    df = df[["code", "name", "price", "chg_pct", "mcap", "pe", "turnover"]]
    df = df[df["code"].str.match(r"^(sh|sz|bj)\d{6}$", case=False)]
    print(f"  共 {len(df)} 只股票")
    return df


def build_sector_map():
    """构建个股→行业分类映射（东方财富行业板块）

    遍历所有行业板块及其成分股，构建映射表。
    结果缓存到 SECTOR_MAP_FILE。
    """
    import akshare as ak
    sector_map = {}

    if os.path.exists(SECTOR_MAP_FILE):
        print("[2/4] 行业分类已缓存，跳过")
        with open(SECTOR_MAP_FILE, "rb") as f:
            return pickle.load(f)

    print("[2/4] 构建行业分类映射（遍历东方财富行业板块，约需1-2分钟）...")
    try:
        # 获取所有行业板块列表
        boards = ak.stock_board_industry_name_em()
        print(f"  共 {len(boards)} 个行业板块")

        for i, (_, row) in enumerate(boards.iterrows()):
            board_code = row["板块代码"]
            board_name = row["板块名称"]
            try:
                cons = ak.stock_board_industry_cons_em(symbol=board_code)
                for _, stock_row in cons.iterrows():
                    code = str(stock_row["代码"]).upper()
                    if code not in sector_map:
                        sector_map[code] = board_name
            except Exception:
                continue

            if (i + 1) % 20 == 0:
                print(f"  进度: {i+1}/{len(boards)} 板块, "
                      f"已映射 {len(sector_map)} 只个股")

        # 保存缓存
        with open(SECTOR_MAP_FILE, "wb") as f:
            pickle.dump(sector_map, f, protocol=pickle.HIGHEST_PROTOCOL)
        print(f"  完成! 映射 {len(sector_map)} 只个股到 {len(boards)} 个行业")

    except Exception as e:
        print(f"  [WARN] 行业分类构建失败: {e}")
        print(f"  回测将使用板块级分类（上证主板/深证主板/创业板/科创板）")

    return sector_map


def download_one(code, start_date, end_date):
    """下载单只股票日K线（前复权）

    注意: akshare stock_zh_a_hist 返回的列可能因版本而异。
    基础列: 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
    可能包含: 市盈率-动态 (PE-TTM), 市净率, 总市值 等
    如果 PE 列不存在，回测时会使用静态 PE 或跳过估值因子。
    """
    import akshare as ak
    symbol = code[2:]  # 去掉 sh/sz/bj 前缀

    df = ak.stock_zh_a_hist(
        symbol=symbol,
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust="qfq",  # 前复权
    )
    if df is None or df.empty:
        return None

    # 标准化列名（兼容新旧版本akshare）
    col_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "振幅": "amplitude", "涨跌幅": "chg_pct",
        "涨跌额": "chg_amt", "换手率": "turnover",
        "市盈率-动态": "pe", "市净率": "pb", "总市值": "mcap",
    }
    df.rename(columns={k: v for k, v in col_map.items() if k in df.columns}, inplace=True)

    # 保留需要的列（包括可能不存在的 pe）
    keep = ["date", "open", "close", "high", "low", "volume",
            "amount", "chg_pct", "turnover"]
    for c in ["pe", "pb", "mcap"]:
        if c in df.columns:
            keep.append(c)
    df = df[[c for c in keep if c in df.columns]]
    df["date"] = pd.to_datetime(df["date"])

    return df


def load_existing_codes():
    """检查已下载的股票"""
    if not os.path.isdir(DATA_DIR):
        return set()
    existing = set()
    for f in os.listdir(DATA_DIR):
        if f.endswith(".pkl") and f != "stock_list.pkl":
            existing.add(f.replace(".pkl", ""))
    return existing


def save_stock(code, df):
    """保存单只股票数据"""
    filepath = os.path.join(DATA_DIR, f"{code}.pkl")
    with open(filepath, "wb") as f:
        pickle.dump(df, f, protocol=pickle.HIGHEST_PROTOCOL)


def load_stock(code):
    """加载单只股票数据"""
    filepath = os.path.join(DATA_DIR, f"{code}.pkl")
    if not os.path.exists(filepath):
        return None
    with open(filepath, "rb") as f:
        return pickle.load(f)


def main():
    parser = argparse.ArgumentParser(description="下载A股日K线数据")
    parser.add_argument("--start", default=DEFAULT_START, help="起始日期 YYYYMMDD")
    parser.add_argument("--end", default=DEFAULT_END, help="截止日期 YYYYMMDD")
    parser.add_argument("--workers", type=int, default=15, help="并发线程数")
    parser.add_argument("--retry", action="store_true", help="仅重试失败的股票")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    # 获取股票列表
    stock_df = get_stock_list()

    # 构建行业分类映射
    sector_map = build_sector_map()

    # 确定待下载列表
    existing = load_existing_codes()
    stock_df["code_full"] = stock_df["code"].str.upper()

    if args.retry:
        # 读取失败列表
        if os.path.exists(FAILED_FILE):
            with open(FAILED_FILE, "r") as f:
                failed_codes = set(line.strip() for line in f if line.strip())
            todo = stock_df[stock_df["code_full"].isin(failed_codes)]
            print(f"[retry模式] 重试 {len(todo)} 只失败股票")
        else:
            print("[retry模式] 无失败记录，检查未下载的...")
            all_codes = set(stock_df["code_full"])
            todo_codes = all_codes - existing
            todo = stock_df[stock_df["code_full"].isin(todo_codes)]
    else:
        all_codes = set(stock_df["code_full"])
        todo_codes = all_codes - existing
        todo = stock_df[stock_df["code_full"].isin(todo_codes)]
        print(f"[3/4] 已下载: {len(existing)}, 待下载: {len(todo)}")

    if todo.empty:
        print("  所有股票已是最新！")
        todo = stock_df  # 可能数据有更新，强制刷新
        print(f"  强制刷新全部 {len(todo)} 只...")

    # 保存股票列表
    with open(STOCK_LIST_FILE, "wb") as f:
        pickle.dump(stock_df, f, protocol=pickle.HIGHEST_PROTOCOL)

    # 并发下载
    start_ts = time.time()
    success, failed = 0, 0
    failed_list = []

    print(f"[4/4] 开始下载 ({args.workers} 线程)...")

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {}
        for _, row in todo.iterrows():
            code = row["code_full"]
            fut = executor.submit(download_one, code, args.start, args.end)
            futures[fut] = (code, row.get("name", ""))

        for i, fut in enumerate(as_completed(futures)):
            code, name = futures[fut]
            try:
                df = fut.result()
                if df is not None and not df.empty:
                    save_stock(code, df)
                    success += 1
                else:
                    failed += 1
                    failed_list.append(code)
            except Exception as e:
                failed += 1
                failed_list.append(code)
                print(f"  [ERROR] {code} {name}: {e}")

            # 进度
            done = i + 1
            if done % 200 == 0 or done == len(futures):
                elapsed = time.time() - start_ts
                rate = done / max(elapsed, 1)
                eta = (len(futures) - done) / max(rate, 0.01)
                print(f"  进度: {done}/{len(futures)} "
                      f"成功={success} 失败={failed} "
                      f"速度={rate:.1f}只/s ETA={eta:.0f}s")

    # 保存失败列表
    if failed_list:
        with open(FAILED_FILE, "w") as f:
            for code in failed_list:
                f.write(code + "\n")
        print(f"\n失败 {len(failed_list)} 只，已记录到 {FAILED_FILE}")
        print("  可执行 python download_data.py --retry 重试")

    elapsed = time.time() - start_ts
    print(f"\n完成! 成功 {success}, 失败 {failed}, 总耗时 {elapsed:.0f}s")
    print(f"数据目录: {DATA_DIR}")
    print(f"\n下一步: python backtest.py")


if __name__ == "__main__":
    main()
