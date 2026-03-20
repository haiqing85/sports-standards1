#!/usr/bin/env python3
"""
体育建设标准全量抓取脚本 - 终极合并版 (v11)
========================================
- 支持国标、行标、团标、地标四库联动
- 开启自动翻页深挖模式 (每词最高10页)
- 严格关键词过滤逻辑 (排除：培训、照明、康养等)
"""

import json, time, re, os, hashlib, argparse
from datetime import datetime
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ─── 配置区 ──────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DATA_FILE = ROOT / 'data' / 'standards.json'
LOG_FILE = ROOT / 'data' / 'update_log.txt'

# 核心关键词 (放开搜索)
KEYWORDS = [
    "体育", "足球", "篮球", "网球", "排球", "乒乓球", "羽毛球", "手球", "棒球", "冰球",
    "合成材料面层", "塑胶跑道", "合成材料跑道", "聚氨酯跑道", "橡胶面层", "中小学合成材料",
    "人造草坪", "人造草皮", "人工草坪", "颗粒填充料", "草坪填充",
    "体育木地板", "运动木地板", "体育用木质地板", "运动地胶", "PVC运动地板",
    "围网", "运动场围网", "球场围网", "体育围网",
    "体育器材", "学校体育器材", "健身器材",
    "游泳场地", "游泳馆", "游泳池水质",
    "足球场地", "篮球场地", "网球场地", "田径场地", "排球场地", "羽毛球场地", "乒乓球场地",
    "体育场地", "运动场地", "体育场馆建设", "体育建筑设计", "体育公园", "学校操场"
]

# 严格排除词
EXCLUDE_TERMS = ['培训', '照明', '产业', '康养', '户外', '登山', '设施']

# ─── 核心过滤引擎 ─────────────────────────────────────────────
def is_sports(title):
    if not title: return False
    t = title.upper()
    # 1. 命中排除词直接干掉
    if any(ex in t for ex in EXCLUDE_TERMS): return False
    # 2. 必须包含体育核心业务词
    include_logic = [
        '体育', '运动', '比赛', '赛事', '场馆', '健身', '训练', '器材', 
        '场地', '跑道', '草坪', '球场', '木地板', '围网', '游泳', '冰雪',
        '足球', '篮球', '网球', '排球', '乒乓球', '羽毛球', '手球', '棒球', '冰球'
    ]
    return any(term in t for term in include_logic)

# ─── 工具函数 ────────────────────────────────────────────────
def make_id(code):
    return re.sub(r'[^A-Za-z0-9]', '', code)[:30] or hashlib.md5(code.encode()).hexdigest()[:12]

def norm_status(raw):
    raw = str(raw or '').strip()
    if any(x in raw for x in ['现行','有效','执行']): return '现行'
    if any(x in raw for x in ['废止','作废']): return '废止'
    if '即将' in raw or '未实施' in raw: return '即将实施'
    return '现行'

def norm_date(raw):
    if not raw: return None
    raw = str(raw).strip()
    if raw.isdigit() and len(raw) >= 10:
        try: return datetime.fromtimestamp(int(raw[:10])).strftime('%Y-%m-%d')
        except: pass
    c = re.sub(r'[^\d]', '', raw)
    return f"{c[:4]}-{c[4:6]}-{c[6:8]}" if len(c) >= 8 else None

def make_session():
    s = requests.Session()
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    s.mount('https://', HTTPAdapter(max_retries=retries))
    s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
    return s

SESSION = make_session()

def log(msg):
    t = datetime.now().strftime('%H:%M:%S')
    print(f"[{t}] {msg}")
    with open(LOG_FILE, 'a', encoding='utf-8') as f: f.write(f"[{t}] {msg}\n")

# ─── 抓取逻辑 ────────────────────────────────────────────────
def fetch_samr(keyword):
    """国家标准/行业标准 (支持多页)"""
    res = []
    try:
        for p in range(1, 6): # 国标库深挖5页
            resp = SESSION.post("https://std.samr.gov.cn/gb/search/gbQueryPage",
                                json={"searchText": keyword, "pageSize": 50, "pageIndex": p}, timeout=20)
            data = resp.json()
            rows = data.get('rows') or []
            if not rows: break
            for r in rows:
                title = r.get('STD_NAME') or r.get('C_C_NAME') or ''
                if is_sports(title):
                    code = r.get('STD_CODE') or r.get('C_STD_CODE') or ''
                    res.append({
                        'code': code.strip(), 'title': title.strip(), 'type': '国家标准',
                        'status': norm_status(r.get('STATE')),
                        'issueDate': norm_date(r.get('ISSUE_DATE')),
                        'implementDate': norm_date(r.get('ACT_DATE')),
                        'issuedBy': r.get('ISSUE_DEPT') or ''
                    })
            if len(rows) < 50: break
            time.sleep(1)
    except: pass
    return res

def fetch_ttbz(keyword):
    """团体标准 (支持翻页)"""
    res = []
    try:
        for p in range(1, 11): # 团标深挖10页
            resp = SESSION.post("https://www.ttbz.org.cn/api/search/standard",
                                json={"keyword": keyword, "pageIndex": p, "pageSize": 30}, timeout=15)
            rows = resp.json().get('Data') or []
            if not rows: break
            for r in rows:
                title = r.get('StdName', '')
                if is_sports(title):
                    res.append({
                        'code': r.get('StdCode'), 'title': title, 'type': '团标',
                        'status': '现行', 'issuedBy': r.get('OrgName'),
                        'issueDate': norm_date(r.get('IssueDate')),
                        'implementDate': norm_date(r.get('ImplementDate'))
                    })
            if len(rows) < 30: break
            time.sleep(0.5)
    except: pass
    return res

def fetch_dbba(keyword):
    """地方标准 (支持翻页)"""
    res = []
    try:
        for p in range(1, 11): # 地标深挖10页
            resp = SESSION.get('https://dbba.sacinfo.org.cn/api/standard/list',
                               params={"searchText": keyword, "pageSize": 30, "pageNum": p}, timeout=15)
            items = resp.json().get('data', {}).get('list') or []
            if not items: break
            for r in items:
                title = r.get('stdName', '')
                if is_sports(title):
                    res.append({
                        'code': r.get('stdCode'), 'title': title, 'type': '地方标准',
                        'status': norm_status(r.get('status')), 'issuedBy': r.get('publishDept'),
                        'issueDate': norm_date(r.get('publishDate')), 'implementDate': norm_date(r.get('implementDate'))
                    })
            if len(items) < 30: break
            time.sleep(0.5)
    except: pass
    return res

# ─── 主程序 ──────────────────────────────────────────────────
def run():
    log(f"🚀 启动全量更新任务 | 关键词数量: {len(KEYWORDS)}")
    
    # 加载旧数据
    if DATA_FILE.exists():
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            db = json.load(f)
        old_standards = {s['code']: s for s in db.get('standards', [])}
    else:
        db, old_standards = {'standards': []}, {}

    new_count = 0
    for kw in KEYWORDS:
        log(f"🔍 正在检索: {kw} ...")
        # 汇总三方结果
        found = fetch_samr(kw) + fetch_ttbz(kw) + fetch_dbba(kw)
        
        for item in found:
            code = item['code']
            if code not in old_standards:
                # 组装新词条
                entry = {
                    'id': make_id(code),
                    'code': code,
                    'title': item['title'],
                    'type': item['type'],
                    'status': item['status'],
                    'issueDate': item['issueDate'],
                    'implementDate': item['implementDate'],
                    'issuedBy': item['issuedBy'],
                    'category': "综合",
                    'tags': ["体育", "运动场地"],
                    'summary': "",
                    'isMandatory': 'GB' in code and '/T' not in code
                }
                old_standards[code] = entry
                new_count += 1

    # 存盘
    db['standards'] = list(old_standards.values())
    db['updated'] = datetime.now().strftime('%Y-%m-%d')
    db['total'] = len(db['standards'])
    
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    
    log(f"✅ 任务完成！新增: {new_count} 条 | 总计: {db['total']} 条")

if __name__ == "__main__":
    run()