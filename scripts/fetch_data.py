#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_data.py —— akshare 拉取 A 股全市场日行情数据

用于验证 akshare 数据质量与同花顺导出 Table.xls 的一致性。

用法:
    python fetch_data.py                            # 拉取最近交易日
    python fetch_data.py -d 20260605                # 指定日期
    python fetch_data.py -d 20260605 --sample 100   # 采样模式(快)
    python fetch_data.py -d 20260605 --full         # 全量模式(慢,~10min)

输出:
    {日期}/Table_ak.csv          全量输出
    {日期}/Table_ak_sample.csv   采样输出

依赖:
    pip install akshare pandas tqdm
"""

import argparse
import csv
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ========== 路径 ==========
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS_DIR)


def fetch_stock_list():
    """获取当前A股列表（含实时快照）"""
    import akshare as ak
    print("[1/3] 获取A股列表...")
    df = ak.stock_zh_a_spot_em()
    print(f"  共 {len(df)} 只")
    return df


def fetch_hist_batch(codes, date_str):
    """并发拉取一批个股的历史日K线（仅指定日期那一行）"""
    import akshare as ak
    import pandas as pd

    results = {}
    date_norm = date_str[:4] + "-" + date_str[4:6] + "-" + date_str[6:8]

    def _fetch_one(code):
        try:
            symbol = code  # akshare 接受 sh600001 或 600001
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=date_str,
                end_date=date_str,
                adjust="qfq",
            )
            if df is not None and not df.empty:
                row = df[df["日期"] == date_norm]
                if not row.empty:
                    return code, row.iloc[0].to_dict()
        except Exception:
            pass
        return code, None

    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(_fetch_one, c): c for c in codes}
        for fut in as_completed(futures):
            code, row = fut.result()
            if row:
                results[code] = row

    return results


def build_full_codes(spot_df):
    """从 spot DataFrame 中提取带前缀的代码列表"""
    codes = []
    for _, r in spot_df.iterrows():
        code = str(r["代码"]).upper()
        # 确保有 sh/sz/bj 前缀
        if not code.startswith(("SH", "SZ", "BJ")):
            # 根据代码推断市场
            if code.startswith("6"):
                code = "SH" + code
            elif code.startswith(("0", "3")):
                code = "SZ" + code
            elif code.startswith(("4", "8")):
                code = "BJ" + code
        codes.append(code)
    return codes


def build_output_row(stock, spot_row):
    """
    把 akshare spot 快照数据 + 个股历史日线数据 拼成与 Table.csv 对齐的行。

    spot_row:  stock_zh_a_spot_em() 的一行 (当前快照)
    stock:     该股指定日期的历史日K线数据 (dict，来自 stock_zh_a_hist)
              如果是当天数据则为 None，直接用 spot_row
    """
    # 代码 (带格式)
    code_raw = str(spot_row.get("代码", ""))
    code = code_raw.upper()
    if not code.startswith(("SH", "SZ", "BJ")):
        if code.startswith("6"):
            code = "SH" + code
        elif code.startswith(("0", "3")):
            code = "SZ" + code
        elif code.startswith(("4", "8")):
            code = "BJ" + code

    name = str(spot_row.get("名称", ""))

    # 从历史K线取数据（如果是历史日期），否则从 spot 取
    if stock is not None:
        chg_pct = stock.get("涨跌幅", 0)
        close = stock.get("收盘", 0)
        open_price = stock.get("开盘", 0)
        high = stock.get("最高", 0)
        low = stock.get("最低", 0)
        volume = stock.get("成交量", 0)
        amount = stock.get("成交额", 0)
        amplitude = stock.get("振幅", 0)
        turnover = stock.get("换手率", 0)
        chg_amt = stock.get("涨跌额", 0)
    else:
        chg_pct = safe_num(spot_row.get("涨跌幅"))
        close = safe_num(spot_row.get("最新价"))
        open_price = ""  # spot 快照无开盘价
        high = safe_num(spot_row.get("最高"))
        low = safe_num(spot_row.get("最低"))
        volume = safe_num(spot_row.get("成交量"))
        amount = safe_num(spot_row.get("成交额"))
        amplitude = safe_num(spot_row.get("振幅"))
        turnover = safe_num(spot_row.get("换手率"))
        chg_amt = safe_num(spot_row.get("涨跌额"))

    # 公共字段（spot 快照提供）
    pe = safe_num(spot_row.get("市盈率-动态"))
    pb = safe_num(spot_row.get("市净率"))
    mcap = safe_num(spot_row.get("总市值"))
    mcap_circ = safe_num(spot_row.get("流通市值"))
    vol_ratio = safe_num(spot_row.get("量比"))

    # 计算 昨收
    prev_close = round(float(close) - float(chg_amt), 2) if chg_amt and close else ""

    return {
        "代码": code,
        "名称": name,
        "涨幅": f"{chg_pct:+.2f}" if chg_pct else "0.00",
        "现价": f"{close:.2f}" if close else "",
        "涨跌": f"{chg_amt:+.2f}" if chg_amt else "",
        "总手": volume,
        "总金额": amount,
        "换手": turnover,
        "总市值": mcap,
        "流通市值": mcap_circ,
        "量比": vol_ratio,
        "TTM市盈率": pe,
        "市净率": pb,
        "昨收": prev_close,
        "开盘": open_price,
        "最高": high,
        "最低": low,
        "振幅": amplitude,
        # 标识数据来源
        "数据来源": "akshare",
    }


def safe_num(val):
    """安全转数值"""
    if val is None:
        return ""
    if isinstance(val, (int, float)):
        return val
    try:
        return float(str(val).replace("%", "").replace(",", ""))
    except (ValueError, TypeError):
        return ""


def write_csv(output_path, rows, col_order):
    """写 CSV"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=col_order, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def print_summary(rows):
    """打印数据摘要"""
    if not rows:
        print("  无数据")
        return

    n = len(rows)
    chg_vals = [float(r["涨幅"]) for r in rows if r["涨幅"] not in ("", "--")]
    amt_vals = [float(r["总金额"]) for r in rows if r["总金额"] not in ("", "--", 0)]
    pe_vals = [float(r["TTM市盈率"]) for r in rows
               if r["TTM市盈率"] not in ("", "--", 0)]

    print(f"\n{'='*50}")
    print(f"  数据概要")
    print(f"{'='*50}")
    print(f"  总记录数:      {n}")
    if chg_vals:
        print(f"  涨幅范围:      {min(chg_vals):+.2f}% ~ {max(chg_vals):+.2f}%")
        up = sum(1 for c in chg_vals if c > 0)
        down = sum(1 for c in chg_vals if c < 0)
        zero = sum(1 for c in chg_vals if c == 0)
        print(f"  涨/跌/平:      {up} / {down} / {zero}")
        print(f"  中位数涨幅:     {sorted(chg_vals)[len(chg_vals)//2]:+.2f}%")
    if amt_vals:
        total_amt = sum(amt_vals)
        print(f"  总成交额:      {total_amt/1e8:.0f}亿")
    if pe_vals:
        pe_clean = [p for p in pe_vals if 0 < p < 10000]
        if pe_clean:
            print(f"  PE(TTM)中位数: {sorted(pe_clean)[len(pe_clean)//2]:.1f}")
    print(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(
        description="akshare 拉取A股全市场日行情 → CSV（用于验证数据质量）"
    )
    parser.add_argument(
        "-d", "--date",
        default=None,
        help="日期 YYYYMMDD（默认: 最近交易日）",
    )
    parser.add_argument(
        "--sample", type=int, default=0,
        help="采样模式: 仅拉取 N 只股票（快速验证，默认0=全量）",
    )
    parser.add_argument(
        "--full", action="store_true",
        help="全量拉取（历史日期 + 全部个股，约 5000 只，需 5-10 分钟）",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="输出 CSV 路径（默认: {日期}/Table_ak.csv）",
    )
    args = parser.parse_args()

    # ---- 确定日期 ----
    if args.date:
        date_str = args.date.replace("-", "")
    else:
        date_str = time.strftime("%Y%m%d")

    # 是否需要历史数据
    today = time.strftime("%Y%m%d")
    is_today = (date_str == today)

    print(f"目标日期: {date_str}")
    print(f"模式: {'采样' if args.sample else ('全量' if args.full else '快照')}")

    # ---- 获取股票列表 ----
    spot_df = fetch_stock_list()
    all_codes = build_full_codes(spot_df)

    # ---- 采样 ----
    if args.sample and args.sample < len(all_codes):
        import random
        random.seed(42)
        all_codes = random.sample(all_codes, args.sample)
        print(f"  采样 {len(all_codes)} 只")

    # ---- 如果是历史日期：批量拉取日K线 ----
    hist_data = {}
    if not is_today:
        n = len(all_codes)
        print(f"\n[2/3] 拉取 {date_str} 历史日K线 ({n} 只, ~{n/100:.0f}秒)...")
        t0 = time.time()
        hist_data = fetch_hist_batch(all_codes, date_str)
        elapsed = time.time() - t0
        print(f"  完成: {len(hist_data)}/{n} 只有效数据, 耗时 {elapsed:.0f}s")
    else:
        print(f"\n[2/3] 使用实时快照数据 ({date_str} 是今天)")

    # ---- 构建输出 ----
    print("\n[3/3] 构建 CSV...")
    col_order = [
        "代码", "名称", "涨幅", "现价", "涨跌",
        "总手", "总金额", "换手", "量比",
        "总市值", "流通市值",
        "TTM市盈率", "市净率",
        "昨收", "开盘", "最高", "最低", "振幅",
        "数据来源",
    ]

    spot_dict = {}
    for _, r in spot_df.iterrows():
        c = str(r["代码"]).upper()
        if not c.startswith(("SH", "SZ", "BJ")):
            if c.startswith("6"):
                c = "SH" + c
            elif c.startswith(("0", "3")):
                c = "SZ" + c
            elif c.startswith(("4", "8")):
                c = "BJ" + c
        spot_dict[c] = r

    rows = []
    for code in all_codes:
        spot_row = spot_dict.get(code, {})
        stock = hist_data.get(code) if hist_data else None
        row = build_output_row(stock, spot_row)
        if row["现价"] and row["现价"] != "":
            rows.append(row)

    # ---- 输出路径 ----
    if args.output:
        output_path = args.output
    else:
        if args.sample:
            output_path = os.path.join(REPO_ROOT, date_str, "Table_ak_sample.csv")
        else:
            output_path = os.path.join(REPO_ROOT, date_str, "Table_ak.csv")

    n_written = write_csv(output_path, rows, col_order)
    print(f"\n写入: {n_written} 行 → {output_path}")
    print_summary(rows)


if __name__ == "__main__":
    main()
