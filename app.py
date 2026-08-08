#!/usr/bin/env python3
"""
家庭资产投资仪表盘 - 价值投资作战面板
纯标准库实现，使用腾讯股票 API 获取实时报价
"""

import http.server
import json
import urllib.request
import urllib.parse
import os
import subprocess
import threading
import time
import re
from datetime import datetime

# ────────────────── neodata API（港股 TTM PE 兜底） ──────────────────
NEODATA_TOKEN_FILE = os.path.expanduser("~/.workbuddy/.neodata_token")
NEODATA_API_URL = "https://copilot.tencent.com/agenttool/v1/neodata"
NEODATA_TOKEN_TTL = 12 * 3600   # 12小时
NEODATA_PE_TTL = 3600           # PE缓存1小时

neodata_pe_cache = {}
neodata_pe_lock = threading.Lock()

PORT = int(os.environ.get("PORT", 8899))

# ────────────────── 股票代码映射 ──────────────────
# 腾讯 API 格式: sh=上海, sz=深圳, hk=港股, us=美股
SYMBOL_TO_QT = {
    "0700.HK": "hk00700",
    "9992.HK": "hk09992",
    "9633.HK": "hk09633",
    "600938.SS": "sh600938",
    "600519.SS": "sh600519",
    "300602.SZ": "sz300602",
    "601869.SS": "sh601869",
    "002384.SZ": "sz002384",
    "PDD": "usPDD",
    "TSLA": "usTSLA",
    "RKLB": "usRKLB",
    "NVDA": "usNVDA",
    "GOOGL": "usGOOGL",
    "AAPL": "usAAPL",
}

QT_TO_SYMBOL = {v: k for k, v in SYMBOL_TO_QT.items()}

ALL_QT_CODES = list(SYMBOL_TO_QT.values())
MARKET_NAMES = {"sh": "上交所", "sz": "深交所", "hk": "港交所", "us": "美股"}

# ────────────────── westock-data PE TTM 获取 ──────────────────
WESOCK_NODE = "/Users/henryhome/.workbuddy/binaries/node/versions/22.22.2/bin/node"
WESOCK_SCRIPT = "/Volumes/WorkBuddy 5.3.5-arm64/WorkBuddy.app/Contents/Resources/app.asar.unpacked/resources/builtin-skills/westock-data/scripts/index.js"

# PE TTM 缓存（TTL 5 分钟，PE 变动不快）
pe_ttm_cache = {}
pe_ttm_lock = threading.Lock()
pe_ttm_last_fetch = 0
PE_TTM_CACHE_TTL = 300  # 5分钟
PE_BLOB_URL = "https://jsonblob.com/api/jsonBlob/019fc5c4-5bb0-7954-ab27-a1e2cc460269"
PE_BLOB_KEEPALIVE = 7200  # 2小时无条件推送一次，防止 blob 过期


def _read_neodata_token():
    """读取 neodata API token，检查是否过期（>12小时）"""
    try:
        with open(NEODATA_TOKEN_FILE, "r") as f:
            data = json.load(f)
        token = data.get("token")
        saved_at = data.get("saved_at", 0)
        if not token:
            return None
        if time.time() - saved_at > NEODATA_TOKEN_TTL:
            print("[NeoData] Token 已过期（>12小时），跳过")
            return None
        return token
    except Exception:
        return None


def _parse_neodata_pe_table(content):
    """解析 neodata 统一估值查询 markdown 表格，提取最新日期的动态市盈率"""
    lines = content.strip().split("\n")
    data_started = False
    for line in lines:
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        if "估值日期" in line or "YYYYMMDD" in line:
            data_started = True
            continue
        if not data_started:
            continue
        # 数据行: | 20260731 | -- | 21.4116 | ...
        parts = [p.strip() for p in line.split("|")]
        parts = [p for p in parts if p]  # 去掉首尾空元素
        if len(parts) >= 3:
            date_str = parts[0]
            pe_str = parts[2]  # 第3列：动态市盈率（倍）
            if re.match(r"^\d{8}$", date_str) and pe_str not in ("", "--", "暂无数据"):
                try:
                    return float(pe_str)
                except ValueError:
                    continue
    return None


def fetch_pe_from_neodata(qt_code):
    """通过 neodata API 获取港股动态市盈率（TTM PE）"""
    # 先查缓存
    with neodata_pe_lock:
        if qt_code in neodata_pe_cache:
            cached_time, cached_pe = neodata_pe_cache[qt_code]
            if time.time() - cached_time < NEODATA_PE_TTL:
                return cached_pe

    token = _read_neodata_token()
    if not token:
        return None

    # 构建查询 — 格式 "09633.HK 动态市盈率"
    code_num = qt_code[2:]
    query = f"{code_num}.HK 动态市盈率"

    try:
        payload = json.dumps({
            "query": query,
            "channel": "neodata",
            "sub_channel": "workbuddy"
        }).encode()
        req = urllib.request.Request(NEODATA_API_URL, data=payload, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}"
            })
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_data = json.loads(resp.read().decode())

        if resp_data.get("code") != "200":
            return None

        api_recall = resp_data.get("data", {}).get("apiData", {}).get("apiRecall", [])
        for recall in api_recall:
            if recall.get("type") == "统一估值查询":
                content = recall.get("content", "")
                pe = _parse_neodata_pe_table(content)
                if pe is not None:
                    with neodata_pe_lock:
                        neodata_pe_cache[qt_code] = (time.time(), pe)
                    return pe
        return None
    except Exception as e:
        print(f"[NeoData] API 调用失败 ({qt_code}): {e}")
        return None


def _parse_quote(qt_code):
    """调用 westock-data quote 并解析关键字段"""
    result = subprocess.run(
        [WESOCK_NODE, WESOCK_SCRIPT, "quote", qt_code],
        capture_output=True, text=True, timeout=15
    )
    lines = result.stdout.strip().split("\n")
    if len(lines) < 3:
        return None
    hdrs = [h.strip() for h in lines[0].split("|")]
    data = [d.strip() for d in lines[2].split("|")]
    out = {}
    for key in ("price", "pe_ratio", "pe_fwd"):
        if key in hdrs:
            idx = hdrs.index(key)
            out[key] = safe_float(data[idx]) if data[idx] not in ("", "?") else None
    return out


def _find_prev_report(rows, idx, report_type):
    """在 idx 之后查找指定类型的上期报表"""
    cur_date = rows[idx]["date"]
    for j in range(idx + 1, len(rows)):
        if rows[j]["type"] == report_type and rows[j]["date"] < cur_date:
            return rows[j]
    return None


def fetch_hk_eps_ttm(qt_code):
    """计算港股 EPS TTM — 从 westock-data finance 获取近4个季度单季 EPS 并加总"""
    try:
        result = subprocess.run(
            [WESOCK_NODE, WESOCK_SCRIPT, "finance", qt_code, "--num", "8"],
            capture_output=True, text=True, timeout=20
        )
        text = result.stdout
        # 切取 zhsy（综合收益）表
        zhsy = text.split("**zhsy**")
        if len(zhsy) < 2:
            return None

        # 解析 markdown 表格（部分单元格含 JSON 可能换行，用日期正则精确匹配行首）
        rows = []
        hdr_map = {}
        for line in zhsy[1].strip().split("\n"):
            line = line.strip()
            if not line.startswith("|") or "---" in line:
                continue
            # 表头行
            if not hdr_map:
                parts = [p.strip() for p in line.split("|")]
                for idx, col in enumerate(parts):
                    if col in ("_date", "EPS", "ReportType"):
                        hdr_map[col] = idx
                continue
            # 数据行 —— 必须以 YYYY-MM-DD 日期开头
            m = re.match(r"\|\s*(\d{4}-\d{2}-\d{2})\s*\|", line)
            if not m:
                continue
            date_val = m.group(1)
            parts = [p.strip() for p in line.split("|")]
            di, ei, ri = hdr_map.get("_date", -1), hdr_map.get("EPS", -1), hdr_map.get("ReportType", -1)
            if di < 0 or ei < 0 or ri < 0:
                continue
            eps_val = safe_float(parts[ei]) if ei < len(parts) else None
            rtype = parts[ri] if ri < len(parts) else ""
            if eps_val is not None and rtype:
                rows.append({"date": date_val, "eps": eps_val, "type": rtype})

        if len(rows) < 4:
            return None

        # 按日期降序（最新在前）
        rows.sort(key=lambda r: r["date"], reverse=True)

        # 从累计报表反推单季 EPS
        single_q = []
        for i, r in enumerate(rows):
            if r["type"] == "第一季报":
                single_q.append(r["eps"])
            elif r["type"] == "中期报告":
                prev = _find_prev_report(rows, i, "第一季报")
                if prev:
                    single_q.append(r["eps"] - prev["eps"])
            elif r["type"] == "第三季报":
                prev = _find_prev_report(rows, i, "中期报告")
                if prev:
                    single_q.append(r["eps"] - prev["eps"])
            elif r["type"] == "年度报告":
                prev = _find_prev_report(rows, i, "第三季报")
                if prev:
                    single_q.append(r["eps"] - prev["eps"])

        if len(single_q) >= 4:
            return sum(single_q[:4])

        # 半年报公司回退：用 (FY_EPS - H1_EPS) + 下一年 H1_EPS 计算 TTM EPS
        # 适用于不发布季报、只有半年报和年报的公司（如农夫山泉 9633.HK）
        fy_rows = [r for r in rows if r["type"] == "年度报告"]
        h1_rows = [r for r in rows if r["type"] == "中期报告"]
        if fy_rows and h1_rows:
            fy_rows.sort(key=lambda r: r["date"], reverse=True)
            h1_rows.sort(key=lambda r: r["date"], reverse=True)
            latest_fy = fy_rows[0]
            fy_year = latest_fy["date"][:4]
            # 匹配同一财年的 H1 数据
            matching_h1 = next((r for r in h1_rows if r["date"].startswith(fy_year)), None)
            if matching_h1:
                h2_eps = latest_fy["eps"] - matching_h1["eps"]
                if h2_eps > 0:
                    # 尝试找下一财年的 H1 数据（当前半年的 EPS）
                    next_year = str(int(fy_year) + 1)
                    next_h1 = next((r for r in h1_rows if r["date"].startswith(next_year)), None)
                    if next_h1:
                        return h2_eps + next_h1["eps"]
                    # 无当期 H1 数据时，用全年 EPS 兜底（等价于 PE 静）
                    return latest_fy["eps"]
        return None
    except Exception:
        return None


def fetch_single_pe_ttm(qt_code, max_retries=2):
    """获取单只股票的 PE TTM"""
    for attempt in range(max_retries):
        try:
            q = _parse_quote(qt_code)
            if not q:
                if attempt < max_retries - 1:
                    time.sleep(0.5)
                    continue
                return None

            pe_ratio = q.get("pe_ratio")
            pe_fwd = q.get("pe_fwd")

            # A股：pe_fwd 是 PE TTM
            if qt_code.startswith(("sh", "sz")):
                return pe_fwd

            # 港股：通过财务数据计算 EPS TTM → PE TTM
            if qt_code.startswith("hk"):
                eps_ttm = fetch_hk_eps_ttm(qt_code)
                price = q.get("price")
                if eps_ttm and eps_ttm > 0 and price:
                    return price / eps_ttm
                # 无财务数据时回退到 PE(静)
                # 注意：neodata 的"动态市盈率"实际是 forward PE（基于分析师预期），
                # 不是 TTM PE，因此不用它作为 PE TTM 的兜底值
                return pe_ratio

            # 美股及其他：使用 pe_ratio
            return pe_ratio
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(0.5)
            else:
                return None
    return None


def fetch_all_pe_ttm():
    """批量获取所有持仓股票的 PE TTM"""
    global pe_ttm_cache, pe_ttm_last_fetch
    now = time.time()
    if (now - pe_ttm_last_fetch) < PE_TTM_CACHE_TTL and pe_ttm_cache:
        return pe_ttm_cache

    results = {}
    for qt_code in ALL_QT_CODES:
        # 只对 A股 和 港股 获取 PE TTM（美股 qt.gtimg.cn 已有 PE TTM）
        if qt_code.startswith(("sh", "sz", "hk")):
            pe_ttm = fetch_single_pe_ttm(qt_code)
            if pe_ttm is not None:
                results[qt_code] = pe_ttm

    with pe_ttm_lock:
        pe_ttm_cache = results
        pe_ttm_last_fetch = now

    # 推送 PE 数据到 jsonblob（供 GitHub Pages 前端使用）
    _push_pe_to_blob(results)

    return results


def _push_pe_to_blob(pe_data):
    """将 PE TTM 数据推送到 jsonblob，供静态前端读取"""
    try:
        payload = json.dumps({"pe_ttm": pe_data, "updated_at": int(time.time()), "version": 1}).encode()
        req = urllib.request.Request(PE_BLOB_URL, data=payload, method="PUT",
            headers={"Content-Type": "application/json", "Accept": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass  # 静默失败，不影响主流程


def push_pe_keepalive():
    """每2小时无条件推送一次 PE 数据，防止 jsonblob 24小时过期"""
    while True:
        time.sleep(PE_BLOB_KEEPALIVE)
        try:
            with pe_ttm_lock:
                if pe_ttm_cache:
                    _push_pe_to_blob(pe_ttm_cache)
        except Exception:
            pass


# ── 汇率获取 ──
fx_cache = {"HKD_CNY": 0.93, "USD_CNY": 7.25}
fx_lock = threading.Lock()
fx_last_fetch = 0
FX_CACHE_TTL = 3600  # 汇率1小时刷新一次


def fetch_exchange_rates():
    """从 exchangerate-api 获取港币/美元兑人民币汇率"""
    global fx_cache, fx_last_fetch
    now = time.time()
    if (now - fx_last_fetch) < FX_CACHE_TTL and fx_cache:
        return fx_cache
    try:
        req = urllib.request.Request(
            "https://api.exchangerate-api.com/v4/latest/CNY",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        rates = data.get("rates", {})
        hkd_cny = safe_float(rates.get("HKD"))
        usd_cny = safe_float(rates.get("USD"))
        if hkd_cny and usd_cny:
            with fx_lock:
                fx_cache = {
                    "HKD_CNY": round(1.0 / hkd_cny, 4),
                    "USD_CNY": round(1.0 / usd_cny, 4)
                }
                fx_last_fetch = now
            return fx_cache
    except Exception:
        pass
    return fx_cache


# ────────────────── 持仓数据 ──────────────────
PORTFOLIO = {
    "total_assets": 12180606,
    "liquid_assets": 7523606,
    "updated": "2026-07-27",
    "attack": {
        "total": 3529551,
        "ratio": 28.98,
        "assets": [
            {
                "category": "A股 & 港股",
                "total_value": 1269599,
                "ratio": 16.87,
                "holdings": [
                    {"name": "腾讯控股", "symbol": "0700.HK", "market": "HK", "value": 562980, "ratio": 7.48, "cost": 488.00, "note": "核心持仓", "tags": []},
                    {"name": "泡泡玛特", "symbol": "9992.HK", "market": "HK", "value": 416859, "ratio": 5.54, "cost": 175.00, "note": "", "tags": []},
                    {"name": "E大长赢", "symbol": None, "market": "CN", "value": 164622, "ratio": 2.19, "cost": None, "note": "投顾产品", "tags": ["投顾"]},
                    {"name": "E大长赢（新）", "symbol": None, "market": "CN", "value": 91973, "ratio": 1.22, "cost": None, "note": "投顾产品", "tags": ["投顾"]},
                    {"name": "长飞光纤", "symbol": "601869.SS", "market": "CN", "value": 33165, "ratio": 0.44, "cost": 330.14, "note": "短期交易", "tags": ["短期"]},
                ],
                "watchlist": [
                    {"name": "农夫山泉", "symbol": "9633.HK", "market": "HK", "target_pe": None, "target_price": None, "note": ""},
                    {"name": "中国海油", "symbol": "600938.SS", "market": "CN", "target_pe": None, "target_mcap": "1.4-1.7万亿市值", "note": ""},
                    {"name": "贵州茅台", "symbol": "600519.SS", "market": "CN", "target_pe": None, "target_price": None, "note": ""},
                    {"name": "飞荣达", "symbol": "300602.SZ", "market": "CN", "target_pe": "40-60", "target_price": None, "note": "短期"},
                    {"name": "东山精密", "symbol": "002384.SZ", "market": "CN", "target_pe": "40-80", "target_price": None, "note": "短期"},
                ]
            },
            {
                "category": "美股",
                "total_value": 259952,
                "ratio": 3.45,
                "holdings": [
                    {"name": "拼多多", "symbol": "PDD", "market": "US", "value": 127127, "ratio": 1.69, "cost": 127.53, "note": "", "tags": []},
                    {"name": "特斯拉", "symbol": "TSLA", "market": "US", "value": 83922, "ratio": 1.12, "cost": 339.25, "note": "风险投资", "tags": ["风投"]},
                    {"name": "ROCKET LAB", "symbol": "RKLB", "market": "US", "value": 48903, "ratio": 0.65, "cost": 77.60, "note": "风险投资", "tags": ["风投"]},
                ],
                "watchlist": [
                    {"name": "英伟达", "symbol": "NVDA", "market": "US", "target_pe": None, "target_price": None, "note": ""},
                    {"name": "谷歌", "symbol": "GOOGL", "market": "US", "target_pe": None, "target_price": None, "note": ""},
                    {"name": "苹果", "symbol": "AAPL", "market": "US", "target_pe": None, "target_price": None, "note": ""},
                    {"name": "SPACE X", "symbol": None, "market": "PRIVATE", "target_pe": None, "target_price": None, "note": "未上市"},
                ]
            },
            {
                "category": "FCN",
                "total_value": 2000000,
                "ratio": 26.58,
                "fcn_contracts": [
                    {"id": "No.1", "underlying": "腾讯控股", "symbol": "0700.HK", "value": 1000000, "ratio": 13.29, "strike": 421.00, "coupon_rate": 1.9177, "expiry": "2026-08-06", "cost": 421.00},
                    {"id": "No.2", "underlying": "腾讯控股", "symbol": "0700.HK", "value": 1000000, "ratio": 13.29, "strike": 447.00, "coupon_rate": 2.0271, "expiry": "2026-09-01", "cost": 447.00},
                ]
            }
        ]
    },
    "defense": {
        "total": 3994055,
        "ratio": 32.79,
        "assets": [
            {"name": "嘉实固收+ 1号", "value": 714600, "ratio": 9.50, "type": "固收"},
            {"name": "嘉实固收+ 6号", "value": 361129, "ratio": 4.80, "type": "固收"},
            {"name": "实物黄金（300g）", "value": 270000, "ratio": 3.59, "type": "黄金"},
            {"name": "货币基金 - 我", "value": 126819, "ratio": 1.69, "type": "现金"},
            {"name": "货币基金 - 妈", "value": 1600000, "ratio": 21.27, "type": "现金"},
            {"name": "珠海公积金", "value": 167339, "ratio": 2.22, "type": "现金"},
            {"name": "北京公积金", "value": 624168, "ratio": 8.30, "type": "现金"},
            {"name": "启明投资收益", "value": 130000, "ratio": 1.73, "type": "现金"},
        ]
    },
    "illiquid": {
        "name": "启明风投（未退出）",
        "value": 4657000,
        "ratio": 38.23,
        "note": "非流动性资产，不计入可配置资产"
    }
}


def parse_tencent_quote(raw_text):
    """解析腾讯股票 API 返回数据"""
    results = {}
    lines = raw_text.strip().split("\n")
    for line in lines:
        line = line.strip()
        if not line or "=" not in line:
            continue
        try:
            # v_usPDD="200~拼多多~..."
            key, val = line.split("=", 1)
            val = val.strip('";')
            fields = val.split("~")
            if len(fields) < 40:
                continue

            qt_code = key[2:]  # 去掉 v_ 前缀
            symbol = QT_TO_SYMBOL.get(qt_code, qt_code)

            # 统一字段解析（腾讯API各市场字段位置基本一致）
            name = fields[1]
            price = safe_float(fields[3])
            prev_close = safe_float(fields[4])
            open_price = safe_float(fields[5])
            high = safe_float(fields[33]) if len(fields) > 33 else None
            low = safe_float(fields[34]) if len(fields) > 34 else None
            pe = safe_float(fields[39]) if len(fields) > 39 else None
            market_cap = safe_float(fields[44]) if len(fields) > 44 else None
            volume = safe_float(fields[6]) if len(fields) > 6 else None
            change = safe_float(fields[31]) if len(fields) > 31 else None  # will fix
            change_pct = safe_float(fields[32]) if len(fields) > 32 else None

            # 货币判断
            if qt_code.startswith("hk"):
                currency = "HKD"
            elif qt_code.startswith("us"):
                currency = "USD"
            else:
                currency = "CNY"

            results[symbol] = {
                "name": name,
                "price": price,
                "prev_close": prev_close,
                "open": open_price,
                "high": high,
                "low": low,
                "pe": pe,
                "pe_ttm": None,  # 稍后由 fetch_all_pe_ttm 填充
                "market_cap": market_cap,
                "volume": volume,
                "currency": currency,
                "change_pct": round((price - prev_close) / prev_close * 100, 2) if price and prev_close and prev_close != 0 else None,
            }
        except Exception as e:
            print(f"[Parse] 解析失败: {line[:80]}... {e}")
            continue
    return results


def safe_float(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def fetch_all_quotes():
    """从腾讯 API 批量获取股票报价"""
    codes_str = ",".join(ALL_QT_CODES)
    url = f"http://qt.gtimg.cn/q={codes_str}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.qq.com"
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
            # 尝试 GBK 解码
            try:
                text = raw.decode("gbk")
            except:
                text = raw.decode("utf-8", errors="replace")
            return parse_tencent_quote(text)
    except Exception as e:
        print(f"[Fetcher] 获取报价失败: {e}")
        return {}


# 缓存
price_cache = {}
cache_lock = threading.Lock()
last_fetch_time = 0
CACHE_TTL = 30  # 30秒缓存


def get_prices(force=False):
    global price_cache, last_fetch_time
    now = time.time()
    if not force and (now - last_fetch_time) < CACHE_TTL and price_cache:
        return price_cache
    quotes = fetch_all_quotes()

    # 注入 PE TTM 数据（独立缓存，5分钟刷新）
    pe_ttm_data = fetch_all_pe_ttm()
    for symbol, quote in quotes.items():
        qt_code = SYMBOL_TO_QT.get(symbol)
        if qt_code and qt_code in pe_ttm_data:
            quote["pe_ttm"] = pe_ttm_data[qt_code]
            quote["pe"] = pe_ttm_data[qt_code]   # 用 westock-data 的 PE 覆盖 qt.gtimg.cn 的 PE（更准确）

    with cache_lock:
        price_cache = quotes
        last_fetch_time = now
    return price_cache


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def send_html(self, content, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content.encode("utf-8"))

    def do_GET(self):
        self._handle_request()

    def do_OPTIONS(self):
        """CORS 预检请求"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def _handle_request(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/api/portfolio":
            self.send_json(PORTFOLIO)

        elif path == "/api/prices":
            force = qs.get("force", ["0"])[0] == "1"
            prices = get_prices(force=force)
            self.send_json(prices)

        elif path == "/api/ping":
            self.send_json({
                "status": "ok",
                "time": datetime.now().isoformat(),
                "quotes_cached": len(price_cache)
            })

        elif path == "/api/search":
            keyword = qs.get("q", [""])[0]
            if not keyword or len(keyword) < 1:
                self.send_json({"results": []})
                return
            try:
                req = urllib.request.Request(
                    f"https://smartbox.gtimg.cn/s3/?q={urllib.parse.quote(keyword)}&t=all",
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com"}
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    text = resp.read().decode("utf-8", errors="replace")
                # 解析结果: v_hint="market~code~name~pinyin~type^..."
                match = re.search(r'v_hint="([^"]*)"', text)
                results = []
                if match:
                    seen = set()
                    for entry in match.group(1).split("^"):
                        if not entry.strip():
                            continue
                        fields = entry.split("~")
                        if len(fields) < 5:
                            continue
                        market, code, raw_name, _, typ = fields[0], fields[1], fields[2], fields[3], fields[4]
                        if not typ or (not typ.startswith("GP") and typ != "GP"):
                            continue
                        # 构建 symbol
                        mkt = market.upper()
                        if mkt == "HK":
                            symbol = str(int(code)).zfill(4) + ".HK"
                        elif mkt == "SH":
                            symbol = code + ".SS"
                        elif mkt == "SZ":
                            symbol = code + ".SZ"
                        elif mkt == "US":
                            dot = code.find(".")
                            symbol = (code[:dot] if dot > 0 else code).upper()
                        else:
                            continue
                        if symbol in seen:
                            continue
                        seen.add(symbol)
                        # Unicode 反转义
                        name = raw_name
                        try:
                            name = raw_name.encode().decode("unicode_escape")
                        except:
                            pass
                        results.append({"name": name, "symbol": symbol, "market": mkt})
                        if len(results) >= 15:
                            break
                self.send_json({"results": results})
            except Exception as e:
                self.send_json({"results": [], "error": str(e)})

        elif path in ("/", "/index.html"):
            # 优先使用 index.html，回退到模板
            for fname in ("index.html", "templates/dashboard.html"):
                html_path = os.path.join(os.path.dirname(__file__), fname)
                if os.path.exists(html_path):
                    with open(html_path, "r", encoding="utf-8") as f:
                        html = f.read()
                    # 注入实时股价数据，页面首次加载即显示价格
                    prices_now = get_prices()
                    prices_json = json.dumps(prices_now, ensure_ascii=False)
                    html = html.replace(
                        "let prices = {};",
                        f"let prices = {prices_json};"
                    )
                    self.send_html(html)
                    return
            self.send_html("<h1>仪表盘模板未找到</h1>", 404)

        else:
            self.send_json({"error": "not found"}, 404)


def run_server():
    server = http.server.HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"🚀 投资仪表盘已启动 → http://127.0.0.1:{PORT}")
    # 启动 PE 数据 keepalive 线程（每2小时推送到 jsonblob 防止过期）
    threading.Thread(target=push_pe_keepalive, daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    print("📡 预热股价缓存（腾讯API）...")
    quotes = get_prices(force=True)
    print(f"✅ 已获取 {len(quotes)} 只股票的实时报价")
    for sym, q in quotes.items():
        if q.get("price"):
            print(f"   {q['name']:8s}  {q.get('currency','?'):3s} {q['price']:>10.2f}  PE={q.get('pe','?')}")
    run_server()
