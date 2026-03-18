#!/usr/bin/env python3
"""
体育建设标准全自动抓取与核查引擎 (2026 增强版)
覆盖范围：全国标准信息公共服务平台 (国标/行标)、地方标准数据库、全国团标平台
功能：
  1. 状态核查：遍历本地标准，发现“已废止”则自动更新状态。
  2. 新增抓取：通过核心业务关键词，定期广搜三大平台，自动补充新颁布的体育相关标准。
"""

import json
import time
import uuid
import requests
from datetime import datetime
from pathlib import Path

# ============================================================
# 配置区
# ============================================================
DATA_FILE = Path(__file__).parent.parent / 'data' / 'standards.json'
SEARCH_KEYWORDS = ["体育场地", "合成材料面层", "人造草坪", "体育馆照明", "运动地板", "塑胶跑道", "体育围网", "健身器材"]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*'
}

# 建立带重试机制的 Session
session = requests.Session()
adapter = requests.adapters.HTTPAdapter(max_retries=3)
session.mount('https://', adapter)
session.mount('http://', adapter)
session.headers.update(HEADERS)

# ============================================================
# 抓取接口实现
# ============================================================

def search_samr_national(keyword):
    """抓取国标/行标 (全国标准信息公共服务平台)"""
    results = []
    try:
        url = "https://std.samr.gov.cn/gb/search/gbQueryPage"
        payload = {"searchText": keyword, "pageSize": 10, "pageIndex": 1}
        resp = session.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            rows = resp.json().get('rows', [])
            for r in rows:
                code = r.get('STD_CODE', '').strip()
                if not code: continue
                # STANDARD_STATE 通常为 1(现行), 2(废止) 或中文
                raw_status = str(r.get('STANDARD_STATE', '现行'))
                status = "废止" if "废止" in raw_status or raw_status == "2" else "现行"
                
                results.append({
                    "id": code.replace('/', '').replace(' ', '').replace('-', ''),
                    "code": code,
                    "title": r.get('C_NAME', '').strip(),
                    "english": r.get('E_NAME', '').strip(),
                    "type": "国家标准" if code.startswith("GB") else "行业标准",
                    "status": status,
                    "issueDate": r.get('ISSUE_DATE', '')[:10] if r.get('ISSUE_DATE') else None,
                    "implementDate": r.get('EXECUTE_DATE', '')[:10] if r.get('EXECUTE_DATE') else None,
                    "issuedBy": "国家市场监督管理总局" if code.startswith("GB") else "",
                    "summary": f"由 {r.get('PUBLISH_ORG', '主管部门')} 发布的{keyword}相关标准。",
                    "isMandatory": code.startswith("GB ") and not code.startswith("GB/T"),
                    "localFile": None
                })
    except Exception as e:
        print(f"  [Error] 抓取国标失败 ({keyword}): {e}")
    return results

def search_ttbz_group(keyword):
    """抓取团体标准 (全国团体标准信息平台)"""
    results = []
    try:
        url = "https://www.ttbz.org.cn/api/search/standard"
        payload = {"keyword": keyword, "pageIndex": 1, "pageSize": 10}
        resp = session.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            rows = resp.json().get('Data', [])
            for r in rows:
                code = r.get('StdCode', '').strip()
                if not code: continue
                status = "废止" if "废止" in r.get('Status', '') else "现行"
                
                results.append({
                    "id": code.replace('/', '').replace(' ', '').replace('-', ''),
                    "code": code,
                    "title": r.get('ChnName', '').strip(),
                    "type": "团标",
                    "status": status,
                    "issueDate": r.get('PubDate', '')[:10] if r.get('PubDate') else None,
                    "implementDate": r.get('ImpDate', '')[:10] if r.get('ImpDate') else None,
                    "issuedBy": r.get('SocName', ''),
                    "summary": f"由 {r.get('SocName', '相关团体')} 发布的{keyword}团体标准。",
                    "isMandatory": False,
                    "localFile": None
                })
    except Exception as e:
        print(f"  [Error] 抓取团标失败 ({keyword}): {e}")
    return results

def search_dbba_local(keyword):
    """抓取地方标准 (地方标准数据库)"""
    results = []
    try:
        url = "https://dbba.sacinfo.org.cn/api/standard/list"
        params = {"searchText": keyword, "pageSize": 10, "pageNum": 1}
        resp = session.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json().get('data', {}).get('list', [])
            for r in data:
                code = r.get('stdCode', '').strip()
                if not code: continue
                status = "废止" if "废止" in r.get('status', '') else "现行"
                
                results.append({
                    "id": code.replace('/', '').replace(' ', '').replace('-', ''),
                    "code": code,
                    "title": r.get('cname', '').strip(),
                    "type": "地方标准",
                    "status": status,
                    "issueDate": r.get('publishDate', '')[:10] if r.get('publishDate') else None,
                    "implementDate": r.get('implementDate', '')[:10] if r.get('implementDate') else None,
                    "issuedBy": "地方市场监督管理局",
                    "summary": f"地方颁布的关于{keyword}的技术标准。",
                    "isMandatory": code.startswith("DB") and not code.startswith("DB/T"),
                    "localFile": None
                })
    except Exception as e:
        print(f"  [Error] 抓取地标失败 ({keyword}): {e}")
    return results

# ============================================================
# 主逻辑
# ============================================================
def update_standards():
    print(f"\n{'='*55}")
    print(f"🚀 体育标准全自动抓取引擎启动")
    print(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*55}\n")

    # 1. 读取本地库
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    existing_standards = data.get('standards', [])
    existing_codes = {s['code'].strip().upper() for s in existing_standards}
    
    new_found = 0
    status_updated = 0

    # 2. 遍历关键词广搜全网标准
    print("🔍 阶段一：全网检索新标准...")
    for kw in SEARCH_KEYWORDS:
        print(f"  > 正在搜索关键词: [{kw}]")
        fetched_items = []
        fetched_items.extend(search_samr_national(kw))
        fetched_items.extend(search_ttbz_group(kw))
        fetched_items.extend(search_dbba_local(kw))
        
        for item in fetched_items:
            code_upper = item['code'].upper()
            
            # 判断是否是新标准
            if code_upper not in existing_codes:
                # 补全默认缺失字段
                item['abolishDate'] = None
                item['replaces'] = None
                item['replacedBy'] = None
                item['category'] = "综合" if kw not in item['title'] else kw # 粗略分类
                item['tags'] = [kw, item['type']]
                
                existing_standards.append(item)
                existing_codes.add(code_upper)
                new_found += 1
                print(f"    ✨ 发现新标准: {item['code']} - {item['title'][:15]}...")
            
            # 判断本地是否需要更新状态 (例如从现行变更为废止)
            else:
                for local_std in existing_standards:
                    if local_std['code'].upper() == code_upper:
                        if local_std['status'] != item['status'] and item['status'] == "废止":
                            local_std['status'] = "废止"
                            local_std['abolishDate'] = datetime.now().strftime('%Y-%m-%d')
                            status_updated += 1
                            print(f"    🔄 状态更新: {local_std['code']} 已废止！")
                        break
        
        time.sleep(2) # 防封控延时

    # 3. 保存回写 JSON
    today = datetime.now().strftime('%Y-%m-%d')
    data['updated'] = today
    data['version'] = today.replace('-', '.')
    data['total'] = len(existing_standards)
    
    # 按照发布日期倒序排序（最新的在最前）
    data['standards'] = sorted(
        existing_standards, 
        key=lambda x: x.get('issueDate') or '1970-01-01', 
        reverse=True
    )

    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 抓取与核验完成！")
    print(f"   - 库内标准总数: {len(existing_standards)}")
    print(f"   - 本次新增收录: {new_found} 条")
    print(f"   - 本次状态更新: {status_updated} 条")

if __name__ == '__main__':
    update_standards()