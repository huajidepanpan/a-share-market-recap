#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_osmosis.py —— 渗透压模型回溯测试

用历史连续交易日数据，验证 ΔP 排名的方向预测能力：
  - 今日吸水板块 → 次日是否跑赢市场均值？
  - 今日失水板块 → 次日是否跑输市场均值？

用法:
    python backtest_osmosis.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from market_recap import read_stocks, build_osmosis_view


def validate_prediction(pred_day_dir, next_day_dir, label):
    """验证单对交易日：pred_day 的 ΔP 预测 vs next_day 实际表现"""
    pred_csv = os.path.join(pred_day_dir, "Table.csv")
    next_csv = os.path.join(next_day_dir, "Table.csv")

    if not os.path.exists(pred_csv) or not os.path.exists(next_csv):
        return None

    # Day T: 计算 ΔP
    stocks_t = read_stocks(pred_csv)
    osmosis_t, C_pool_t = build_osmosis_view(stocks_t)

    # Day T+1: 计算实际板块表现
    stocks_t1 = read_stocks(next_csv)
    # 按板块聚合 T+1 的涨幅
    from collections import defaultdict
    sector_chg_t1 = defaultdict(list)
    for s in stocks_t1:
        sector_chg_t1[s["sector"]].append(s["chg"])

    # 全市场 T+1 平均涨幅
    all_chg = [s["chg"] for s in stocks_t1]
    market_avg_chg = sum(all_chg) / len(all_chg) if all_chg else 0

    # 匹配：T日渗透压数据 → T+1 实际板块涨幅
    results = []
    for r in osmosis_t:
        sec = r["sector"]
        t1_chgs = sector_chg_t1.get(sec, [])
        if not t1_chgs:
            continue
        t1_avg = sum(t1_chgs) / len(t1_chgs)
        results.append({
            "sector": sec,
            "delta_P": r["delta_P"],
            "flow_label": r["flow_label"],
            "C_i": r["C_i"],
            "pred_avg_chg": t1_avg,   # T+1 实际板块均涨幅
            "beat_market": t1_avg > market_avg_chg,
            "n_stocks": r["count"],
            "limit_ups": r["limit_ups"],
            "avg_vr_t1": 0,  # will fill below
        })

    # 填充 T+1 的板块量比
    sector_vr_t1 = defaultdict(list)
    for s in stocks_t1:
        if s["vol_ratio"] > 0:
            sector_vr_t1[s["sector"]].append(s["vol_ratio"])
    for r in results:
        vrs = sector_vr_t1.get(r["sector"], [])
        r["avg_vr_t1"] = sum(vrs) / len(vrs) if vrs else 0

    results.sort(key=lambda x: -x["delta_P"])

    # 统计
    top10 = [r for r in results if r["delta_P"] > 0][:10]
    bot5 = [r for r in results if r["delta_P"] < 0][-5:]

    top_beat = sum(1 for r in top10 if r["beat_market"])
    bot_lose = sum(1 for r in bot5 if not r["beat_market"])
    top_avg_chg = sum(r["pred_avg_chg"] for r in top10) / len(top10) if top10 else 0
    bot_avg_chg = sum(r["pred_avg_chg"] for r in bot5) / len(bot5) if bot5 else 0

    return {
        "label": label,
        "pred_day": pred_day_dir,
        "next_day": next_day_dir,
        "C_pool": C_pool_t,
        "market_avg_t1": market_avg_chg,
        "top10_beat_rate": f"{top_beat}/{len(top10)}" if top10 else "N/A",
        "top10_beat_pct": top_beat / len(top10) * 100 if top10 else 0,
        "bot5_lose_rate": f"{bot_lose}/{len(bot5)}" if bot5 else "N/A",
        "bot5_lose_pct": bot_lose / len(bot5) * 100 if bot5 else 0,
        "top10_avg_chg": top_avg_chg,
        "bot5_avg_chg": bot_avg_chg,
        "top10": [(r["sector"], r["delta_P"], r["pred_avg_chg"], r["flow_label"], r["beat_market"]) for r in top10],
        "bot5": [(r["sector"], r["delta_P"], r["pred_avg_chg"], r["flow_label"], r["beat_market"]) for r in bot5],
        # 全量统计: 吸水端全部 vs 失水端全部的对比
        "all_absorb": [r for r in results if r["delta_P"] > 0],
        "all_lose": [r for r in results if r["delta_P"] < 0],
    }


def main():
    base = os.path.join(os.path.dirname(__file__), "..")
    pairs = [
        ("20260522", "20260525", "5/22(五) → 5/25(一)"),
        ("20260525", "20260527", "5/25(一) → 5/27(三)"),
        ("20260527", "20260528", "5/27(三) → 5/28(四)"),
    ]

    print("=" * 72)
    print("  渗透压模型回溯测试")
    print("  测试: 当日 ΔP 排名 → 次日板块实际表现")
    print("=" * 72)
    print()

    all_absorb_chgs = []
    all_lose_chgs = []
    all_top10_detail = []

    for pred, next_, label in pairs:
        pred_dir = os.path.join(base, pred)
        next_dir = os.path.join(base, next_)
        result = validate_prediction(pred_dir, next_dir, label)
        if result is None:
            continue

        print(f"【{label}】")
        print(f"  市场环境: C_pool = {result['C_pool']:.3f}, T+1 全市场均涨幅 = {result['market_avg_t1']:+.2f}%")
        print()

        # TOP10 吸水
        print(f"  ── 吸水 TOP10（T日 ΔP>0，预测 T+1 跑赢）──")
        print(f"  {'板块':<12s} {'T日ΔP':>7s} {'T+1均涨':>8s} {'流向':<6s} {'是否跑赢':>8s}")
        for sec, dp, chg, fl, beat in result["top10"]:
            beat_str = "[WIN]" if beat else ("[LOSE]" if chg <= result["market_avg_t1"] else "[-]")
            print(f"  {sec:<12s} {dp:>+7.3f} {chg:>+7.2f}% {fl:<6s} {beat_str:>8s}")
        print(f"  跑赢率: {result['top10_beat_rate']} ({result['top10_beat_pct']:.0f}%) | 平均涨幅: {result['top10_avg_chg']:+.2f}%")

        # BOT5 失水
        print()
        print(f"  ── 失水 BOT5（T日 ΔP<0，预测 T+1 跑输）──")
        print(f"  {'板块':<12s} {'T日ΔP':>7s} {'T+1均涨':>8s} {'流向':<6s} {'是否跑输':>8s}")
        for sec, dp, chg, fl, beat in result["bot5"]:
            beat_str = "[LOSE]" if not beat else ("[WIN]" if chg > result["market_avg_t1"] else "[-]")
            print(f"  {sec:<12s} {dp:>+7.3f} {chg:>+7.2f}% {fl:<6s} {beat_str:>8s}")
        print(f"  跑输率: {result['bot5_lose_rate']} ({result['bot5_lose_pct']:.0f}%) | 平均涨幅: {result['bot5_avg_chg']:+.2f}%")

        # 全量统计
        all_absorb = result["all_absorb"]
        all_lose = result["all_lose"]
        absorb_beat = sum(1 for r in all_absorb if r["beat_market"])
        lose_lose = sum(1 for r in all_lose if not r["beat_market"])
        absorb_avg = sum(r["pred_avg_chg"] for r in all_absorb) / len(all_absorb) if all_absorb else 0
        lose_avg = sum(r["pred_avg_chg"] for r in all_lose) / len(all_lose) if all_lose else 0

        print()
        print(f"  ── 全量统计 ──")
        print(f"  吸水端 ({len(all_absorb)} 板块): 跑赢率 {absorb_beat}/{len(all_absorb)} ({absorb_beat/len(all_absorb)*100:.0f}%), 平均 {absorb_avg:+.2f}%")
        print(f"  失水端 ({len(all_lose)} 板块): 跑输率 {lose_lose}/{len(all_lose)} ({lose_lose/len(all_lose)*100:.0f}%), 平均 {lose_avg:+.2f}%")
        print(f"  吸水-失水均值差: {absorb_avg - lose_avg:+.2f}%")
        print()

        all_absorb_chgs.append(absorb_avg)
        all_lose_chgs.append(lose_avg)
        all_top10_detail.append(result)

    # 汇总
    print("=" * 72)
    print("  汇总统计")
    print("=" * 72)
    print()
    for r in all_top10_detail:
        print(f"  {r['label']:30s} TOP10跑赢率={r['top10_beat_pct']:.0f}%  BOT5跑输率={r['bot5_lose_pct']:.0f}%  "
              f"吸水均值={r['top10_avg_chg']:+.2f}%  失水均值={r['bot5_avg_chg']:+.2f}%")

    if all_absorb_chgs:
        avg_absorb = sum(all_absorb_chgs) / len(all_absorb_chgs)
        avg_lose = sum(all_lose_chgs) / len(all_lose_chgs)
        print()
        print(f"  3组平均: 吸水端 {avg_absorb:+.2f}% | 失水端 {avg_lose:+.2f}% | 差值 {avg_absorb - avg_lose:+.2f}%")
        if avg_absorb > avg_lose:
            print(f"  [WIN] 模型方向正确：吸水板块在次日系统性地跑赢了失水板块")
        else:
            print(f"  [LOSE] 模型方向错误")
        print(f"  C_pool 均值: {sum(r['C_pool'] for r in all_top10_detail)/len(all_top10_detail):.3f}")


if __name__ == "__main__":
    main()
