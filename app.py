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
import threading
import time
import re
from datetime import datetime

PORT = 8899

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
    server.serve_forever()


if __name__ == "__main__":
    print("📡 预热股价缓存（腾讯API）...")
    quotes = get_prices(force=True)
    print(f"✅ 已获取 {len(quotes)} 只股票的实时报价")
    for sym, q in quotes.items():
        if q.get("price"):
            print(f"   {q['name']:8s}  {q.get('currency','?'):3s} {q['price']:>10.2f}  PE={q.get('pe','?')}")
    run_server()
