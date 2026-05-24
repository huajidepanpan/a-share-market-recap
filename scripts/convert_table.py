#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
convert_table.py —— 同花顺导出 Table.xls → UTF-8 CSV

Table.xls 本质是 GBK 编码的 TSV（Tab 分隔），不是真正的 Excel。
此脚本将其转为标准 UTF-8 CSV，Excel/WPS 可直接打开。

用法:
    python convert_table.py ../20260522/Table.xls                    # 全量转换
    python convert_table.py ../20260522/Table.xls -o output.csv      # 指定输出
    python convert_table.py ../20260522/Table.xls --slim             # 精简版(核心列)
"""

import csv
import os
import re
import sys


# === 核心列清单（slim 模式使用） ===
CORE_COLUMNS = [
    "序号", "代码", "名称", "涨幅", "现价", "涨跌",
    "总量", "总金额", "量比", "换手率",
    "总市值", "流通市值", "细分行业",
    "5日涨幅", "10日涨幅", "20日涨幅",
    "TTM市盈率", "市净率", "每股盈利", "每股净资产",
]


def detect_encoding(filepath):
    """自动检测文件编码: gbk / gb2312 / utf-8"""
    encodings = ["gbk", "gb2312", "gb18030", "utf-8"]
    for enc in encodings:
        try:
            with open(filepath, "r", encoding=enc) as f:
                f.read(1024)
            return enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return "gbk"  # 默认回退


def clean_header(cols):
    """清洗表头：去除空格、点号、换行等杂质"""
    cleaned = []
    for c in cols:
        c = c.strip().lstrip(".")
        c = re.sub(r"\s+", "", c)
        cleaned.append(c)
    return cleaned


def convert(input_path, output_path=None, slim=False):
    """
    主转换函数
    - input_path: Table.xls 路径
    - output_path: 输出 CSV 路径（默认同目录同名 .csv）
    - slim: True=仅保留核心列
    """
    if not os.path.exists(input_path):
        print(f"[错误] 文件不存在: {input_path}")
        sys.exit(1)

    enc = detect_encoding(input_path)
    print(f"[检测] 编码: {enc}")

    with open(input_path, "r", encoding=enc) as f:
        lines = f.readlines()

    if len(lines) < 2:
        print("[错误] 文件内容不足（至少需要表头+1行数据）")
        sys.exit(1)

    # 解析表头
    header = clean_header(lines[0].rstrip("\n").split("\t"))
    print(f"[表头] {len(header)} 列")

    # slim 模式：确定要保留的列索引
    if slim:
        keep_indices = []
        keep_names = []
        for i, h in enumerate(header):
            for keyword in CORE_COLUMNS:
                if keyword == h:
                    keep_indices.append(i)
                    keep_names.append(h)
                    break
        if not keep_indices:
            print("[警告] slim 模式未匹配任何核心列，回退为全量输出")
            keep_indices = list(range(len(header)))
            keep_names = header
    else:
        keep_indices = list(range(len(header)))
        keep_names = header

    print(f"[输出] {len(keep_names)} 列" + (" (slim精简模式)" if slim else " (全量)"))

    # 确定输出路径
    if output_path is None:
        base = os.path.splitext(input_path)[0]
        suffix = "_slim" if slim else ""
        output_path = base + suffix + ".csv"

    # 转换并写入
    row_count = 0
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(keep_names)

        for line in lines[1:]:
            line = line.rstrip("\n").rstrip("\r")
            if not line.strip():
                continue  # 跳过空行
            cols = line.split("\t")
            # 选取需要保留的列
            row = [cols[i] if i < len(cols) else "" for i in keep_indices]
            writer.writerow(row)
            row_count += 1

    print(f"[完成] {row_count} 行 → {output_path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="同花顺 Table.xls → UTF-8 CSV")
    parser.add_argument("input", help="Table.xls 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出 CSV 路径（可选）")
    parser.add_argument("--slim", action="store_true", help="仅保留核心列（约20列）")
    args = parser.parse_args()

    convert(args.input, args.output, args.slim)


if __name__ == "__main__":
    main()
