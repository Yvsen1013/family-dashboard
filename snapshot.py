#!/usr/bin/env python3
"""每日资产快照脚本 — GitHub Actions 每天 17:00 运行"""
import json, urllib.request, os, datetime

# 资产数据（与 index.html 同步）
ATTACK_TOTAL = 3529551
DEFENSE_TOTAL = 3994055
ILLIQUID = 4657000
GOLD_GRAM = 300
# 防守资产中黄金静态值 270000，其余为固定现金

# 腾讯行情 API
def fetch_price(qt_code):
    url = f"https://qt.gtimg.cn/q={qt_code}"
    req = urllib.request.Request(url)
    resp = urllib.request.urlopen(req, timeout=10)
    raw = resp.read()
    try:
        text = raw.decode("gbk")
    except:
        text = raw.decode("gb2312", errors="replace")
    for line in text.split("\n"):
        m = line.split('="')
        if len(m) < 2: continue
        f = m[1].strip('";\n').split("~")
        if len(f) < 40: continue
        try:
            return {"price": float(f[3]), "prev_close": float(f[4])}
        except:
            return None
    return None

# 获取黄金ETF价格
gold = fetch_price("sh518880")
if gold and gold["prev_close"]:
    au_per_gram = gold["prev_close"] * 100  # ETF价格×100 ≈ Au99.99/g
else:
    au_per_gram = 900
gold_value = round(GOLD_GRAM * au_per_gram * 1.08)  # 银行金条溢价8%

defense_dynamic = DEFENSE_TOTAL - 270000 + gold_value
total = ATTACK_TOTAL + defense_dynamic + ILLIQUID

today = datetime.date.today().isoformat()

# 读取现有快照
snap_path = os.path.join(os.path.dirname(__file__), "data", "snapshots.json")
snapshots = []
if os.path.exists(snap_path):
    with open(snap_path, "r") as f:
        snapshots = json.load(f)

# 检查今日是否已记录
if snapshots and snapshots[-1]["date"] == today:
    print(f"今日({today})已记录，跳过")
else:
    snapshots.append({
        "date": today,
        "total": round(total),
        "attack": ATTACK_TOTAL,
        "defense": defense_dynamic,
        "illiquid": ILLIQUID
    })
    with open(snap_path, "w") as f:
        json.dump(snapshots, f, ensure_ascii=False, indent=2)
    print(f"已记录 {today}: ¥{total:,}")
