#!/usr/bin/env python3
import json
import time
import requests
from datetime import datetime
from pathlib import Path

# ============================================================
# 智能路径配置：自动识别环境
# ============================================================
ROOT = Path(__file__).parent.parent
# 尝试多个可能的位置
POSSIBLE_PATHS = [
    ROOT / 'data' / 'standards.json',
    ROOT / '数据' / 'standards.json',
    ROOT / 'standards.json'
]

DATA_FILE = POSSIBLE_PATHS[0] # 默认指向 data/standards.json

for p in POSSIBLE_PATHS:
    if p.exists():
        DATA_FILE = p
        break

SEARCH_KEYWORDS = ["体育场地", "合成材料面层", "人造草坪", "体育馆照明", "运动地板", "塑胶跑道", "体育围网"]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# ============================================================
# 抓取逻辑 (保持不变，但增加了容错)
# ============================================================

def search_samr_national(keyword):
    results = []
    try:
        url = "https://std.samr.gov.cn/gb/search/gbQueryPage"
        payload = {"searchText": keyword, "pageSize": 10, "pageIndex": 1}
        resp = requests.post(url, json=payload, timeout=15, headers=HEADERS)
        if resp.status_code == 200:
            rows = resp.json().get('rows', [])
            for r in rows:
                code = r.get('STD_CODE', '').strip()
                if not code: continue
                results.append({
                    "id": code.replace('/', '').replace(' ', '').replace('-', ''),
                    "code": code,
                    "title": r.get('C_NAME', '').strip(),
                    "type": "国家标准" if code.startswith("GB") else "行业标准",
                    "status": "现行", # 简化处理
                    "issueDate": r.get('ISSUE_DATE', '')[:10] if r.get('ISSUE_DATE') else None,
                    "category": "体育设施",
                    "tags": [keyword]
                })
    except Exception as e:
        print(f"  [!] 抓取国标失败: {e}")
    return results

def update_standards():
    print(f"🚀 启动抓取引擎...")
    
    # 如果文件夹不存在，自动创建
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    # 1. 读取数据 (如果文件不存在，初始化一个空的结构)
    if DATA_FILE.exists():
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
            except:
                data = {"standards": []}
    else:
        print(f"📝 提醒：未找到数据库文件，正在 {DATA_FILE} 创建新库...")
        data = {"standards": []}
    
    existing_standards = data.get('standards', [])
    existing_codes = {s['code'].upper() for s in existing_standards}
    
    new_found = 0

    # 2. 开始抓取
    for kw in SEARCH_KEYWORDS:
        print(f"🔍 正在检索: {kw}...")
        items = search_samr_national(kw)
        for item in items:
            if item['code'].upper() not in existing_codes:
                existing_standards.append(item)
                existing_codes.add(item['code'].upper())
                new_found += 1
                print(f"   ✨ 发现新标准: {item['code']}")
        time.sleep(1)

    # 3. 保存
    today = datetime.now().strftime('%Y-%m-%d')
    data.update({
        "updated": today,
        "version": today.replace('-', '.'),
        "total": len(existing_standards),
        "standards": existing_standards
    })

    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✅ 完成！新增 {new_found} 条。文件保存在: {DATA_FILE}")

if __name__ == '__main__':
    update_standards()
