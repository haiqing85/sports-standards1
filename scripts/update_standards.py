#!/usr/bin/env python3
"""
体育建设标准全量抓取脚本 - 终极完整版
"""

import json, time, re, os, hashlib
from datetime import datetime
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ─── 配置区 ──────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DATA_FILE = ROOT / 'data' / 'standards.json'

# 放开的体育核心关键词 (增加全部球类及围网等)
KEYWORDS = [
    "体育", "足球", "篮球", "网球", "排球", "乒乓球", "羽毛球", "手球", "棒球", "冰球",
    "台球", "高尔夫", "门球", "壁球", "橄榄球", "曲棍球", 
    "合成材料面层", "塑胶跑道", "合成材料跑道", "聚氨酯跑道", "橡胶面层", "中小学合成材料",
    "人造草坪", "人造草皮", "人工草坪", "颗粒填充料", "草坪填充",
    "体育木地板", "运动木地板", "体育用木质地板", "运动地胶", "PVC运动地板",
    "围网", "运动场围网", "球场围网", "体育围网", "防护网",
    "体育器材", "学校体育器材", "健身器材", "健身路径",
    "游泳场地", "游泳馆", "游泳池水质",
    "足球场地", "篮球场地", "网球场地", "田径场地", "排球场地", "羽毛球场地", "乒乓球场地",
    "体育场地", "运动场地", "体育场馆", "体育建筑", "体育公园", "学校操场"
]

# 严格排除词汇
EXCLUDE_TERMS = ['培训', '照明', '产业', '康养', '户外', '登山', '设施']

# ─── 核心处理逻辑 ─────────────────────────────────────────────
def is_sports(title):
    if not title: return False
    t = title.upper()
    if any(ex in t for ex in EXCLUDE_TERMS): return False
    return any(kw in t for kw in KEYWORDS)

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

def infer_issued_by(code, issue_date):
    cu = re.sub(r'\s+', '', str(code)).upper()
    year = int(str(issue_date)[:4]) if issue_date else 0
    if cu.startswith('GB'):
        if year >= 2018: return '国家市场监督管理总局'
        if year >= 2001: return '国家质量监督检验检疫总局'
        return '国家标准化管理委员会'
    if re.match(r'^(JGJ|JG|CJJ)', cu): return '住房和城乡建设部'
    if cu.startswith('T/SGTAS'): return '中国运动场地联合会'
    if cu.startswith('T/CECS'):  return '中国工程建设标准化协会'
    return ''

def generate_summary(std):
    title = std.get('title', '')
    issued_by = std.get('issuedBy') or '相关权威部门'
    type_str = std.get('type') or '技术标准'
    date_str = std.get('issueDate', '')
    date_txt = f"于 {date_str} " if date_str else ""
    return f"《{title}》是由{issued_by}{date_txt}发布的{type_str}。本标准主要规定了该领域相关的技术要求、质量规范及检验检测方法，为体育建设、场地验收及安全控制提供专业指导和依据。"

def auto_fill_replaces(standards):
    groups = {}
    for s in standards:
        m = re.match(r'^(.+?)\s*[－\-–]\s*(\d{4})$', s.get('code', '').strip())
        if m:
            base = re.sub(r'\s+', '', m.group(1)).upper()
            year = int(m.group(2))
            if base not in groups: groups[base] = []
            groups[base].append({'std': s, 'year': year, 'code': s['code']})

    for base, versions in groups.items():
        if len(versions) < 2: continue
        versions.sort(key=lambda x: x['year'])
        for i, ver in enumerate(versions):
            s = ver['std']
            if i > 0 and not s.get('replaces'): s['replaces'] = versions[i-1]['code']
            if i < len(versions) - 1:
                if not s.get('replacedBy'): s['replacedBy'] = versions[i+1]['code']
                if s.get('status') == '现行': s['status'] = '废止'

def make_session():
    s = requests.Session()
    s.mount('https://', HTTPAdapter(max_retries=Retry(total=3)))
    s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    return s

SESSION = make_session()

# ─── 网络抓取逻辑 ─────────────────────────────────────────────
def fetch_samr(keyword):
    res = []
    try:
        for p in range(1, 6):
            resp = SESSION.post("https://std.samr.gov.cn/gb/search/gbQueryPage",
                                json={"searchText": keyword, "pageSize": 50, "pageIndex": p}, timeout=15)
            rows = resp.json().get('rows') or []
            if not rows: break
            for r in rows:
                title = r.get('STD_NAME') or r.get('C_C_NAME') or ''
                if is_sports(title):
                    res.append({
                        'code': (r.get('STD_CODE') or r.get('C_STD_CODE') or '').strip(),
                        'title': title.strip(), 'type': '国家标准',
                        'status': norm_status(r.get('STATE')),
                        'issueDate': norm_date(r.get('ISSUE_DATE')),
                        'implementDate': norm_date(r.get('ACT_DATE')),
                        'issuedBy': r.get('ISSUE_DEPT') or '',
                        'replaces': r.get('C_SUPERSEDE_CODE') or None,
                        'replacedBy': r.get('C_REPLACED_CODE') or None,
                    })
            if len(rows) < 50: break
            time.sleep(0.5)
    except: pass
    return res

def fetch_ttbz(keyword):
    res = []
    try:
        for p in range(1, 10):
            resp = SESSION.post("https://www.ttbz.org.cn/api/search/standard",
                                json={"keyword": keyword, "pageIndex": p, "pageSize": 30}, timeout=15)
            rows = resp.json().get('Data') or []
            if not rows: break
            for r in rows:
                title = r.get('StdName', '')
                if is_sports(title):
                    res.append({
                        'code': r.get('StdCode'), 'title': title, 'type': '团标',
                        'status': norm_status(r.get('Status')), 'issuedBy': r.get('OrgName'),
                        'issueDate': norm_date(r.get('IssueDate')),
                        'implementDate': norm_date(r.get('ImplementDate'))
                    })
            if len(rows) < 30: break
            time.sleep(0.3)
    except: pass
    return res

def fetch_dbba(keyword):
    res = []
    try:
        for p in range(1, 10):
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
                        'issueDate': norm_date(r.get('publishDate')), 
                        'implementDate': norm_date(r.get('implementDate'))
                    })
            if len(items) < 30: break
            time.sleep(0.3)
    except: pass
    return res

# ─── 主入口 ──────────────────────────────────────────────────
def run():
    print(f"🚀 启动自动化全量抓取 | 关键词数: {len(KEYWORDS)}")
    
    if DATA_FILE.exists():
        with open(DATA_FILE, 'r', encoding='utf-8') as f: db = json.load(f)
        old_standards = {s['code']: s for s in db.get('standards', [])}
    else:
        db, old_standards = {'standards': []}, {}

    for kw in KEYWORDS:
        print(f"🔍 正在检索并深挖: {kw} ...")
        found = fetch_samr(kw) + fetch_ttbz(kw) + fetch_dbba(kw)
        
        for item in found:
            code = item['code']
            if not code: continue
            
            # 自动完善信息
            if not item.get('issuedBy'):
                item['issuedBy'] = infer_issued_by(code, item.get('issueDate'))

            if code not in old_standards:
                entry = {
                    'id': make_id(code),
                    'code': code,
                    'title': item['title'],
                    'type': item.get('type', '标准'),
                    'status': item['status'],
                    'issueDate': item['issueDate'],
                    'implementDate': item['implementDate'],
                    'issuedBy': item['issuedBy'],
                    'category': "综合",
                    'tags': ["体育", "运动场地"],
                    'replaces': item.get('replaces'),
                    'replacedBy': item.get('replacedBy'),
                    'isMandatory': 'GB' in code and '/T' not in code,
                    'localFile': None
                }
                entry['summary'] = generate_summary(entry)
                old_standards[code] = entry
            else:
                # 给老数据补充缺失字段
                if not old_standards[code].get('summary'):
                    old_standards[code]['summary'] = generate_summary(old_standards[code])
                if not old_standards[code].get('issuedBy') and item.get('issuedBy'):
                    old_standards[code]['issuedBy'] = item['issuedBy']

    standards_list = list(old_standards.values())
    auto_fill_replaces(standards_list) # 自动整理新旧标准替换关系

    db['standards'] = standards_list
    db['updated'] = datetime.now().strftime('%Y-%m-%d')
    db['total'] = len(standards_list)
    
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 更新完成！库中总计: {db['total']} 条")

if __name__ == "__main__":
    run()