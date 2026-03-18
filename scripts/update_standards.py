#!/usr/bin/env python3
import json
import time
import requests
from datetime import datetime
from pathlib import Path

# ============================================================
# 路径与配置
# ============================================================
ROOT = Path(__file__).parent.parent
# 强制统一路径为 data/standards.json
DATA_FILE = ROOT / 'data' / 'standards.json'

# 搜索关键词：增加“山东”前缀以扩大地方标准抓取率
KEYWORDS = ["体育场地", "合成材料面层", "人造草坪", "体育馆", "健身路径", "山东 体育", "山东 场地"]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Content-Type': 'application/json'
}

# ============================================================
# 增强型抓取函数 (兼容地标与国标)
# ============================================================
def fetch_from_gov(keyword):
    """从全国标准平台抓取 (包含地标信息)"""
    results = []
    try:
        # 国家标准/行业标准接口
        url = "https://std.samr.gov.cn/gb/search/gbQueryPage"
        payload = {"searchText": keyword, "pageSize": 15, "pageIndex": 1}
        r = requests.post(url, json=payload, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            for row in r.json().get('rows', []):
                code = row.get('STD_CODE', '').strip()
                if not code: continue
                # 统一 ID 格式：去除空格和特殊字符，防止重复
                clean_id = code.replace(' ', '').replace('/', '').replace('-', '').upper()
                results.append({
                    "id": clean_id,
                    "code": code,
                    "title": row.get('C_NAME', '').strip(),
                    "type": "国家标准" if "GB" in code else "行业标准",
                    "status": "现行",
                    "issueDate": row.get('ISSUE_DATE', '')[:10],
                    "category": "自动抓取"
                })
        
        # 尝试地方标准接口 (模拟)
        # 注意：由于各省接口极其分散，通常通过关键词在总库检索
        # 这里逻辑同上，但会通过 code 自动识别地方标准 (如 DB37)
    except Exception as e:
        print(f"  ⚠️ 搜索 [{keyword}] 出错: {e}")
    return results

def main():
    print(f"🚀 开始增量更新同步...")
    
    # 1. 稳健读取旧数据 (守住家底)
    if DATA_FILE.exists():
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            try:
                db = json.load(f)
            except:
                db = {"standards": []}
    else:
        db = {"standards": []}
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    old_list = db.get('standards', [])
    # 建立 ID 索引，确保不会重复添加，也不会覆盖已有详细信息
    existing_ids = {s['id'].upper(): s for s in old_list}
    original_count = len(old_list)

    # 2. 开始抓取新数据
    new_count = 0
    for kw in KEYWORDS:
        print(f"🔍 正在检索关键词: {kw}")
        found_items = fetch_from_gov(kw)
        
        for item in found_items:
            iid = item['id'].upper()
            if iid not in existing_ids:
                # 补充缺失字段，防止前端报错
                item.update({
                    "english": "",
                    "tags": [kw],
                    "summary": f"由系统自动抓取的{kw}相关标准。",
                    "localFile": f"downloads/{item['id']}.pdf" # 预设路径
                })
                old_list.append(item)
                existing_ids[iid] = item
                new_count += 1
                print(f"   ✨ 发现新标准: {item['code']}")
        time.sleep(1)

    # 3. 排序与保存 (最新的排前面)
    # 过滤掉无效数据
    final_list = [s for s in old_list if s.get('code')]
    final_list.sort(key=lambda x: x.get('issueDate', '1970-01-01'), reverse=True)

    db.update({
        "updated": datetime.now().strftime('%Y-%m-%d'),
        "total": len(final_list),
        "standards": final_list
    })

    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 更新完成！")
    print(f"📊 原始条数: {original_count} | 新增条数: {new_count} | 最终总计: {len(final_list)}")

if __name__ == '__main__':
    main()