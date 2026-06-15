#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest.py —— 渗透压模型策略回测引擎

策略: 基于板块渗透压差 ΔP 的板块轮动策略
  - 每日收盘后计算各板块的 C_i（浓度）和 ΔP（渗透压差）
  - 当板块 ΔP > entry_threshold 时，产生买入信号
  - T+1 以开盘价买入板块内代表性个股
  - 持有至信号反转（ΔP < exit_threshold）或达到最大持仓天数

用法:
    python backtest.py                           # 默认参数回测
    python backtest.py --entry 0.15 --hold 5     # ΔP>0.15买入, 持有5天
    python backtest.py --entry 0.30 --top 3      # 强吸水买入, 每板块3只
    python backtest.py --compare                 # 多参数对比回测

依赖: pandas, numpy
      （数据需先通过 download_data.py 下载）
"""

import argparse
import math
import os
import pickle
import sys
import time
import warnings
from collections import defaultdict

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ========== 配置 ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
STOCK_LIST_FILE = os.path.join(DATA_DIR, "stock_list.pkl")
SECTOR_MAP_FILE = os.path.join(DATA_DIR, "sector_map.pkl")
FLAT_TABLE_FILE = os.path.join(DATA_DIR, "flat_table.pkl")


# ====================================================================
#  渗透压模型（日K适配版）
#
#  原始模型（需要实时数据: 主力净量 + 封单额）:
#    C_i = 0.20 * s_value + 0.60 * s_momentum + 0.20 * s_seal
#    s_momentum = 0.40 * s_nm + 0.35 * s_lu + 0.25 * s_chg
#
#  适配版（仅日K数据可用）:
#    C_i = 0.25 * s_value + 0.75 * s_momentum
#    s_momentum = 0.20 * s_ret5 + 0.25 * s_lu + 0.20 * s_vr + 0.20 * s_ret20 + 0.15 * s_chg
#
#  分量说明:
#    s_value  — PE估值吸引力（sigmoid, 同原版）
#    s_ret5   — 板块5日平均涨跌幅
#    s_lu     — 涨停密度（同原版）
#    s_vr     — 量比信号（替代主力净量，反映资金参与度）
#    s_ret20  — 20日趋势强度
#    s_chg    — 当日平均涨跌幅（同原版）
# ====================================================================


def sigmoid_pe(pe_median, market_pe_median):
    """PE估值吸引力: sigmoid压缩，PE越低分越高

    当PE数据不可用时返回0.0，模型退化为纯动量策略。
    """
    if pe_median <= 0 or market_pe_median <= 0:
        return 0.0
    pe_ratio = pe_median / market_pe_median
    return 2.0 / (1.0 + math.exp(2.0 * (pe_ratio - 1.0)))


def tanh_compress(x, scale=1.0):
    """tanh压缩到 [-1, 1]"""
    return math.tanh(x * scale)


def exp_saturate(x, rate=1.0):
    """指数饱和: 1 - exp(-x * rate), 映射到 [0, 1]"""
    return 1.0 - math.exp(-max(x, 0) * rate)


# ====================================================================
#  数据加载与预处理
# ====================================================================

def classify_board(code):
    """按代码判断交易板块和涨停阈值"""
    code = code.upper()
    if "SH688" in code:
        return "科创板", 19.9
    elif "SH60" in code:
        return "上证主板", 9.9
    elif "SZ30" in code:
        return "创业板", 19.9
    elif "SZ00" in code:
        return "深证主板", 9.9
    elif code.startswith("BJ"):
        return "北交所", 29.9
    return "其他", 9.9


def load_sector_map():
    """加载个股→行业分类映射"""
    if os.path.exists(SECTOR_MAP_FILE):
        with open(SECTOR_MAP_FILE, "rb") as f:
            return pickle.load(f)

    # 如果数据还没下载，回测时使用板块级分类作为fallback
    print("  [WARN] 行业分类文件不存在，使用板块级分类（上证/深证/创业/科创）")
    return {}


def build_flat_table(all_data, sector_map):
    """构建预计算宽表: 每行 = (date, code) + 所有需要的指标

    这是性能关键——一次性计算ret5/ret20/vol_ratio，
    避免回测循环中重复遍历。
    """
    cache_file = FLAT_TABLE_FILE
    if os.path.exists(cache_file):
        print("加载预计算宽表缓存...")
        with open(cache_file, "rb") as f:
            return pickle.load(f)

    print("构建预计算宽表（首次较慢，约2-5分钟）...")

    records = []
    total = len(all_data)
    for idx, (code, df) in enumerate(all_data.items()):
        if df is None or df.empty:
            continue

        # 确保必需列存在
        for col in ["chg_pct", "open", "close", "amount", "turnover", "volume"]:
            if col not in df.columns:
                df[col] = 0.0
        if "pe" not in df.columns:
            df["pe"] = 0.0  # PE缺失，模型自动退化为纯动量

        df = df.sort_values("date").reset_index(drop=True)
        n = len(df)

        # 向量化计算 ret5, ret20, vol_ratio
        chg_arr = df["chg_pct"].fillna(0).values
        vol_arr = df["volume"].fillna(0).values

        # ret5: 前5日累计涨跌幅（含当日）
        ret5_arr = np.zeros(n)
        for i in range(n):
            start = max(0, i - 4)
            ret5_arr[i] = np.sum(chg_arr[start:i+1])

        # ret20: 前20日累计涨跌幅
        ret20_arr = np.zeros(n)
        for i in range(n):
            start = max(0, i - 19)
            ret20_arr[i] = np.sum(chg_arr[start:i+1])

        # vol_ratio: 当日量 / 过去5日均量（不含当日）
        vol_ratio_arr = np.ones(n)
        for i in range(5, n):
            avg_vol = np.mean(vol_arr[i-5:i])
            if avg_vol > 0:
                vol_ratio_arr[i] = min(vol_arr[i] / avg_vol, 5.0)
        for i in range(1, min(5, n)):
            avg_vol = np.mean(vol_arr[:i])
            if avg_vol > 0:
                vol_ratio_arr[i] = min(vol_arr[i] / avg_vol, 5.0)

        board, limit = classify_board(code)
        sector = sector_map.get(code, board)  # 无行业映射时用板块兜底

        for i in range(n):
            records.append({
                "date": df["date"].iloc[i],
                "code": code,
                "chg_pct": float(chg_arr[i]),
                "open": float(df["open"].iloc[i]),
                "close": float(df["close"].iloc[i]),
                "amount": float(df["amount"].iloc[i]),
                "turnover": float(df["turnover"].iloc[i]),
                "volume": float(vol_arr[i]),
                "pe": float(df["pe"].iloc[i]) if "pe" in df.columns else 0.0,
                "ret5": float(ret5_arr[i]),
                "ret20": float(ret20_arr[i]),
                "vol_ratio": float(vol_ratio_arr[i]),
                "board": board,
                "limit": limit,
                "sector": sector,
            })

        if (idx + 1) % 1000 == 0:
            print(f"  进度: {idx+1}/{total} 只股票")

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])

    # 缓存
    with open(cache_file, "wb") as f:
        pickle.dump(df, f, protocol=pickle.HIGHEST_PROTOCOL)

    print(f"  宽表构建完成: {len(df)} 行, {df['code'].nunique()} 只股票, "
          f"{df['date'].nunique()} 个交易日")
    return df


# ====================================================================
#  板块渗透压计算（每日）
# ====================================================================

def calc_sector_Ci(sector_df, market_pe_median, min_stocks=5):
    """计算某板块在某日的渗透压浓度 C_i

    sector_df: 该板块该日所有股票的 DataFrame 切片
    """
    n = len(sector_df)
    if n < min_stocks:
        return None

    # PE中位数 & s_value
    pe_list = sorted(sector_df.loc[sector_df["pe"] > 0, "pe"])
    pe_median = pe_list[len(pe_list) // 2] if pe_list else 0
    s_value = sigmoid_pe(pe_median, market_pe_median)

    # 涨停密度
    limit_up_mask = (sector_df["chg_pct"] >= sector_df["limit"]) & (sector_df["chg_pct"].abs() < 30)
    lu_density = limit_up_mask.sum() / n
    s_lu = exp_saturate(lu_density, 15)

    # 当日平均涨跌幅
    avg_chg = sector_df["chg_pct"].mean()
    s_chg = tanh_compress(avg_chg, 1.0 / 3)

    # 5日平均涨跌幅
    avg_ret5 = sector_df["ret5"].mean()
    s_ret5 = tanh_compress(avg_ret5, 1.0 / 5)

    # 20日趋势
    avg_ret20 = sector_df["ret20"].mean()
    s_ret20 = tanh_compress(avg_ret20, 1.0 / 15)

    # 量比信号
    avg_vr = sector_df["vol_ratio"].mean()
    s_vr = tanh_compress(avg_vr - 1.0, 2.0)

    # 平均换手率
    avg_turnover = sector_df["turnover"].mean()

    # 合成浓度 C_i
    # 当PE数据不可用(s_value==0)时，自动退化为纯动量模型
    s_momentum = 0.20 * s_ret5 + 0.25 * s_lu + 0.20 * s_vr + 0.20 * s_ret20 + 0.15 * s_chg
    if pe_median > 0 and market_pe_median > 0:
        C_i = 0.25 * s_value + 0.75 * s_momentum
    else:
        C_i = s_momentum  # 无PE数据，纯动量驱动

    return {
        "n": n,
        "C_i": C_i,
        "s_value": s_value,
        "s_momentum": s_momentum,
        "avg_chg": avg_chg,
        "avg_vr": avg_vr,
        "avg_turnover": avg_turnover,
        "limit_up_count": limit_up_mask.sum(),
        "total_amount": sector_df["amount"].sum(),
        # 保留个股明细供后续选股
        "stocks": sector_df[["code", "open", "close", "chg_pct",
                              "amount", "turnover", "limit"]].to_dict("records"),
        "pe_median": pe_median,
    }


def get_stock_name_map():
    """获取代码→名称映射"""
    if os.path.exists(STOCK_LIST_FILE):
        with open(STOCK_LIST_FILE, "rb") as f:
            sl = pickle.load(f)
        return dict(zip(sl["code_full"], sl["name"]))
    return {}


# ====================================================================
#  回测引擎
# ====================================================================

class BacktestEngine:
    """渗透压策略回测引擎"""

    def __init__(self, entry_threshold=0.15, exit_threshold=0.0,
                 max_hold_days=5, top_n_stocks=3, min_sector_stocks=5,
                 max_sectors=5, capital=100000.0,
                 commission=0.0003, stamp_tax=0.001):
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.max_hold_days = max_hold_days
        self.top_n_stocks = top_n_stocks
        self.min_sector_stocks = min_sector_stocks
        self.max_sectors = max_sectors
        self.capital = capital
        self.commission = commission
        self.stamp_tax = stamp_tax

        self.cash = capital
        self.holdings = {}
        self.trades = []
        self.daily_values = []
        self.pending_buys = []  # T日信号 → T+1日开盘执行

    def run(self, flat_table, all_dates, name_map):
        """执行回测

        T+1 建模:
          - T日收盘后: 计算板块渗透压 → 生成买卖信号
          - T+1日开盘: 执行 T 日的买卖信号（以 T+1 日开盘价成交）
          - T日净值: 以 T 日收盘价计算持仓市值

        flat_table: 预计算宽表 DataFrame
        all_dates : 排序后的交易日列表
        """
        params_str = (f"entry={self.entry_threshold}, exit={self.exit_threshold}, "
                      f"hold={self.max_hold_days}d, topN={self.top_n_stocks}")
        print(f"\n{'='*60}")
        print(f"回测参数: {params_str}")
        print(f"初始资金: {self.capital:,.0f}")
        print(f"{'='*60}")

        t_start = time.time()
        n_dates = len(all_dates)
        pending_buy_signals = []  # [(sector, selected_stocks, delta_P)]
        pending_sell_codes = []   # [code, ...]

        for i, date in enumerate(all_dates):
            day_data = flat_table[flat_table["date"] == date]
            if len(day_data) < 100:
                continue

            # === Phase 1: 执行 T-1 日的挂单（T 日开盘价成交） ===
            # 先卖后买（释放现金）
            for code in pending_sell_codes:
                if code in self.holdings:
                    self._execute_sell(code, date, day_data)
            pending_sell_codes.clear()

            current_sectors = set(p["sector"] for p in self.holdings.values())
            for sec, selected, dp in pending_buy_signals:
                if sec in current_sectors:
                    continue  # 已持仓板块不再加仓
                self._execute_buys(selected, date, day_data, sec, dp, name_map)
            pending_buy_signals.clear()

            # === Phase 2: 基于 T 日数据计算板块渗透压 ===
            market_pe = day_data.loc[day_data["pe"] > 0, "pe"].median()
            if pd.isna(market_pe) or market_pe <= 0:
                market_pe = 50

            sectors = day_data.groupby("sector")
            sector_metrics = {}
            for sec, sec_df in sectors:
                if len(sec_df) < self.min_sector_stocks:
                    continue
                m = calc_sector_Ci(sec_df, market_pe, self.min_sector_stocks)
                if m is None:
                    continue
                sector_metrics[sec] = m

            total_amt = sum(m["total_amount"] for m in sector_metrics.values())
            if total_amt > 0:
                C_pool = sum(m["C_i"] * m["total_amount"] / total_amt
                            for m in sector_metrics.values())
            else:
                C_pool = 0

            for sec, m in sector_metrics.items():
                m["delta_P"] = m["C_i"] - C_pool

            # === Phase 3: 生成 T 日信号 → T+1 执行 ===

            # 卖出信号: 检查持仓
            for code, pos in list(self.holdings.items()):
                hold_days = (date - pos["buy_date"]).days
                sec = pos["sector"]
                dp = sector_metrics.get(sec, {}).get("delta_P", -99)
                if dp < self.exit_threshold or hold_days >= self.max_hold_days:
                    pending_sell_codes.append(code)

            # 买入信号: 板块 ΔP > 阈值
            ranked = sorted(sector_metrics.items(), key=lambda x: -x[1]["delta_P"])
            slots = self.max_sectors - len(current_sectors)
            buy_count = 0

            for sec, m in ranked:
                if buy_count >= slots:
                    break
                if m["delta_P"] <= self.entry_threshold:
                    break
                if sec in current_sectors:
                    continue

                candidates = sorted(m["stocks"], key=lambda s: -s["amount"])
                selected = []
                for s in candidates:
                    sname = name_map.get(s["code"], "")
                    if "ST" in str(sname).upper():
                        continue
                    if any(b_s["code"] == s["code"] for _, b_stocks, _ in pending_buy_signals for b_s in b_stocks):
                        continue
                    if s["turnover"] < 0.5 or s["amount"] < 1e7:
                        continue
                    if s["chg_pct"] >= s["limit"]:
                        continue
                    selected.append(s)
                    if len(selected) >= self.top_n_stocks:
                        break

                if selected:
                    pending_buy_signals.append((sec, selected, m["delta_P"]))
                    buy_count += 1

            # === Phase 4: 记录 T 日收盘净值 ===
            total_value = self.cash
            for code, pos in self.holdings.items():
                row = day_data[day_data["code"] == code]
                if not row.empty:
                    total_value += pos["shares"] * float(row.iloc[0]["close"])
                else:
                    total_value += pos["shares"] * pos["buy_price"]

            self.daily_values.append({
                "date": date, "value": total_value, "cash": self.cash,
                "holdings": len(self.holdings),
            })

            if (i + 1) % 50 == 0:
                ret = (total_value / self.capital - 1) * 100
                print(f"  [{i+1}/{n_dates}] 净值: {total_value:,.0f}  "
                      f"收益: {ret:+.2f}%  持仓: {len(self.holdings)}只")

        # 最后一个交易日: 执行残余挂单 + 强制清仓
        last_date = all_dates[-1]
        last_day = flat_table[flat_table["date"] == last_date]

        for code in pending_sell_codes:
            if code in self.holdings:
                self._execute_sell(code, last_date, last_day)
        pending_sell_codes.clear()

        for sec, selected, dp in pending_buy_signals:
            self._execute_buys(selected, last_date, last_day, sec, dp, name_map)
        pending_buy_signals.clear()

        for code in list(self.holdings.keys()):
            self._sell_final(code, last_date, last_day)

        elapsed = time.time() - t_start
        final_val = self.daily_values[-1]["value"] if self.daily_values else self.capital
        ret = (final_val / self.capital - 1) * 100
        print(f"\n回测完成! 耗时 {elapsed:.0f}s  最终净值: {final_val:,.0f}  "
              f"总收益: {ret:+.2f}%")
        return self._calc_metrics()

    def _execute_buys(self, selected, date, day_data, sector, delta_P, name_map):
        """执行买入: T+1 日以当日开盘价成交"""
        cash_per_sec = self.cash * 0.20
        cash_per_stock = cash_per_sec / len(selected)

        for s in selected:
            buy_price = s["open"]  # T+1 日开盘价
            if buy_price <= 0:
                continue
            shares = int(cash_per_stock / (buy_price * (1 + self.commission)))
            shares = shares // 100 * 100
            if shares < 100:
                continue
            cost = shares * buy_price * (1 + self.commission)
            if cost > self.cash:
                shares = int(self.cash * 0.9 / (buy_price * (1 + self.commission)))
                shares = shares // 100 * 100
                if shares < 100:
                    continue
                cost = shares * buy_price * (1 + self.commission)

            self.cash -= cost
            self.holdings[s["code"]] = {
                "shares": shares, "buy_price": buy_price,
                "buy_date": date, "sector": sector, "cost": cost,
            }
            self.trades.append({
                "date": date, "code": s["code"], "action": "BUY",
                "price": buy_price, "shares": shares,
                "sector": sector, "delta_P": delta_P,
            })

    def _execute_sell(self, code, date, day_data):
        """执行卖出: T+1 日以当日开盘价成交（非最终清仓）"""
        pos = self.holdings.pop(code)
        row = day_data[day_data["code"] == code]
        sell_price = float(row.iloc[0]["open"]) if not row.empty else pos["buy_price"]
        if sell_price <= 0:
            sell_price = pos["buy_price"]

        proceeds = pos["shares"] * sell_price * (1 - self.commission - self.stamp_tax)
        self.cash += proceeds
        self.trades.append({
            "date": date, "code": code, "action": "SELL",
            "price": sell_price, "shares": pos["shares"],
            "sector": pos["sector"], "buy_price": pos["buy_price"],
            "hold_days": (date - pos["buy_date"]).days,
            "pnl": proceeds - pos["cost"],
            "pnl_pct": (sell_price / pos["buy_price"] - 1) * 100 if pos["buy_price"] > 0 else 0,
        })

    def _sell_final(self, code, date, day_data):
        """最终清仓: 以当日收盘价卖出"""
        pos = self.holdings.pop(code)
        row = day_data[day_data["code"] == code]
        if not row.empty:
            sell_price = float(row.iloc[0]["close"])
        else:
            sell_price = pos["buy_price"]
        if sell_price <= 0:
            sell_price = pos["buy_price"]

        proceeds = pos["shares"] * sell_price * (1 - self.commission - self.stamp_tax)
        self.cash += proceeds
        self.trades.append({
            "date": date, "code": code, "action": "SELL",
            "price": sell_price, "shares": pos["shares"],
            "sector": pos["sector"], "buy_price": pos["buy_price"],
            "hold_days": (date - pos["buy_date"]).days,
            "pnl": proceeds - pos["cost"],
            "pnl_pct": (sell_price / pos["buy_price"] - 1) * 100 if pos["buy_price"] > 0 else 0,
        })

    def _calc_metrics(self):
        """计算绩效指标"""
        if not self.daily_values:
            return {}

        df = pd.DataFrame(self.daily_values)
        df["ret"] = df["value"].pct_change()
        df["nav"] = df["value"] / self.capital

        total_return = (df["value"].iloc[-1] / self.capital - 1) * 100
        n = len(df)
        annual_return = (df["nav"].iloc[-1] ** (252 / n) - 1) * 100 if n > 1 else 0

        cummax = df["value"].cummax()
        max_dd = ((df["value"] - cummax) / cummax * 100).min()

        sharpe = 0.0
        if n > 1 and df["ret"].std() > 0:
            sharpe = (df["ret"].mean() / df["ret"].std()) * math.sqrt(252)

        sell_trades = [t for t in self.trades if t["action"] == "SELL"]
        wins = sum(1 for t in sell_trades if t["pnl"] > 0)
        win_rate = wins / len(sell_trades) * 100 if sell_trades else 0
        avg_hold = (sum(t["hold_days"] for t in sell_trades) /
                    len(sell_trades)) if sell_trades else 0

        sector_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "total_pnl": 0.0})
        for t in sell_trades:
            s = sector_stats[t["sector"]]
            s["trades"] += 1
            if t["pnl"] > 0:
                s["wins"] += 1
            s["total_pnl"] += t["pnl"]

        return {
            "total_return": total_return,
            "annual_return": annual_return,
            "sharpe": sharpe,
            "max_drawdown": max_dd,
            "win_rate": win_rate,
            "total_trades": len(sell_trades),
            "avg_hold_days": avg_hold,
            "n_days": n,
            "sector_stats": dict(sector_stats),
            "final_value": df["value"].iloc[-1],
            "nav_series": df,
        }


# ====================================================================
#  报告输出
# ====================================================================

def print_report(metrics, params):
    """打印回测报告"""
    print(f"\n{'='*60}")
    print(f"  回测报告")
    print(f"{'='*60}")
    print(f"  参数: entry ΔP > {params['entry']}, exit ΔP < {params['exit']}, "
          f"持仓 {params['hold']}天, Top{params['top_n']}")
    print(f"  交易日数:       {metrics.get('n_days', 0)}")
    print(f"  总收益:         {metrics.get('total_return', 0):+.2f}%")
    print(f"  年化收益:       {metrics.get('annual_return', 0):+.2f}%")
    print(f"  Sharpe比率:     {metrics.get('sharpe', 0):.3f}")
    print(f"  最大回撤:       {metrics.get('max_drawdown', 0):+.2f}%")
    print(f"  交易次数:       {metrics.get('total_trades', 0)}")
    print(f"  胜率:           {metrics.get('win_rate', 0):.1f}%")
    print(f"  平均持仓天数:   {metrics.get('avg_hold_days', 0):.1f}")

    ss = metrics.get("sector_stats", {})
    if ss:
        print(f"\n  ── 板块表现 ──")
        for sec, s in sorted(ss.items(), key=lambda x: -x[1]["total_pnl"])[:10]:
            wr = s["wins"] / s["trades"] * 100 if s["trades"] else 0
            print(f"  {sec:<14s} {s['trades']:>3d}次  "
                  f"胜{s['wins']:>3d}({wr:>5.1f}%)  "
                  f"盈亏 {s['total_pnl']:>+12,.0f}")
    print(f"{'='*60}")


def run_comparison(flat_table, all_dates, name_map):
    """多参数对比回测"""
    configs = [
        (0.10, 0.0, 3, 3, "保守-短持"),
        (0.10, 0.0, 5, 3, "保守-中持"),
        (0.15, 0.0, 3, 3, "均衡-短持"),
        (0.15, 0.0, 5, 3, "均衡-中持"),
        (0.15, 0.0, 5, 5, "均衡-分散"),
        (0.20, 0.0, 5, 3, "偏强-中持"),
        (0.30, 0.0, 3, 3, "强吸水-短持"),
        (0.30, 0.0, 5, 2, "强吸水-集中"),
    ]

    print(f"\n{'='*60}")
    print(f"  多参数对比回测（{len(configs)} 组参数）")
    print(f"{'='*60}")

    results = []
    for entry, exit_, hold, top_n, label in configs:
        engine = BacktestEngine(
            entry_threshold=entry, exit_threshold=exit_,
            max_hold_days=hold, top_n_stocks=top_n,
        )
        engine.run(flat_table, all_dates, name_map)
        results.append((label, engine._calc_metrics()))

    print(f"\n{'='*85}")
    print(f"  {'配置':<14s} {'总收益':>8s} {'年化':>8s} {'Sharpe':>7s} "
          f"{'最大回撤':>8s} {'胜率':>7s} {'交易':>5s} {'均持':>5s}")
    print(f"{'='*85}")
    for label, m in results:
        print(f"  {label:<14s} {m['total_return']:>+7.2f}% {m['annual_return']:>+7.2f}% "
              f"{m['sharpe']:>7.3f} {m['max_drawdown']:>+7.2f}% "
              f"{m['win_rate']:>6.1f}% {m['total_trades']:>4d} "
              f"{m['avg_hold_days']:>4.1f}d")
    print(f"{'='*85}")

    best = max(results, key=lambda x: x[1]["sharpe"])
    print(f"\n最佳配置(Sharpe): {best[0]}  "
          f"Sharpe={best[1]['sharpe']:.3f}  收益={best[1]['total_return']:+.2f}%")


# ====================================================================
#  主入口
# ====================================================================

def main():
    parser = argparse.ArgumentParser(description="渗透压模型策略回测")
    parser.add_argument("--entry", type=float, default=0.15)
    parser.add_argument("--exit", type=float, default=0.0)
    parser.add_argument("--hold", type=int, default=5)
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--capital", type=float, default=100000.0)
    parser.add_argument("--compare", action="store_true", help="多参数对比回测")
    parser.add_argument("--rebuild", action="store_true", help="强制重建宽表缓存")
    args = parser.parse_args()

    # 清理缓存
    if args.rebuild and os.path.exists(FLAT_TABLE_FILE):
        os.remove(FLAT_TABLE_FILE)
        print("已删除宽表缓存，将重新构建")

    # 加载股票列表
    if not os.path.exists(STOCK_LIST_FILE):
        print(f"[ERROR] 找不到 {STOCK_LIST_FILE}")
        print("请先运行: python download_data.py")
        sys.exit(1)

    with open(STOCK_LIST_FILE, "rb") as f:
        stock_list = pickle.load(f)
    print(f"股票列表: {len(stock_list)} 只")

    # 加载日K数据
    print("加载日K数据...")
    all_data = {}
    for _, row in stock_list.iterrows():
        code = row["code_full"]
        fp = os.path.join(DATA_DIR, f"{code}.pkl")
        if os.path.exists(fp):
            with open(fp, "rb") as f:
                df = pickle.load(f)
            if df is not None and not df.empty:
                df["code"] = code
                all_data[code] = df
    print(f"  加载 {len(all_data)} 只股票的日K数据")

    # 加载行业分类
    sector_map = load_sector_map()
    if sector_map:
        print(f"行业分类: {len(set(sector_map.values()))} 个行业")
    else:
        print("行业分类: 使用板块级（上证/深证/创业/科创）")

    # 构建预计算宽表
    flat_table = build_flat_table(all_data, sector_map)
    all_dates = sorted(flat_table["date"].unique())
    print(f"交易日范围: {all_dates[0].date()} ~ {all_dates[-1].date()}  "
          f"共 {len(all_dates)} 天")

    # 股票名称映射（用于ST过滤）
    name_map = get_stock_name_map()

    # 执行回测
    if args.compare:
        run_comparison(flat_table, all_dates, name_map)
    else:
        engine = BacktestEngine(
            entry_threshold=args.entry, exit_threshold=args.exit,
            max_hold_days=args.hold, top_n_stocks=args.top,
            capital=args.capital,
        )
        engine.run(flat_table, all_dates, name_map)
        metrics = engine._calc_metrics()
        print_report(metrics, {
            "entry": args.entry, "exit": args.exit,
            "hold": args.hold, "top_n": args.top,
        })

        if metrics.get("nav_series") is not None:
            nav_file = os.path.join(BASE_DIR, "nav_curve.csv")
            metrics["nav_series"].to_csv(nav_file, index=False, encoding="utf-8-sig")
            print(f"净值曲线已导出: {nav_file}")


if __name__ == "__main__":
    main()
