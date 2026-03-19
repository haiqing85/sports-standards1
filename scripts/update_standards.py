#!/usr/bin/env python3
"""
体育建设标准自动抓取 & 更新脚本 v3
======================================
数据来源（全面覆盖）：
  1. 全国标准信息公共服务平台  https://std.samr.gov.cn     → 国标/行标/地标
  2. 全国团体标准信息平台      https://www.ttbz.org.cn     → 团标 T/
  3. 地方标准数据库            https://dbba.sacinfo.org.cn  → 地方标准

特性：
  - 数据库为空时自动完整抓取（不依赖已有数据）
  - 与现有数据智能对比合并，只补充缺失内容
  - 自动核查并更新废止状态
  - 健壮的错误处理，接口失败自动换备用接口

运行方式：
  python scripts/update_standards.py           # 完整抓取（数据库为空也可用）
  python scripts/update_standards.py --check   # 仅核查现有标准状态
  python scripts/update_standards.py --dry     # 预览模式，不写入文件
"""

import json, time, re, argparse, hashlib
from datetime import datetime
from pathlib import Path

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    raise SystemExit("请安装依赖: pip install requests urllib3")

ROOT      = Path(__file__).parent.parent
DATA_FILE = ROOT / 'data' / 'standards.json'
LOG_FILE  = ROOT / 'data' / 'update_log.txt'

KEYWORDS = [
    "合成材料面层", "合成材料跑道", "塑胶跑道",
    "人造草坪", "人造草", "运动场人造草",
    "体育照明", "体育场馆照明", "运动场照明",
    "体育木地板", "运动木地板", "弹性地板", "PVC运动地板",
    "体育围网", "运动场围网",
    "室外健身器材", "健身路径",
    "体育场地", "运动场地", "体育场馆", "体育建筑",
    "体育设施", "体育公园", "全民健身",
    "颗粒填充料", "橡胶颗粒",
    "足球场地", "篮球场地", "网球场地", "田径场地",
    "游泳场地", "排球场地", "羽毛球场地", "乒乓球场地",
    "学校操场", "中小学运动场地", "学校体育器材",
    "体育场地施工", "运动场地验收",
    "体育建筑设计", "体育设施建设",
]

SPORTS_KW = [
    "体育", "运动", "健身", "竞技", "跑道", "操场", "球场", "场馆",
    "合成材料", "人造草", "草坪", "塑胶", "围网", "木地板", "PVC",
    "弹性地板", "颗粒", "游泳", "篮球", "足球", "网球", "排球",
    "羽毛球", "田径", "乒乓", "体操", "健身器材", "灯光",
]

CATEGORY_MAP = {
    "合成材料": "合成材料面层", "塑胶跑道": "合成材料面层",
    "人造草": "人造草坪", "草坪": "人造草坪",
    "照明": "灯光照明", "灯光": "灯光照明",
    "木地板": "木地板",
    "PVC": "PVC运动地胶", "弹性地板": "PVC运动地胶", "地胶": "PVC运动地胶",
    "围网": "围网",
    "健身器材": "健身路径", "健身路径": "健身路径",
    "体育器材": "体育器材",
    "颗粒": "颗粒填充料", "橡胶颗粒": "颗粒填充料",
    "游泳": "游泳场地",
    "建筑": "场地设计", "设计规范": "场地设计",
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}

def make_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429,500,502,503,504])
    s.mount('https://', HTTPAdapter(max_retries=retry))
    s.mount('http://', HTTPAdapter(max_retries=retry))
    s.headers.update(HEADERS)
    return s

SESSION = make_session()

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass

def make_id(code):
    clean = re.sub(r'[^A-Za-z0-9]', '', code)
    return clean[:30] if clean else hashlib.md5(code.encode()).hexdigest()[:12]

def norm_code(c):
    return re.sub(r'\s+', '', c).upper()

def guess_type(code):
    cu = code.upper()
    for prefix, t in [("GB/T","国家标准"),("GB ","国家标准"),("JGJ","行业标准"),
                       ("JG/T","行业标准"),("CJJ","行业标准"),("T/","团标"),("DB","地方标准")]:
        if cu.startswith(prefix.upper().strip()):
            return t
    return "国家标准"

def guess_category(text):
    for kw, cat in CATEGORY_MAP.items():
        if kw in text:
            return cat
    return "综合"

def guess_tags(text):
    candidates = ["体育","运动","塑胶","合成材料","人造草","照明","木地板","PVC",
                  "围网","健身","器材","颗粒","游泳","篮球","足球","网球","田径",
                  "排球","羽毛球","跑道","场地","操场","中小学","学校","安全"]
    return [t for t in candidates if t in text][:6]

def is_sports(text):
    return any(kw in text for kw in SPORTS_KW)

def norm_status(raw):
    raw = str(raw or '').strip()
    if any(x in raw for x in ['现行','有效','执行','施行']): return '现行'
    if any(x in raw for x in ['废止','作废','撤销','废弃']): return '废止'
    if any(x in raw for x in ['即将','待实施','未实施']):    return '即将实施'
    return '现行'

def norm_date(raw):
    if not raw: return None
    raw = str(raw).strip()
    if raw.isdigit() and len(raw) >= 10:
        try:
            ts = int(raw)
            if ts > 1e11: ts //= 1000
            return datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
        except Exception: pass
    cleaned = re.sub(r'[^\d]', '', raw)
    if len(cleaned) >= 8:
        return f"{cleaned[:4]}-{cleaned[4:6]}-{cleaned[6:8]}"
    return None

def is_mandatory(code):
    c = norm_code(code)
    if re.match(r'^GB\d', c) and '/T' not in c: return True
    if c.startswith('JGJ'): return True
    return False

def build_entry(item):
    code, title = item.get('code',''), item.get('title','')
    return {
        'id':            make_id(code),
        'code':          code,
        'title':         title,
        'english':       '',
        'type':          item.get('type') or guess_type(code),
        'status':        item.get('status', '现行'),
        'issueDate':     item.get('issueDate') or None,
        'implementDate': item.get('implementDate') or None,
        'abolishDate':   item.get('abolishDate') or None,
        'replaces':      None,
        'replacedBy':    None,
        'issuedBy':      item.get('issuedBy', ''),
        'category':      guess_category(title),
        'tags':          guess_tags(title),
        'summary':       item.get('summary') or f"规定了{title}的技术要求、试验方法及相关规定。",
        'isMandatory':   item.get('isMandatory', is_mandatory(code)),
        'scope':         f"适用于{title}相关场合",
        'localFile':     f"downloads/{make_id(code)}.pdf",
    }

def fetch_samr(keyword):
    results = []
    urls = [
        ("https://std.samr.gov.cn/gb/search/gbQueryPage",
         {"searchText": keyword, "status": "", "sortField": "ISSUE_DATE",
          "sortType": "desc", "pageSize": 50, "pageIndex": 1}),
        ("https://std.samr.gov.cn/search/std",
         {"keyword": keyword, "pageSize": 50, "pageNum": 1}),
    ]
    for url, payload in urls:
        try:
            resp = SESSION.post(url, json=payload, timeout=20)
            if not resp.ok: continue
            data = resp.json()
            rows = (data.get('rows') or data.get('data',{}).get('rows',[])
                    or data.get('result',{}).get('data',[]) or [])
            for row in rows:
                code  = (row.get('STD_CODE') or row.get('stdCode') or '').strip()
                title = (row.get('STD_NAME') or row.get('stdName') or '').strip()
                if not code or not title or not is_sports(title): continue
                hcno = row.get('PLAN_CODE') or row.get('hcno') or ''
                results.append({
                    'code':          code,
                    'title':         title,
                    'status':        norm_status(row.get('STD_STATUS') or row.get('status') or ''),
                    'issueDate':     norm_date(row.get('ISSUE_DATE') or row.get('issueDate')),
                    'implementDate': norm_date(row.get('IMPL_DATE') or row.get('implDate')),
                    'abolishDate':   norm_date(row.get('ABOL_DATE') or row.get('abolDate')),
                    'issuedBy':      (row.get('ISSUE_DEPT') or row.get('issueDept') or '').strip(),
                    'isMandatory':   is_mandatory(code),
                    'readUrl':       f"https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno={hcno}" if hcno else None,
                })
            if results: break
        except Exception as e:
            log(f"    samr异常[{url[-30:]}]: {e}")
    return results

def fetch_ttbz(keyword):
    results = []
    try:
        resp = SESSION.post(
            "https://www.ttbz.org.cn/api/search/standard",
            json={"keyword": keyword, "pageIndex": 1, "pageSize": 30},
            timeout=20
        )
        if resp.ok:
            rows = resp.json().get('Data') or resp.json().get('data') or []
            for row in rows:
                code  = (row.get('StdCode') or row.get('stdCode') or '').strip()
                title = (row.get('StdName') or row.get('stdName') or '').strip()
                if not code or not title or not is_sports(title): continue
                results.append({
                    'code': code, 'title': title, 'type': '团标',
                    'status': norm_status(row.get('Status') or '现行'),
                    'issueDate': norm_date(row.get('IssueDate')),
                    'implementDate': norm_date(row.get('ImplementDate')),
                    'abolishDate': None,
                    'issuedBy': (row.get('OrgName') or '').strip(),
                    'isMandatory': False,
                })
    except Exception as e:
        log(f"    ttbz异常: {e}")
    return results

def fetch_dbba(keyword):
    results = []
    try:
        resp = SESSION.get(
            'https://dbba.sacinfo.org.cn/api/standard/list',
            params={"searchText": keyword, "pageSize": 30, "pageNum": 1},
            timeout=20
        )
        if resp.ok:
            items = (resp.json().get('data') or {}).get('list') or []
            for item in items:
                code  = (item.get('stdCode') or '').strip()
                title = (item.get('stdName') or '').strip()
                if not code or not title or not is_sports(title): continue
                results.append({
                    'code': code, 'title': title, 'type': '地方标准',
                    'status': norm_status(item.get('status') or ''),
                    'issueDate': norm_date(item.get('publishDate')),
                    'implementDate': norm_date(item.get('implementDate')),
                    'issuedBy': (item.get('publishDept') or '').strip(),
                    'isMandatory': False,
                })
    except Exception as e:
        log(f"    dbba异常: {e}")
    return results

def check_status_online(std):
    code = std.get('code','')
    if not code: return None
    try:
        resp = SESSION.post(
            "https://std.samr.gov.cn/gb/search/gbQueryPage",
            json={"searchText": code, "pageSize": 5, "pageIndex": 1}, timeout=12
        )
        if not resp.ok: return None
        for row in (resp.json().get('rows') or []):
            rc = (row.get('STD_CODE') or '').strip()
            if rc and norm_code(rc) == norm_code(code):
                ns = norm_status(row.get('STD_STATUS',''))
                if ns and ns != std.get('status'):
                    upd = dict(std)
                    upd['status'] = ns
                    if ns == '废止':
                        upd['abolishDate'] = norm_date(row.get('ABOL_DATE')) or datetime.now().strftime('%Y-%m-%d')
                    return upd
    except Exception: pass
    return None

def merge(existing, new_items):
    idx = {norm_code(s['code']): i for i, s in enumerate(existing)}
    added = updated_n = 0
    for item in new_items:
        cn = norm_code(item['code'])
        if cn in idx:
            orig, changed = existing[idx[cn]], False
            for f in ('status','abolishDate','implementDate','issueDate','issuedBy'):
                nv = item.get(f)
                if nv and nv != orig.get(f):
                    orig[f] = nv; changed = True
            if changed: updated_n += 1
        else:
            entry = build_entry(item)
            existing.append(entry)
            idx[cn] = len(existing)-1
            added += 1
    return existing, added, updated_n

def save_db(db, standards, dry_run):
    today = datetime.now().strftime('%Y-%m-%d')
    db.update({'standards': standards, 'updated': today,
               'version': today.replace('-','.'), 'total': len(standards)})
    if dry_run:
        log(f"\n🔵 [预览] {len(standards)} 条，不写入文件"); return
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    log(f"\n✅ 写入完成：{len(standards)} 条  版本 {today}")

def load_db():
    if not DATA_FILE.exists():
        log("⚠️  数据文件不存在，将从空白建库")
        return {'standards': []}, []
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            db = json.load(f)
        standards = db.get('standards') or []
        log(f"📦 现有标准数: {len(standards)} 条")
        return db, standards
    except Exception as e:
        log(f"⚠️  数据文件损坏({e})，从空白建库")
        return {'standards': []}, []

def run(dry_run=False, check_only=False):
    log("="*60)
    log(f"体育标准数据库 — 自动抓取更新 v3  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"模式: {'[预览]' if dry_run else '[仅核查]' if check_only else '[完整抓取]'}")
    log("="*60)

    db, standards = load_db()

    # 阶段一：核查状态
    if standards:
        log(f"\n🔍 阶段一：核查 {len(standards)} 条标准状态…")
        changed = 0
        for i, std in enumerate(standards):
            upd = check_status_online(std)
            if upd:
                standards[i] = upd; changed += 1
                log(f"  🔄 {std['code']}: {std.get('status')} → {upd['status']}")
            time.sleep(0.5)
        log(f"  状态变更: {changed} 条")
    else:
        log("\n⚡ 数据库为空，跳过核查，直接全量抓取")

    if check_only:
        save_db(db, standards, dry_run); return

    # 阶段二：多源抓取
    log(f"\n🌐 阶段二：多来源抓取（{len(KEYWORDS)} 个关键词）…")
    all_new = []
    for i, kw in enumerate(KEYWORDS, 1):
        log(f"  [{i:02d}/{len(KEYWORDS)}] 「{kw}」")
        a = fetch_samr(kw)
        b = fetch_ttbz(kw)
        if a: log(f"         samr +{len(a)}")
        if b: log(f"         ttbz +{len(b)}")
        all_new.extend(a); all_new.extend(b)
        time.sleep(1.0)
        if i % 5 == 0:
            c = fetch_dbba(kw)
            if c: log(f"         dbba +{len(c)}")
            all_new.extend(c)
            time.sleep(0.8)

    log(f"\n  原始抓取: {len(all_new)} 条（含重复）")

    # 阶段三：合并
    log("\n🔀 阶段三：对比合并…")
    before = len(standards)
    standards, added, updated_n = merge(standards, all_new)
    log(f"  新增 {added} | 更新 {updated_n} | 原有 {before} | 最终 {len(standards)}")

    save_db(db, standards, dry_run)
    log(f"\n📊 现行 {sum(1 for s in standards if s.get('status')=='现行')} | "
        f"废止 {sum(1 for s in standards if s.get('status')=='废止')} | "
        f"即将实施 {sum(1 for s in standards if s.get('status')=='即将实施')}")

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--dry',   action='store_true')
    p.add_argument('--check', action='store_true')
    args = p.parse_args()
    run(dry_run=args.dry, check_only=args.check)
