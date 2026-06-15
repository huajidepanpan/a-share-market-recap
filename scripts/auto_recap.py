#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auto_recap.py —— 一键复盘: Table??.xls → 转换 → 复盘 → 提交 → 推送

用法:
    python auto_recap.py 20260604                    # 从目录名
    python auto_recap.py ../20260604/Table02.xls     # 从文件路径
    python auto_recap.py 20260604 --no-push          # 只生成，不推送
    python auto_recap.py 20260604 --msg "自定义提交信息"

流程:
    1. convert_table.py:  Table??.xls → Table??.csv
    2. market_recap.py:   Table??.csv → recap_??.txt + recap_??.html
    3. 同步 recap.txt/html → 指向最大后缀的 recap_??.*
    4. 构建 recap_index.json (供首页导航)
    5. git add {date_dir}/ → commit → push
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS_DIR)

INDEX_HTML = os.path.join(REPO_ROOT, "index.html")
INDEX_JSON = os.path.join(REPO_ROOT, "recap_index.json")


def run_cmd(cmd, cwd=None, description=""):
    """运行命令，打印输出，遇错退出"""
    label = f" [{description}]" if description else ""
    banner = f"\n{'='*50}\n步骤{label}: {' '.join(cmd)}\n{'='*50}"
    _safe_print(banner)
    result = subprocess.run(cmd, cwd=cwd or REPO_ROOT)
    if result.returncode != 0:
        _safe_print(f"[ERROR] 步骤失败，退出码 {result.returncode}")
        sys.exit(1)
    return True


def _safe_print(text):
    """安全打印，绕过 Windows 控制台编码问题"""
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()


def find_table_file(dir_path, exts=None):
    """
    在目录中按优先级查找 Table 文件。
    优先级: Table02 > Table01 > Table（支持新旧命名）
    返回找到的完整路径，未找到返回 None。
    """
    if exts is None:
        exts = [".xls", ".xlsx"]
    for prefix in ("Table02", "Table01", "Table"):
        for ext in exts:
            path = os.path.join(dir_path, prefix + ext)
            if os.path.exists(path):
                return path
    return None


def find_all_table_files(dir_path, exts=None):
    """
    查找目录中所有 Table??.xls 文件。
    返回 [(prefix, path), ...] 按后缀升序排列。
    """
    if exts is None:
        exts = [".xls", ".xlsx"]
    results = []
    for prefix in ("Table02", "Table01", "Table"):
        for ext in exts:
            path = os.path.join(dir_path, prefix + ext)
            if os.path.exists(path):
                results.append((prefix, path))
    results.sort(key=lambda x: x[0])
    return results


def extract_suffix(prefix):
    """从 Table 前缀提取后缀，如 'Table02'→'02', 'Table01'→'01', 'Table'→''"""
    m = re.search(r"(\d+)$", prefix)
    return m.group(1) if m else ""


def resolve_path(user_input):
    """解析用户输入，返回 (目录路径, date_str)"""
    user_input = user_input.strip().rstrip("/\\")

    if user_input.lower().endswith((".xls", ".xlsx")):
        xls_path = os.path.abspath(user_input) if not os.path.isabs(user_input) \
                   else user_input
        if not os.path.isabs(user_input):
            xls_path = os.path.join(REPO_ROOT, user_input)
        dir_path = os.path.dirname(xls_path)
        date_str = os.path.basename(dir_path)
        return dir_path, date_str

    dir_path = os.path.join(REPO_ROOT, user_input)
    if not os.path.isdir(dir_path):
        dir_path = os.path.abspath(user_input)
    date_str = os.path.basename(dir_path)
    return dir_path, date_str


def sync_default_recap(dir_path):
    """
    将目录中最大后缀的 recap_??.html/txt 复制到 recap.html/txt。
    如果只有 recap.txt/html（旧格式），保持不变。
    """
    html_files = glob.glob(os.path.join(dir_path, "recap_*.html"))
    txt_files = glob.glob(os.path.join(dir_path, "recap_*.txt"))

    if not html_files and not txt_files:
        return  # 没有任何 recp_* 文件，跳过

    # 取最大后缀
    def _suffix_num(p):
        m = re.search(r"recap_(\d+)", os.path.basename(p))
        return int(m.group(1)) if m else 0

    html_files.sort(key=_suffix_num)
    txt_files.sort(key=_suffix_num)

    default_html = os.path.join(dir_path, "recap.html")
    default_txt = os.path.join(dir_path, "recap.txt")

    if html_files:
        src = html_files[-1]
        if not os.path.exists(default_html) or not os.path.samefile(src, default_html):
            shutil.copy2(src, default_html)
            _safe_print(f"  recap.html → {os.path.basename(src)}")

    if txt_files:
        src = txt_files[-1]
        if not os.path.exists(default_txt) or not os.path.samefile(src, default_txt):
            shutil.copy2(src, default_txt)
            _safe_print(f"  recap.txt  → {os.path.basename(src)}")


def build_recap_index():
    """
    扫描所有日期目录，构建 recap_index.json。
    """
    dates = []
    recaps = {}

    for entry in sorted(os.listdir(REPO_ROOT), reverse=True):
        dir_path = os.path.join(REPO_ROOT, entry)
        if not os.path.isdir(dir_path) or not re.match(r"^\d{8}$", entry):
            continue

        html_files = sorted(glob.glob(os.path.join(dir_path, "recap_*.html")))
        txt_files = sorted(glob.glob(os.path.join(dir_path, "recap_*.txt")))

        # 也检查旧格式 recap.html/txt
        legacy_html = os.path.join(dir_path, "recap.html")
        legacy_txt = os.path.join(dir_path, "recap.txt")

        recap_list = []
        seen_suffixes = set()

        for hf in html_files:
            m = re.search(r"recap_(\d+)", os.path.basename(hf))
            suffix = m.group(1) if m else "00"
            if suffix in seen_suffixes:
                continue
            seen_suffixes.add(suffix)
            tf = os.path.join(dir_path, f"recap_{suffix}.txt")
            recap_list.append({
                "suffix": suffix,
                "label": f"复盘 {suffix}",
                "html": f"{entry}/recap_{suffix}.html",
                "txt": f"{entry}/recap_{suffix}.txt" if os.path.exists(tf) else None,
            })

        # 旧格式（没有 recap_* 只有 recap.html）
        if not recap_list and os.path.exists(legacy_html):
            recap_list.append({
                "suffix": "",
                "label": "复盘",
                "html": f"{entry}/recap.html",
                "txt": f"{entry}/recap.txt" if os.path.exists(legacy_txt) else None,
            })

        if recap_list:
            dates.append(entry)
            recaps[entry] = {
                "recaps": recap_list,
                "default": recap_list[-1]["suffix"],
                "premarket": f"{entry}/premarket" if os.path.exists(
                    os.path.join(dir_path, "premarket")) else None,
            }

    index_data = {
        "dates": dates,
        "latest": dates[0] if dates else None,
        "recaps": recaps,
    }

    with open(INDEX_JSON, "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)
    _safe_print(f"  recap_index.json 已更新 ({len(dates)} 个日期)")


def ensure_index_html():
    """确保 repo 根目录存在 index.html 导航首页"""
    if os.path.exists(INDEX_HTML):
        return  # 已存在，不覆盖（用户可能手动修改）

    html = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股市场复盘</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,'Microsoft YaHei',sans-serif;background:#1a1a2e;color:#eee}
#toolbar{position:fixed;top:0;left:0;right:0;z-index:100;
  background:linear-gradient(135deg,#16213e,#0f3460);padding:10px 16px;
  display:flex;align-items:center;gap:12px;flex-wrap:wrap;
  box-shadow:0 2px 12px rgba(0,0,0,.5)}
#toolbar select,#toolbar button{
  padding:6px 12px;border-radius:6px;border:1px solid #e94560;
  background:#1a1a2e;color:#eee;font-size:14px;cursor:pointer}
#toolbar button:hover{background:#e94560}
#toolbar .title{font-size:18px;font-weight:bold;color:#e94560;margin-right:8px}
#toolbar .sep{color:#555;margin:0 2px}
#tabs{display:flex;gap:4px;flex-wrap:wrap}
#tabs .tab{padding:6px 14px;border-radius:6px;border:1px solid #555;
  background:#1a1a2e;color:#aaa;cursor:pointer;font-size:13px}
#tabs .tab.active{background:#e94560;color:#fff;border-color:#e94560}
#toolbar a{color:#0af;text-decoration:none;font-size:13px}
#iframe-wrap{position:fixed;top:54px;left:0;right:0;bottom:0}
iframe{width:100%;height:100%;border:none}
.no-data{text-align:center;padding:80px 20px;color:#888;font-size:16px}
</style>
</head>
<body>
<div id="toolbar">
  <span class="title">A股市场复盘</span>
  <button onclick="goPrev()" title="上一个交易日">◀</button>
  <select id="dateSelect" onchange="onDateChange()"></select>
  <button onclick="goNext()" title="下一个交易日">▶</button>
  <span class="sep">|</span>
  <div id="tabs"></div>
  <span style="flex:1"></span>
  <a id="premarketLink" href="#" style="display:none">盘前分析</a>
</div>
<div id="iframe-wrap">
  <iframe id="recapFrame" src="" onload="onFrameLoad()"></iframe>
</div>
<script>
var data = null;
var currentDate = '';
var currentSuffix = '';

fetch('recap_index.json?' + Date.now())
  .then(r => r.json())
  .then(d => { data = d; init(); })
  .catch(() => document.getElementById('iframe-wrap').innerHTML =
    '<div class="no-data">无法加载数据，请先运行 auto_recap.py 生成复盘</div>');

function init(){
  var sel = document.getElementById('dateSelect');
  sel.innerHTML = data.dates.map(d => '<option value="'+d+'">'+fmtDate(d)+'</option>').join('');
  if(data.latest){
    currentDate = data.latest;
    sel.value = currentDate;
    switchDate(currentDate);
  }
}

function fmtDate(d){ return d.slice(0,4)+'/'+d.slice(4,6)+'/'+d.slice(6,8); }

function switchDate(date){
  currentDate = date;
  var info = data.recaps[date];
  if(!info) return;
  currentSuffix = info.default;
  loadRecap(date, currentSuffix);
  renderTabs(date, info);
  // premarket link
  var pl = document.getElementById('premarketLink');
  if(info.premarket){
    pl.href = info.premarket;
    pl.style.display = '';
  } else {
    pl.style.display = 'none';
  }
}

function loadRecap(date, suffix){
  var info = data.recaps[date];
  var r = info.recaps.find(function(x){ return x.suffix === suffix; });
  var src = r ? r.html : '';
  var frame = document.getElementById('recapFrame');
  if(frame.src !== src) frame.src = src;
}

function renderTabs(date, info){
  var tabs = document.getElementById('tabs');
  if(info.recaps.length <= 1){
    tabs.innerHTML = '';
    return;
  }
  tabs.innerHTML = info.recaps.map(function(r){
    var cls = (r.suffix === currentSuffix) ? 'tab active' : 'tab';
    return '<span class="'+cls+'" onclick="switchRecap(\''+date+'\',\''+r.suffix+'\')">'+r.label+'</span>';
  }).join('');
}

function switchRecap(date, suffix){
  currentSuffix = suffix;
  loadRecap(date, suffix);
  renderTabs(date, data.recaps[date]);
}

function onDateChange(){
  switchDate(document.getElementById('dateSelect').value);
}

function goPrev(){
  var idx = data.dates.indexOf(currentDate);
  if(idx < data.dates.length - 1){
    currentDate = data.dates[idx + 1];
    document.getElementById('dateSelect').value = currentDate;
    switchDate(currentDate);
  }
}

function goNext(){
  var idx = data.dates.indexOf(currentDate);
  if(idx > 0){
    currentDate = data.dates[idx - 1];
    document.getElementById('dateSelect').value = currentDate;
    switchDate(currentDate);
  }
}

function onFrameLoad(){
  // 初始加载后无需额外处理
}
</script>
</body>
</html>"""
    with open(INDEX_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    _safe_print("  index.html 已创建（导航首页）")


def main():
    parser = argparse.ArgumentParser(description="一键复盘：Table??.xls → 复盘报告 → 提交推送")
    parser.add_argument("target", help="日期目录（如 20260604）或 Table.xls 路径")
    parser.add_argument("--no-push", action="store_true", help="只生成不推送")
    parser.add_argument("--no-commit", action="store_true", help="只生成不提交")
    parser.add_argument("--msg", default=None, help="自定义提交信息")
    args = parser.parse_args()

    # 解析路径
    dir_path, date_str = resolve_path(args.target)
    print(f"目标目录: {dir_path}")
    print(f"日期: {date_str}")

    if not os.path.isdir(dir_path):
        print(f"[ERROR] 目录不存在: {dir_path}")
        sys.exit(1)

    premarket = os.path.join(dir_path, "premarket")
    convert_script = os.path.join(SCRIPTS_DIR, "convert_table.py")
    recap_script = os.path.join(SCRIPTS_DIR, "market_recap.py")

    # === Step 1: 查找所有 Table 文件并逐个转换+复盘 ===
    table_files = find_all_table_files(dir_path, [".xls", ".xlsx"])
    if not table_files:
        print(f"[ERROR] 在 {dir_path} 中未找到 Table02.xls / Table01.xls / Table.xls")
        sys.exit(1)

    print(f"\n找到 {len(table_files)} 个 Table 文件")
    generated = []

    for prefix, xls_path in table_files:
        suffix = extract_suffix(prefix)
        xls_basename = os.path.basename(xls_path)
        csv_basename = prefix + ".csv"
        csv_path = os.path.join(dir_path, csv_basename)
        recap_suffix = f"_{suffix}" if suffix else ""
        recap_base = os.path.join(dir_path, f"recap{recap_suffix}")

        # Step 1a: 转换
        run_cmd(
            [sys.executable, convert_script, xls_path, "-o", csv_path],
            description=f"{xls_basename} → {csv_basename}",
        )

        # Step 1b: 复盘
        run_cmd(
            [sys.executable, recap_script, csv_path, "-o", recap_base],
            description=f"生成 recap{recap_suffix}.txt + recap{recap_suffix}.html",
        )

        # 验证
        gen_txt = recap_base + ".txt"
        gen_html = recap_base + ".html"
        for f, name in [(gen_txt, "recap{}.txt"), (gen_html, "recap{}.html")]:
            label = name.format(recap_suffix)
            if not os.path.exists(f):
                print(f"[ERROR] 未生成 {label}")
                sys.exit(1)

        size_txt = os.path.getsize(gen_txt)
        size_html = os.path.getsize(gen_html)
        _safe_print(f"  recap{recap_suffix}.txt  ({size_txt:,} bytes)")
        _safe_print(f"  recap{recap_suffix}.html ({size_html:,} bytes)")
        generated.append((suffix, gen_txt, gen_html))

    # === Step 2: 同步默认 recap.txt/html → 最大后缀 ===
    _safe_print(f"\n{'='*50}\n同步 recap.txt / recap.html")
    sync_default_recap(dir_path)

    # === Step 3: 构建导航索引 ===
    ensure_index_html()
    build_recap_index()

    # === Step 4: Git 操作 ===
    if args.no_commit:
        print("\n[SKIP] 跳过 git 提交（--no-commit）")
        print(f"复盘文件已生成在: {dir_path}")
        return

    rel_dir = os.path.relpath(dir_path, REPO_ROOT)

    # 暂存整个日期目录
    run_cmd(
        ["git", "add", rel_dir],
        description=f"git add {rel_dir}/",
    )

    # 也暂存索引文件和首页（如果在仓库根目录）
    for idx_file in [INDEX_JSON, INDEX_HTML]:
        rel_idx = os.path.relpath(idx_file, REPO_ROOT)
        if os.path.exists(idx_file):
            run_cmd(
                ["git", "add", rel_idx],
                description=f"git add {rel_idx}",
            )

    # 生成提交信息
    if args.msg:
        commit_msg = args.msg
    else:
        commit_msg = f"添加 {date_str[4:6]}/{date_str[6:8]} 复盘数据：recap报告"
        if os.path.exists(premarket):
            commit_msg += "、盘前分析"

    commit_msg += "\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"

    run_cmd(
        ["git", "commit", "-m", commit_msg],
        description="git commit",
    )

    if args.no_push:
        print("\n[SKIP] 跳过推送（--no-push）")
    else:
        run_cmd(
            ["git", "pull", "--rebase", "origin", "master"],
            description="git pull --rebase（同步远程）",
        )
        run_cmd(
            ["git", "push", "origin", "master"],
            description="git push",
        )

    print(f"\n{'='*50}")
    print(f"[DONE] {date_str} 复盘完成！")
    if generated:
        for suffix, txt, html in generated:
            label = f"_{suffix}" if suffix else ""
            print(f"  recap{label}.txt  recap{label}.html")
    if not args.no_push:
        url = "https://huajidepanpan.github.io/a-share-market-recap/"
        print(f"  网页首页: {url}")
        print(f"  日期直达: {url}{rel_dir}/recap.html")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
