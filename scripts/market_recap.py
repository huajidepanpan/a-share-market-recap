#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
market_recap.py —— 分板块市场复盘报告生成器

输出两个文件:
  - recap          文本版（结构清晰，快速扫读）
  - recap.html     可视化版（柱状图/热力图/连板梯队，浏览器打开）

用法:
    python market_recap.py ../20260522/Table.csv
    python market_recap.py ../20260522/Table.csv -o ../20260522/recap
"""

import csv
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime


# ========== 工具函数 ==========

def classify_board(code):
    code = code.strip().upper()
    if "SH688" in code:
        return "科创板", 19.9, -19.9
    elif "SH60" in code:
        return "上证主板", 9.9, -9.9
    elif "SZ30" in code:
        return "创业板", 19.9, -19.9
    elif "SZ00" in code:
        return "深证主板", 9.9, -9.9
    elif code[:1] in "84":
        return "北交所", 29.9, -29.9
    return "其他", 9.9, -9.9


def is_st(name):
    return bool(name and ("ST" in name.upper() or "*ST" in name.upper()))


def safe_float(s, default=0.0):
    if s is None:
        return default
    s = str(s).replace("%", "").replace("+", "").replace(",", "").replace("万", "e4").replace("亿", "e8")
    try:
        return float(s) if s and s != "--" else default
    except ValueError:
        return default


def estimate_streak(chg5, board_limit):
    """根据5日涨幅估算连板数"""
    if chg5 <= 0 or board_limit <= 0:
        return 0
    try:
        n = round(math.log(1 + chg5 / 100) / math.log(1 + board_limit / 100))
    except (ValueError, ZeroDivisionError):
        n = 0
    return n if n > 0 else 0


def grade_strength(chg, board):
    limits = {
        "上证主板": (9.9, 7, 5, 3), "深证主板": (9.9, 7, 5, 3),
        "创业板": (19.9, 14, 10, 5), "科创板": (19.9, 14, 10, 5),
        "北交所": (29.9, 20, 15, 8), "ST板块": (4.9, 3, 2, 1),
        "其他": (9.9, 7, 5, 3),
    }
    up, s, ms, m = limits.get(board, (9.9, 7, 5, 3))
    if chg >= up:
        return "涨停"
    elif chg >= s:
        return "强势"
    elif chg >= ms:
        return "偏强"
    elif chg >= m:
        return "温和"
    return "-"


# ========== 数据读取 ==========

def read_stocks(csv_path):
    stocks = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("代码") or "").strip()
            name = (row.get("名称") or "").strip()
            if not code or not name:
                continue

            chg = safe_float(row.get("涨幅"))
            amt = safe_float(row.get("总金额"))
            mcap = safe_float(row.get("总市值"))
            turnover = safe_float(row.get("换手"))
            chg5 = safe_float(row.get("5日涨幅"))
            chg10 = safe_float(row.get("10日涨幅"))
            chg20 = safe_float(row.get("20日涨幅"))
            pe = safe_float(row.get("TTM市盈率"))
            pb = safe_float(row.get("市净率"))
            sector = (row.get("细分行业") or "").strip()
            seal_amt = safe_float(row.get("封单额_新"))  # 封单额
            seal_vol = safe_float(row.get("封单量_新"))  # 封单量
            vol = safe_float(row.get("总手"))
            open_chg = safe_float(row.get("开盘涨幅"))
            remark = (row.get("备注") or "").strip()

            board, up_lim, down_lim = classify_board(code)
            if is_st(name):
                board = "ST板块"
                up_lim, down_lim = 4.9, -4.9

            # 估算连板数
            streak = estimate_streak(chg5, up_lim) if chg >= up_lim else 0

            stocks.append({
                "code": code, "name": name, "chg": chg, "amt": amt,
                "mcap": mcap, "vol": vol, "turnover": turnover,
                "chg5": chg5, "chg10": chg10, "chg20": chg20,
                "pe": pe, "pb": pb, "sector": sector if sector and sector != "--" else "未分类",
                "board": board, "up_limit": up_lim, "down_limit": down_lim,
                "seal_amt": seal_amt, "seal_vol": seal_vol,
                "open_chg": open_chg, "remark": remark, "streak": streak,
            })
    return stocks


# ========== 分析函数 ==========

def build_sector_heat(stocks):
    """涨停板块热力图: {sector: {count, amt, leaders[2]}}"""
    limit_ups = [s for s in stocks if s["chg"] >= s["up_limit"] and abs(s["chg"]) < 30]
    heat = defaultdict(lambda: {"count": 0, "amt": 0.0, "leaders": []})
    for s in limit_ups:
        h = heat[s["sector"]]
        h["count"] += 1
        h["amt"] += s["amt"]
        h["leaders"].append(s["name"])
    # 排序取 TOP 10
    return sorted(heat.items(), key=lambda x: x[1]["count"], reverse=True)[:10]


def build_streak_ladder(stocks):
    """连板梯队: {streak: [stocks]}"""
    limit_ups = [s for s in stocks if s["streak"] >= 2 and s["chg"] >= s["up_limit"]]
    ladder = defaultdict(list)
    for s in limit_ups:
        ladder[s["streak"]].append(s)
    return dict(sorted(ladder.items(), reverse=True))


def build_divergence_pool(stocks):
    """分歧/炸板池: 接近涨停但未封板 + 高换手 (>30%)"""
    pool = []
    for s in stocks:
        if abs(s["chg"]) >= 30:
            continue  # 跳过新股
        gap = s["up_limit"] - s["chg"]
        # 距涨停 2% 以内，或换手率极高但没封住
        near_limit = (0 < gap <= 2 and s["chg"] > 0)
        high_turnover_near = (s["turnover"] > 30 and s["chg"] >= s["up_limit"] * 0.5)
        if near_limit or high_turnover_near:
            pool.append(s)
    return sorted(pool, key=lambda s: (-s["chg"], -s["turnover"]))[:20]


def build_sector_bubbles(stocks):
    """板块泡泡图数据: 按细分行业聚合，取市值最大的15个板块"""
    sectors = defaultdict(lambda: {"stocks": [], "limit_ups": 0, "rising": 0})
    for s in stocks:
        sec = s["sector"]
        sectors[sec]["stocks"].append(s)
        if s["chg"] >= s["up_limit"] and abs(s["chg"]) < 30:
            sectors[sec]["limit_ups"] += 1
        if s["chg"] > 0:
            sectors[sec]["rising"] += 1

    result = []
    for sec, data in sectors.items():
        ss = data["stocks"]
        n = len(ss)
        if n < 3:
            continue
        avg_chg = sum(s["chg"] for s in ss) / n
        total_mcap = sum(s["mcap"] for s in ss)
        avg_turnover = sum(s["turnover"] for s in ss) / n
        leaders = sorted(ss, key=lambda x: -x["chg"])[:3]
        result.append({
            "sector": sec,
            "count": n,
            "limit_ups": data["limit_ups"],
            "rising": data["rising"],
            "up_ratio": round(data["rising"] / n * 100, 1),
            "avg_chg": round(avg_chg, 2),
            "total_mcap": total_mcap,
            "avg_turnover": round(avg_turnover, 2),
            "leaders": [l["name"] for l in leaders],
        })

    # 取市值最大的15个板块，额外取涨停最多的5个（合并去重）
    result.sort(key=lambda x: -x["total_mcap"])
    top15 = result[:15]
    # 补充涨停家数多但市值不在前15的板块
    by_limit = sorted(result, key=lambda x: -x["limit_ups"])
    existing = {r["sector"] for r in top15}
    for r in by_limit:
        if r["sector"] not in existing and r["limit_ups"] >= 2:
            top15.append(r)
            existing.add(r["sector"])
        if len(top15) >= 20:
            break
    return top15


# ========== 文本报告 ==========

def generate_text(stocks, output_path):
    lines = []
    def w(s=""): lines.append(s)

    total = len(stocks)
    up_s = [s for s in stocks if s["chg"] > 0]
    down_s = [s for s in stocks if s["chg"] < 0]
    flat_s = [s for s in stocks if s["chg"] == 0]
    total_amt = sum(s["amt"] for s in stocks)
    sorted_chg = sorted([s["chg"] for s in stocks])
    limit_ups = [s for s in stocks if s["chg"] >= s["up_limit"] and abs(s["chg"]) < 30]
    limit_downs = [s for s in stocks if s["chg"] <= s["down_limit"]]

    w("=" * 70)
    w("  A股市场复盘报告")
    w(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    w("=" * 70)
    w()
    w("【一、大盘概况】")
    w(f"  股票: {total} 只 | 成交: {total_amt/1e8:,.0f} 亿")
    w(f"  上涨: {len(up_s)} ({len(up_s)/total*100:.1f}%)  "
      f"下跌: {len(down_s)} ({len(down_s)/total*100:.1f}%)  "
      f"平盘: {len(flat_s)}")
    w(f"  涨跌比: {len(up_s)}:{len(down_s)}  |  "
      f"涨停: {len(limit_ups)}  |  跌停: {len(limit_downs)}")
    w(f"  均涨幅: {sum(sorted_chg)/total:+.2f}%  |  中位数: {sorted_chg[total//2]:+.2f}%")
    w()

    # 板块概览
    w("【二、分板块概览】")
    w(f"  {'板块':<8s} {'家数':>5s} {'上涨':>5s} {'下跌':>5s} {'涨停':>4s} {'跌停':>4s} "
      f"{'均涨':>7s} {'成交(亿)':>8s} {'占比':>6s}")
    boards = defaultdict(lambda: {"s": [], "lu": 0, "ld": 0})
    for s in stocks:
        boards[s["board"]]["s"].append(s)
        if s["chg"] >= s["up_limit"] and abs(s["chg"]) < 30:
            boards[s["board"]]["lu"] += 1
        if s["chg"] <= s["down_limit"]:
            boards[s["board"]]["ld"] += 1
    for bn in ["上证主板", "深证主板", "创业板", "科创板", "北交所", "ST板块", "其他"]:
        b = boards.get(bn)
        if not b or not b["s"]:
            continue
        ss = b["s"]
        n = len(ss)
        u = sum(1 for x in ss if x["chg"] > 0)
        d = sum(1 for x in ss if x["chg"] < 0)
        avg = sum(x["chg"] for x in ss) / n
        amt = sum(x["amt"] for x in ss)
        pct = amt / total_amt * 100 if total_amt > 0 else 0
        w(f"  {bn:<8s} {n:>5d} {u:>5d} {d:>5d} {b['lu']:>4d} {b['ld']:>4d} "
          f"{avg:>+6.2f}% {amt/1e8:>8.1f} {pct:>5.1f}%")
    w()

    # === 新模块: 涨停板块热力图 ===
    sector_heat = build_sector_heat(stocks)
    w("【三、涨停板块热力图】（动态聚合，今日TOP10）")
    max_cnt = max(h["count"] for _, h in sector_heat) if sector_heat else 1
    for sec, h in sector_heat:
        bar = "█" * int(30 * h["count"] / max_cnt)
        leaders = "、".join(h["leaders"][:3])
        w(f"  {sec:<12s} {bar}  {h['count']:>2d}家涨停  成交{h['amt']/1e8:6.0f}亿  代表: {leaders}")
    w()

    # === 新模块: 连板梯队 ===
    ladder = build_streak_ladder(stocks)
    w("【四、连板梯队】")
    if ladder:
        for streak, ss in ladder.items():
            names = ", ".join(f"{s['name']}({s['board'][:2]})" for s in ss[:10])
            more = f" ...等{len(ss)}家" if len(ss) > 10 else ""
            w(f"  {streak}连板 ({len(ss)}家): {names}{more}")
    else:
        w("  (无2连板以上个股)")
    w()

    # === 新模块: 分歧/炸板池 ===
    divergence = build_divergence_pool(stocks)
    w("【五、分歧观察池】（近涨停未封 + 高换手，前瞻信号）")
    w(f"  {'名称':<8s} {'代码':<10s} {'板块':<8s} {'涨幅':>7s} {'换手':>6s} {'成交(亿)':>8s}")
    for s in divergence[:15]:
        w(f"  {s['name']:<8s} {s['code']:<10s} {s['board']:<8s} "
          f"{s['chg']:>+6.2f}% {s['turnover']:>5.1f}% {s['amt']/1e8:>8.1f}")
    w()

    # 成交额 TOP20
    by_amt = sorted(stocks, key=lambda x: x["amt"], reverse=True)
    w("【六、成交额 TOP20】")
    for i, s in enumerate(by_amt[:20]):
        w(f"  {i+1:2d}. {s['name']:<8s} {s['code']:<10s} {s['board']:<6s} "
          f"{s['chg']:>+7.2f}%  成交{s['amt']/1e8:>8.1f}亿  换手{s['turnover']:>5.1f}%")
    w()

    # 5日涨跌
    valid5 = [s for s in stocks if s["chg5"] != 0]
    valid5.sort(key=lambda x: x["chg5"], reverse=True)
    w("【七、5日涨幅 TOP15】")
    for i, s in enumerate(valid5[:15]):
        w(f"  {i+1:2d}. {s['name']:<8s} 5日{s['chg5']:>+7.2f}%  今{s['chg']:>+7.2f}%  {s['board']}")
    valid5.sort(key=lambda x: x["chg5"])
    w()
    w("【七-B、5日跌幅 TOP15】")
    for i, s in enumerate(valid5[:15]):
        w(f"  {i+1:2d}. {s['name']:<8s} 5日{s['chg5']:>+7.2f}%  今{s['chg']:>+7.2f}%  {s['board']}")
    w()

    # 市值 TOP10
    w("【八、总市值 TOP10】")
    for i, s in enumerate(sorted(stocks, key=lambda x: x["mcap"], reverse=True)[:10]):
        w(f"  {i+1:2d}. {s['name']:<8s} 市值{s['mcap']/1e8:>10,.0f}亿  {s['chg']:>+6.2f}%")
    w()

    # 总结
    up_ratio = len(up_s) / total if total > 0 else 0
    mood = "极度亢奋" if up_ratio > 0.75 else ("偏强" if up_ratio > 0.6 else ("分化" if up_ratio > 0.45 else "偏弱"))
    best_sec = sector_heat[0] if sector_heat else None
    top_streak = max(ladder.keys()) if ladder else 0

    w("=" * 70)
    w("【市场总结】")
    w(f"  情绪: {mood}（涨跌比 {len(up_s)}:{len(down_s)}）")
    w(f"  涨停 {len(limit_ups)} 家 | 跌停 {len(limit_downs)} 家 | 成交 {total_amt/1e8:,.0f} 亿")
    if top_streak:
        w(f"  最高板: {top_streak}连板")
    if best_sec:
        w(f"  主线板块: {best_sec[0]}（{best_sec[1]['count']}家涨停, 成交{best_sec[1]['amt']/1e8:.0f}亿）")
    w("=" * 70)

    text = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[完成] 文本复盘 → {output_path}")


# ========== HTML 可视化 ==========

def generate_html(stocks, output_path):
    total = len(stocks)
    total_amt = sum(s["amt"] for s in stocks)
    limit_ups = [s for s in stocks if s["chg"] >= s["up_limit"] and abs(s["chg"]) < 30]
    limit_downs = [s for s in stocks if s["chg"] <= s["down_limit"]]
    sorted_chg = sorted([s["chg"] for s in stocks])

    # 板块热力（文本总结用）
    sector_heat = build_sector_heat(stocks)

    # 泡泡图数据
    bubble_data = build_sector_bubbles(stocks)
    bubble_json = json.dumps(bubble_data, ensure_ascii=False)

    # 连板
    ladder = build_streak_ladder(stocks)

    # 成交额（10+10可展开）
    by_amt = sorted(stocks, key=lambda x: x["amt"], reverse=True)
    max_amt = by_amt[0]["amt"] if by_amt else 1e8

    # 分歧池
    divergence = build_divergence_pool(stocks)

    # 5日涨跌（10+20可展开）
    valid5_up = sorted([s for s in stocks if s["chg5"] != 0], key=lambda x: x["chg5"], reverse=True)
    valid5_down = sorted([s for s in stocks if s["chg5"] != 0], key=lambda x: x["chg5"])

    # 高换手 TOP30
    by_turnover = sorted([s for s in stocks if s["turnover"] > 0], key=lambda x: x["turnover"], reverse=True)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    up_count = sum(1 for s in stocks if s["chg"] > 0)
    down_count = sum(1 for s in stocks if s["chg"] < 0)

    # ===== 构建 CSS =====
    css = """
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif; background:#1a1a2e; color:#e0e0e0; padding:20px; }
  .header { text-align:center; padding:24px 0 16px; border-bottom:2px solid #333; margin-bottom:20px; }
  .header h1 { font-size:26px; color:#fff; }
  .header .date { color:#888; font-size:14px; margin-top:4px; }
  .dashboard { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:24px; }
  .card { background:#16213e; border-radius:8px; padding:16px; }
  .card .label { color:#888; font-size:12px; margin-bottom:4px; }
  .card .value { font-size:28px; font-weight:bold; }
  .card .sub { color:#888; font-size:12px; margin-top:4px; }
  .red { color:#e74c3c; }
  .green { color:#27ae60; }
  .yellow { color:#f39c12; }
  .white { color:#ecf0f1; }
  .section { background:#16213e; border-radius:8px; padding:20px; margin-bottom:16px; }
  .section h2 { font-size:18px; color:#fff; border-bottom:1px solid #333; padding-bottom:10px; margin-bottom:14px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { text-align:left; color:#888; padding:6px 8px; border-bottom:1px solid #333; font-weight:normal; }
  td { padding:6px 8px; border-bottom:1px solid #222; }
  tr:hover { background:rgba(255,255,255,0.03); }
  .bar-wrap { display:flex; align-items:center; gap:8px; }
  .bar { height:18px; border-radius:3px; transition:width 0.3s; }
  .bar-red { background:linear-gradient(90deg,#c0392b,#e74c3c); }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  .tag { display:inline-block; padding:2px 6px; border-radius:3px; font-size:11px; margin:1px 2px; }
  .tag-up { background:#c0392b; color:#fff; }
  .tag-strong { background:#e67e22; color:#fff; }
  .summary { font-size:14px; line-height:2; }

  /* 金字塔连板 */
  .pyramid { display:flex; flex-direction:column; align-items:center; gap:6px; }
  .pyramid-level { text-align:center; padding:8px 16px; border-radius:6px; background:rgba(255,255,255,0.04); }
  .pyramid-level .num { font-size:24px; font-weight:bold; color:#f39c12; }
  .pyramid-level .names { color:#bbb; font-size:13px; margin-top:4px; }

  /* 碰撞泡泡图 */
  .bubble-wrap { position:relative; text-align:center; }
  .bubble-wrap canvas { max-width:100%; cursor:pointer; border-radius:6px; }
  .bubble-tooltip { display:none; position:absolute; background:rgba(15,15,30,0.95); color:#fff; padding:10px 14px; border-radius:6px; font-size:12px; pointer-events:none; z-index:10; white-space:nowrap; line-height:1.7; border:1px solid rgba(255,255,255,0.12); box-shadow:0 4px 16px rgba(0,0,0,0.5); }
  .disclaimer { color:#666; font-size:11px; margin-top:10px; text-align:left; }

  /* 可展开 */
  .expand-btn { display:block; width:100%; text-align:center; padding:8px; margin-top:8px; background:rgba(255,255,255,0.04); color:#888; border:none; border-radius:4px; cursor:pointer; font-size:12px; }
  .expand-btn:hover { background:rgba(255,255,255,0.08); color:#ccc; }
  .expandable { display:none; }
  .expandable.show { display:table-row; }
"""

    # ===== 构建 HTML body =====
    body = f"""<div class="header">
  <h1>A股市场复盘</h1>
  <div class="date">{now}</div>
</div>

<!-- 仪表盘 -->
<div class="dashboard">
  <div class="card">
    <div class="label">涨跌分布</div>
    <div class="value"><span class="red counter" data-target="{up_count}">0</span> <span style="font-size:18px;color:#888;">/</span> <span class="green counter" data-target="{down_count}">0</span></div>
    <div class="sub">涨跌比 {up_count/max(1,down_count):.1f}:1</div>
  </div>
  <div class="card">
    <div class="label">成交额</div>
    <div class="value white"><span class="counter" data-target="{total_amt/1e8:.0f}">0</span><span style="font-size:16px;">亿</span></div>
    <div class="sub">全市场</div>
  </div>
  <div class="card">
    <div class="label">涨停 / 跌停</div>
    <div class="value"><span class="red counter" data-target="{len(limit_ups)}">0</span> <span style="color:#888;">/</span> <span class="green">{len(limit_downs)}</span></div>
    <div class="sub">均涨幅 {sum(sorted_chg)/total:+.2f}%  中位 {sorted_chg[total//2]:+.2f}%</div>
  </div>
  <div class="card">
    <div class="label">资金主攻</div>
    <div class="value yellow" style="font-size:20px;">{sector_heat[0][0][:10] if sector_heat else '-'}</div>
    <div class="sub">{sector_heat[0][1]['count'] if sector_heat else 0}家涨停</div>
  </div>
</div>

<!-- 板块热力图 - 碰撞泡泡 -->
<div class="section">
  <h2>板块热力图</h2>
  <div class="bubble-wrap">
    <canvas id="bubbleChart" width="750" height="450"></canvas>
    <div id="bubbleTooltip" class="bubble-tooltip"></div>
  </div>
  <p class="disclaimer">* 行业分类基于同花顺细分行业。泡泡大小=板块总市值，颜色=平均涨跌幅等级。鼠标悬停查看详情。</p>
</div>

<div class="grid2">
<!-- 连板梯队 - 金字塔 -->
<div class="section">
  <h2>连板梯队</h2>
  <div class="pyramid">
"""
    if ladder:
        max_streak = max(ladder.keys())
        for streak in sorted(ladder.keys(), reverse=True):
            ss = ladder[streak]
            pct = int(50 + 50 * streak / max_streak)  # width %: higher streak → wider
            names = "、".join(f"<span class='tag tag-up'>{s['name']}</span>" for s in ss[:6])
            more = f" 等{len(ss)}家" if len(ss) > 6 else ""
            body += f"""    <div class="pyramid-level" style="min-width:{pct}%;">
      <div class="num">{streak}板 ×{len(ss)}</div>
      <div class="names">{names}{more}</div>
    </div>
"""
    else:
        body += '    <div style="color:#888;text-align:center;">无2连板以上</div>\n'

    body += """  </div>
</div>

<!-- 成交额 TOP（可展开） -->
<div class="section" id="sec-amt">
  <h2>成交额 TOP10</h2>
  <table>
"""
    for i, s in enumerate(by_amt[:10]):
        bar_pct = s["amt"] / max_amt * 100
        chg_color = "#e74c3c" if s["chg"] > 0 else ("#27ae60" if s["chg"] < 0 else "#888")
        body += f"""    <tr>
      <td style="color:#888;">{i+1}</td>
      <td>{s['name']}</td>
      <td style="font-size:11px;">{s['board'][:4]}</td>
      <td style="color:{chg_color};">{s['chg']:+.2f}%</td>
      <td style="width:100%;">
        <div class="bar-wrap"><div class="bar bar-red" style="width:{bar_pct}%;opacity:0.6;"></div></div>
      </td>
      <td style="text-align:right;">{s['amt']/1e8:.0f}亿</td>
    </tr>
"""
    # 11-20 隐藏行
    for i, s in enumerate(by_amt[10:20]):
        bar_pct = s["amt"] / max_amt * 100
        chg_color = "#e74c3c" if s["chg"] > 0 else ("#27ae60" if s["chg"] < 0 else "#888")
        body += f"""    <tr class="expandable" data-group="amt">
      <td style="color:#888;">{i+11}</td>
      <td>{s['name']}</td>
      <td style="font-size:11px;">{s['board'][:4]}</td>
      <td style="color:{chg_color};">{s['chg']:+.2f}%</td>
      <td style="width:100%;">
        <div class="bar-wrap"><div class="bar bar-red" style="width:{bar_pct}%;opacity:0.6;"></div></div>
      </td>
      <td style="text-align:right;">{s['amt']/1e8:.0f}亿</td>
    </tr>
"""
    body += """  </table>
  <button class="expand-btn" onclick="toggleGroup('amt', this)">展开全部</button>
</div>
</div>

<!-- 分歧观察池（可展开） -->
<div class="section" id="sec-div">
  <h2>分歧观察池</h2>
  <table>
    <tr><th>名称</th><th>代码</th><th>板块</th><th>涨幅</th><th>换手</th><th>成交</th></tr>
"""
    for i, s in enumerate(divergence[:10]):
        chg_color = "#e74c3c" if s["chg"] > 0 else "#27ae60"
        body += f"""    <tr>
      <td>{s['name']}</td><td>{s['code']}</td><td style="font-size:11px;">{s['board'][:4]}</td>
      <td style="color:{chg_color};">{s['chg']:+.2f}%</td>
      <td>{s['turnover']:.1f}%</td>
      <td style="text-align:right;">{s['amt']/1e8:.1f}亿</td>
    </tr>
"""
    for i, s in enumerate(divergence[10:20]):
        chg_color = "#e74c3c" if s["chg"] > 0 else "#27ae60"
        body += f"""    <tr class="expandable" data-group="div">
      <td>{s['name']}</td><td>{s['code']}</td><td style="font-size:11px;">{s['board'][:4]}</td>
      <td style="color:{chg_color};">{s['chg']:+.2f}%</td>
      <td>{s['turnover']:.1f}%</td>
      <td style="text-align:right;">{s['amt']/1e8:.1f}亿</td>
    </tr>
"""
    if len(divergence) > 10:
        body += """  </table>
  <button class="expand-btn" onclick="toggleGroup('div', this)">展开全部</button>
</div>
"""
    else:
        body += """  </table>
</div>
"""

    # 5日涨跌（可展开）
    body += """<div class="grid2">
<div class="section" id="sec-5up">
  <h2>5日涨幅 TOP10</h2>
  <table>
"""
    for i, s in enumerate(valid5_up[:10]):
        body += f"""    <tr><td style="color:#888;">{i+1}</td><td>{s['name']}</td>
      <td style="font-size:11px;">{s['board'][:4]}</td>
      <td style="color:#e74c3c;">{s['chg5']:+.2f}%</td><td style="color:#888;font-size:11px;">今{s['chg']:+.2f}%</td></tr>
"""
    body += """  </table>
"""
    if len(valid5_up) > 10:
        body += """  <button class="expand-btn" onclick="toggleGroup('5up', this)">展开全部</button>
"""
    body += """</div>
<div class="section" id="sec-5dn">
  <h2>5日跌幅 TOP10</h2>
  <table>
"""
    for i, s in enumerate(valid5_down[:10]):
        body += f"""    <tr><td style="color:#888;">{i+1}</td><td>{s['name']}</td>
      <td style="font-size:11px;">{s['board'][:4]}</td>
      <td style="color:#27ae60;">{s['chg5']:+.2f}%</td><td style="color:#888;font-size:11px;">今{s['chg']:+.2f}%</td></tr>
"""
    body += """  </table>
"""
    if len(valid5_down) > 10:
        body += """  <button class="expand-btn" onclick="toggleGroup('5dn', this)">展开全部</button>
"""
    body += """</div>
</div>

<!-- 高换手率（可展开） -->
<div class="section" id="sec-turnover">
  <h2>高换手率 TOP10</h2>
  <table>
    <tr><th>名称</th><th>代码</th><th>板块</th><th>换手率</th><th>涨幅</th><th>成交</th></tr>
"""
    for i, s in enumerate(by_turnover[:10]):
        chg_color = "#e74c3c" if s["chg"] > 0 else ("#27ae60" if s["chg"] < 0 else "#888")
        body += f"""    <tr>
      <td style="color:#888;">{i+1}</td><td>{s['name']}</td><td style="font-size:11px;">{s['board'][:4]}</td>
      <td>{s['turnover']:.1f}%</td>
      <td style="color:{chg_color};">{s['chg']:+.2f}%</td>
      <td style="text-align:right;">{s['amt']/1e8:.1f}亿</td>
    </tr>
"""
    for i, s in enumerate(by_turnover[10:20]):
        chg_color = "#e74c3c" if s["chg"] > 0 else ("#27ae60" if s["chg"] < 0 else "#888")
        body += f"""    <tr class="expandable" data-group="turnover">
      <td style="color:#888;">{i+11}</td><td>{s['name']}</td><td style="font-size:11px;">{s['board'][:4]}</td>
      <td>{s['turnover']:.1f}%</td>
      <td style="color:{chg_color};">{s['chg']:+.2f}%</td>
      <td style="text-align:right;">{s['amt']/1e8:.1f}亿</td>
    </tr>
"""
    body += """  </table>
  <button class="expand-btn" onclick="toggleGroup('turnover', this)">展开全部</button>
</div>

<!-- 总结 -->
<div class="section">
  <h2>市场总结</h2>
  <div class="summary">
"""
    up_ratio = up_count / total if total > 0 else 0
    mood = "极度亢奋，全面普涨" if up_ratio > 0.75 else ("偏强，多数上涨" if up_ratio > 0.6 else ("分化，涨跌互现" if up_ratio > 0.45 else "偏弱"))
    top_streak = max(ladder.keys()) if ladder else 0
    body += f"""    <p>市场情绪: <b style="color:#f39c12;">{mood}</b> | 涨跌比 <span class="red">{up_count}</span>:<span class="green">{down_count}</span></p>
    <p>涨停 <span class="red">{len(limit_ups)}</span> 家 | 跌停 <span class="green">{len(limit_downs)}</span> 家 | 成交 <b>{total_amt/1e8:,.0f}</b> 亿</p>
"""
    if top_streak:
        body += f"    <p>最高连板: <b class='yellow'>{top_streak}板</b></p>\n"
    if sector_heat:
        body += f"    <p>主线: <b>{sector_heat[0][0]}</b>（{sector_heat[0][1]['count']}家涨停, 成交{sector_heat[0][1]['amt']/1e8:.0f}亿）</p>\n"
    body += """    <p style="color:#666;margin-top:12px;font-size:12px;">免责声明: 以上分析仅供参考，不构成投资建议。</p>
  </div>
</div>
"""

    # ===== 构建 JS（数据预先序列化，避免 f-string 花括号冲突） =====
    valid5_up_json = json.dumps([{"name": s["name"], "board": s["board"][:4], "chg5": s["chg5"], "chg": s["chg"]} for s in valid5_up[10:30]], ensure_ascii=False)
    valid5_dn_json = json.dumps([{"name": s["name"], "board": s["board"][:4], "chg5": s["chg5"], "chg": s["chg"]} for s in valid5_down[10:30]], ensure_ascii=False)

    js = f"""<script>
// ===== 数据 =====
var BUBBLE_DATA = {bubble_json};
var VALID5_UP = {valid5_up_json};
var VALID5_DN = {valid5_dn_json};

// ===== 计数器动画 =====
function animateCounters() {{
  var counters = document.querySelectorAll('.counter');
  counters.forEach(function(el) {{
    var target = parseInt(el.dataset.target);
    if (isNaN(target)) return;
    var duration = 800;
    var start = performance.now();
    function update(now) {{
      var elapsed = now - start;
      var progress = Math.min(elapsed / duration, 1);
      var eased = 1 - Math.pow(1 - progress, 3);
      el.textContent = Math.round(target * eased).toLocaleString();
      if (progress < 1) requestAnimationFrame(update);
      else el.textContent = target.toLocaleString();
    }}
    requestAnimationFrame(update);
  }});
}}

// ===== 可展开列表 =====
function toggleGroup(group, btn) {{
  var rows = document.querySelectorAll('.expandable[data-group="' + group + '"]');
  var isOpen = rows.length > 0 && rows[0].classList.contains('show');
  rows.forEach(function(r) {{ r.classList.toggle('show', !isOpen); }});
  btn.textContent = isOpen ? '展开全部' : '收起';
}}

// ===== 5日涨跌展开（追加行） =====
function toggle5Up(btn) {{
  var container = document.getElementById('sec-5up');
  var table = container.querySelector('table');
  var existing = container.querySelector('.exp-added');
  if (existing) {{
    existing.remove();
    btn.textContent = '展开全部';
    return;
  }}
  var tbody = document.createElement('tbody');
  tbody.className = 'exp-added';
  VALID5_UP.forEach(function(s, i) {{
    var tr = document.createElement('tr');
    tr.innerHTML = '<td style="color:#888;">' + (i+11) + '</td><td>' + s.name + '</td>' +
      '<td style="font-size:11px;">' + s.board + '</td>' +
      '<td style="color:#e74c3c;">' + (s.chg5>=0?'+':'') + s.chg5.toFixed(2) + '%</td>' +
      '<td style="color:#888;font-size:11px;">今' + (s.chg>=0?'+':'') + s.chg.toFixed(2) + '%</td>';
    tbody.appendChild(tr);
  }});
  table.appendChild(tbody);
  btn.textContent = '收起';
}}

function toggle5Dn(btn) {{
  var container = document.getElementById('sec-5dn');
  var table = container.querySelector('table');
  var existing = container.querySelector('.exp-added');
  if (existing) {{
    existing.remove();
    btn.textContent = '展开全部';
    return;
  }}
  var tbody = document.createElement('tbody');
  tbody.className = 'exp-added';
  VALID5_DN.forEach(function(s, i) {{
    var tr = document.createElement('tr');
    tr.innerHTML = '<td style="color:#888;">' + (i+11) + '</td><td>' + s.name + '</td>' +
      '<td style="font-size:11px;">' + s.board + '</td>' +
      '<td style="color:#27ae60;">' + (s.chg5>=0?'+':'') + s.chg5.toFixed(2) + '%</td>' +
      '<td style="color:#888;font-size:11px;">今' + (s.chg>=0?'+':'') + s.chg.toFixed(2) + '%</td>';
    tbody.appendChild(tr);
  }});
  table.appendChild(tbody);
  btn.textContent = '收起';
}}

// ===== 碰撞泡泡图 =====
(function() {{
  var canvas = document.getElementById('bubbleChart');
  if (!canvas || !BUBBLE_DATA.length) return;
  var ctx = canvas.getContext('2d');
  var tooltip = document.getElementById('bubbleTooltip');
  var W = canvas.width, H = canvas.height;
  var floorColor = '#1a1a2e';  // 画板背景

  // === 颜色阶梯: 根据 avg_chg 和 limit_ups 分配 ===
  function getBubbleStyle(d) {{
    var chg = d.avg_chg, lu = d.limit_ups;
    if (lu >= 5)     return {{ fill:'#c62828', glow:'#ff1744', label:'多股涨停' }};
    if (lu >= 2)     return {{ fill:'#d32f2f', glow:'#ff5252', label:'板块涨停潮' }};
    if (chg >= 5)    return {{ fill:'#e53935', glow:'#ff6e40', label:'强势' }};
    if (chg >= 2)    return {{ fill:'#ff5722', glow:'#ff8a65', label:'偏强' }};
    if (chg >= 0.5)  return {{ fill:'#ff9800', glow:'#ffb74d', label:'温和' }};
    if (chg > 0)     return {{ fill:'#ffcc80', glow:'#ffe0b2', label:'微涨' }};
    if (chg == 0)    return {{ fill:'#78909c', glow:'#b0bec5', label:'平盘' }};
    if (chg > -1.5)  return {{ fill:'#80cbc4', glow:'#b2dfdb', label:'微跌' }};
    if (chg > -3)    return {{ fill:'#4caf50', glow:'#81c784', label:'下跌' }};
    if (chg > -5)    return {{ fill:'#2e7d32', glow:'#4caf50', label:'深跌' }};
    return {{ fill:'#1b5e20', glow:'#2e7d32', label:'大跌' }};
  }}

  // === 半径计算: log市值映射 ===
  function calcRadius(mcap) {{
    var logM = Math.log10(Math.max(mcap, 1e8));
    return 16 + (logM - 8) / 4 * 38;
  }}

  // === 初始化泡泡 ===
  var bubbles = [];
  BUBBLE_DATA.forEach(function(d) {{
    var r = calcRadius(d.total_mcap);
    var style = getBubbleStyle(d);
    bubbles.push({{
      x: 30 + Math.random() * (W - 60),
      y: 30 + Math.random() * (H - 60),
      vx: (Math.random() - 0.5) * 2,
      vy: (Math.random() - 0.5) * 2,
      r: r, baseR: r,
      sector: d.sector, avg_chg: d.avg_chg, up_ratio: d.up_ratio,
      mcap: d.total_mcap, limit_ups: d.limit_ups, count: d.count,
      leaders: d.leaders, style: style,
      hoverScale: 1
    }});
  }});

  // === 碰撞检测与响应 ===
  function resolveCollision(a, b) {{
    var dx = b.x - a.x, dy = b.y - a.y;
    var dist = Math.sqrt(dx * dx + dy * dy);
    var minDist = a.r * a.hoverScale + b.r * b.hoverScale + 1;
    if (dist < minDist && dist > 0.001) {{
      // 分离重叠
      var overlap = minDist - dist;
      var nx = dx / dist, ny = dy / dist;
      var totalMass = a.r * a.r + b.r * b.r;
      var aRatio = b.r * b.r / totalMass;
      var bRatio = a.r * a.r / totalMass;
      a.x -= nx * overlap * aRatio * 0.8;
      a.y -= ny * overlap * aRatio * 0.8;
      b.x += nx * overlap * bRatio * 0.8;
      b.y += ny * overlap * bRatio * 0.8;
      // 弹性碰撞
      var dvx = a.vx - b.vx, dvy = a.vy - b.vy;
      var dvDotN = dvx * nx + dvy * ny;
      if (dvDotN > 0) {{
        var impulse = dvDotN * 0.8;
        a.vx -= nx * impulse * aRatio;
        a.vy -= ny * impulse * aRatio;
        b.vx += nx * impulse * bRatio;
        b.vy += ny * impulse * bRatio;
      }}
    }}
  }}

  // === 物理更新 ===
  function updatePhysics() {{
    var maxSpeed = 1.5;
    bubbles.forEach(function(b) {{
      // 减速（hover时减速更多）
      b.vx *= (b.hoverScale > 1 ? 0.995 : 0.999);
      b.vy *= (b.hoverScale > 1 ? 0.995 : 0.999);
      // 最小速度(防止静止)
      var spd = Math.sqrt(b.vx * b.vx + b.vy * b.vy);
      if (spd < 0.15 && b.hoverScale < 1.05) {{
        var angle = Math.random() * Math.PI * 2;
        b.vx += Math.cos(angle) * 0.08;
        b.vy += Math.sin(angle) * 0.08;
      }}
      // 限速
      if (spd > maxSpeed) {{
        b.vx = b.vx / spd * maxSpeed;
        b.vy = b.vy / spd * maxSpeed;
      }}
      // 位移
      b.x += b.vx;
      b.y += b.vy;
      // 边界碰撞
      var cr = b.r * b.hoverScale;
      if (b.x - cr < 0) {{ b.x = cr; b.vx = Math.abs(b.vx) * 0.7; }}
      if (b.x + cr > W) {{ b.x = W - cr; b.vx = -Math.abs(b.vx) * 0.7; }}
      if (b.y - cr < 0) {{ b.y = cr; b.vy = Math.abs(b.vy) * 0.7; }}
      if (b.y + cr > H) {{ b.y = H - cr; b.vy = -Math.abs(b.vy) * 0.7; }}
      // Hover缩放平滑
      b.hoverScale += ((b._targetScale || 1) - b.hoverScale) * 0.15;
    }});
    // 泡泡间碰撞 O(n²)
    for (var i = 0; i < bubbles.length; i++) {{
      for (var j = i + 1; j < bubbles.length; j++) {{
        resolveCollision(bubbles[i], bubbles[j]);
      }}
    }}
  }}

  // === 绘制 ===
  function draw() {{
    ctx.clearRect(0, 0, W, H);

    // 背景微妙网格
    ctx.strokeStyle = 'rgba(255,255,255,0.015)';
    ctx.lineWidth = 0.5;
    for (var gx = 0; gx < W; gx += 40) {{
      ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, H); ctx.stroke();
    }}
    for (var gy = 0; gy < H; gy += 40) {{
      ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(W, gy); ctx.stroke();
    }}

    // 画泡泡
    bubbles.forEach(function(b) {{
      var cr = b.r * b.hoverScale;
      var s = b.style;

      // 光晕
      var glowGrad = ctx.createRadialGradient(b.x, b.y, cr * 0.5, b.x, b.y, cr * 1.8);
      glowGrad.addColorStop(0, s.glow);
      glowGrad.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.beginPath();
      ctx.arc(b.x, b.y, cr * 1.8, 0, Math.PI * 2);
      ctx.fillStyle = glowGrad;
      ctx.fill();

      // 主体渐变
      var bodyGrad = ctx.createRadialGradient(b.x - cr*0.25, b.y - cr*0.3, cr * 0.1, b.x, b.y, cr);
      bodyGrad.addColorStop(0, 'rgba(255,255,255,0.3)');
      bodyGrad.addColorStop(0.6, s.fill);
      bodyGrad.addColorStop(1, s.fill.replace(/[\\d.]+\\)$/, '0.65)'));
      ctx.beginPath();
      ctx.arc(b.x, b.y, cr, 0, Math.PI * 2);
      ctx.fillStyle = bodyGrad;
      ctx.fill();
      ctx.strokeStyle = s.fill;
      ctx.lineWidth = 1.2;
      ctx.stroke();

      // 文字标签
      if (cr > 18) {{
        ctx.fillStyle = '#fff';
        ctx.font = (cr > 28 ? 'bold 11px' : '10px') + ' "Microsoft YaHei"';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        var label = b.sector.length > 7 ? b.sector.slice(0, 7) : b.sector;
        ctx.fillText(label, b.x, b.y - 2);
        // 涨跌幅小字
        if (cr > 25) {{
          ctx.font = '9px "Microsoft YaHei"';
          ctx.fillStyle = 'rgba(255,255,255,0.8)';
          ctx.fillText((b.avg_chg >= 0 ? '+' : '') + b.avg_chg.toFixed(1) + '%', b.x, b.y + 10);
        }}
      }}
    }});
  }}

  // === 动效: 初始从中心爆开 ===
  var cx = W / 2, cy = H / 2;
  bubbles.forEach(function(b) {{
    var dx = b.x - cx, dy = b.y - cy;
    b.x = cx + dx * 0.01;
    b.y = cy + dy * 0.01;
    b.vx = dx * 0.04 + (Math.random() - 0.5) * 1;
    b.vy = dy * 0.04 + (Math.random() - 0.5) * 1;
  }});

  // === 主循环 ===
  function loop() {{
    updatePhysics();
    draw();
    requestAnimationFrame(loop);
  }}
  loop();

  // === Hover 交互 ===
  canvas.addEventListener('mousemove', function(e) {{
    var rect = canvas.getBoundingClientRect();
    var mx = (e.clientX - rect.left) * (W / rect.width);
    var my = (e.clientY - rect.top) * (H / rect.height);
    var found = null;
    for (var i = bubbles.length - 1; i >= 0; i--) {{
      var b = bubbles[i];
      var dx = mx - b.x, dy = my - b.y;
      if (dx * dx + dy * dy <= (b.r * 1.2) * (b.r * 1.2)) {{
        found = b; break;
      }}
    }}
    bubbles.forEach(function(b) {{ b._targetScale = (b === found) ? 1.25 : 1; }});
    if (found) {{
      tooltip.style.display = 'block';
      tooltip.style.left = (found.x + found.r + 12) + 'px';
      tooltip.style.top = (found.y - 25) + 'px';
      tooltip.innerHTML = '<b>' + found.sector + '</b> <span style="font-size:10px;color:' + found.style.fill + ';">' + found.style.label + '</span><br>' +
        '平均涨跌: <b style="color:' + (found.avg_chg>=0?'#ff5252':'#69f0ae') + ';">' + (found.avg_chg>=0?'+':'') + found.avg_chg.toFixed(2) + '%</b> | 上涨占比: ' + found.up_ratio + '%<br>' +
        '涨停: ' + found.limit_ups + '家 | 市值: ' + (found.mcap/1e8).toFixed(0) + '亿 | ' + found.count + '只成分股<br>' +
        '代表: ' + (found.leaders||[]).slice(0,2).join(', ');
      canvas.style.cursor = 'pointer';
    }} else {{
      tooltip.style.display = 'none';
      canvas.style.cursor = 'default';
    }}
  }});
  canvas.addEventListener('mouseleave', function() {{
    tooltip.style.display = 'none';
    canvas.style.cursor = 'default';
    bubbles.forEach(function(b) {{ b._targetScale = 1; }});
  }});
}})();

// ===== 页面加载完成后执行 =====
document.addEventListener('DOMContentLoaded', function() {{
  animateCounters();
  // 5日涨跌的展开按钮
  var btn5up = document.querySelector('#sec-5up .expand-btn');
  if (btn5up) btn5up.onclick = function() {{ toggle5Up(this); }};
  var btn5dn = document.querySelector('#sec-5dn .expand-btn');
  if (btn5dn) btn5dn.onclick = function() {{ toggle5Dn(this); }};
}});
</script>
"""

    # ===== 组装完整 HTML =====
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股市场复盘 {now}</title>
<style>
{css}
</style>
</head>
<body>
{body}
{js}
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[完成] HTML复盘 → {output_path}")


# ========== 主入口 ==========

def main():
    import argparse
    parser = argparse.ArgumentParser(description="分板块市场复盘报告生成器（文本+HTML）")
    parser.add_argument("csv", help="Table.csv 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出基础路径（生成 .recap 和 .html）")
    args = parser.parse_args()

    if not os.path.exists(args.csv):
        print(f"[错误] 文件不存在: {args.csv}")
        sys.exit(1)

    print(f"[读取] {args.csv}")
    stocks = read_stocks(args.csv)
    print(f"[解析] {len(stocks)} 只股票")

    base = args.output or os.path.splitext(args.csv)[0] + "_recap"
    generate_text(stocks, base + ".txt")
    generate_html(stocks, base + ".html")

    print(f"\n[DONE] 可打开 {base}.html 查看可视化报告")


if __name__ == "__main__":
    main()
