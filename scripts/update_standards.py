#!/usr/bin/env python3
"""
体育建设标准自动抓取 & 更新脚本
======================================
数据来源（三大官方平台）：
  1. 全国标准信息公共服务平台  https://std.samr.gov.cn   → 国标 / 行标 / 地标
  2. 全国团体标准信息平台      https://www.ttbz.org.cn   → 团标 T/
  3. 国家标准全文公开系统      https://openstd.samr.gov.cn → 精准阅读/下载链接

运行方式：
  python scripts/update_standards.py           # 完整抓取 + 状态核查
  python scripts/update_standards.py --check   # 仅核查现有标准状态
  python scripts/update_standards.py --enrich  # 补全国标精准阅读/下载链接
  python scripts/update_standards.py --dry     # 预览模式，不写入文件
"""

import json
import time
import hashlib
import argparse
import re
from datetime import datetime
from pathlib import Path

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    print("请先安装依赖: pip install requests urllib3")
    raise

# ============================================================
#  配置
# ============================================================
DATA_FILE = Path(__file__).parent.parent / 'data' / 'standards.json'
LOG_FILE  = Path(__file__).parent.parent / 'data' / 'update_log.txt'

SEARCH_KEYWORDS = [
    "体育场地", "运动场地", "合成材料面层", "塑胶跑道",
    "人造草", "人造草坪", "体育照明", "运动照明",
    "体育木地板", "运动木地板", "体育围网", "健身器材",
    "室外健身", "体育器材", "运动地板", "PVC运动",
    "弹性地板", "体育建筑", "体育场馆", "颗粒填充",
    "橡胶颗粒", "体育公园", "全民健身", "学校操场",
    "足球场地", "篮球场地", "网球场地", "田径场地",
    "游泳场地", "排球场地", "羽毛球场地", "乒乓球场地",
    "体育设施建设", "健身路径",
]

CATEGORY_MAP = {
    "合成材料": "合成材料面层", "塑胶跑道": "合成材料面层", "合成面层": "合成材料面层",
    "人造草":   "人造草坪",    "草坪":     "人造草坪",
    "照明":     "灯光照明",    "灯光":     "灯光照明",
    "木地板":   "木地板",      "木质地板": "木地板",
    "PVC":      "PVC运动地胶", "弹性地板": "PVC运动地胶", "运动地板": "PVC运动地胶",
    "围网":     "围网",        "防护网":   "围网",
    "健身器材": "健身路径",    "室外健身": "健身路径",    "健身路径": "健身路径",
    "体育器材": "体育器材",    "学校器材": "体育器材",
    "颗粒":     "颗粒填充料",  "橡胶颗粒": "颗粒填充料",
    "游泳":     "游泳场地",
    "建筑":     "场地设计",    "设计规范": "场地设计",    "建设": "场地设计",
}

TYPE_MAP = {
    "GB/T": "国家标准", "GB ": "国家标准",
    "JGJ":  "行业标准", "JG/T": "行业标准", "CJJ": "行业标准",
    "GA ":  "行业标准",
    "DB":   "地方标准",
    "T/":   "团标",
}

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Referer': 'https://std.samr.gov.cn/',
}

SPORTS_KEYWORDS_CHECK = [
    "体育", "运动", "健身", "竞技", "球场", "跑道", "操场",
    "合成材料", "人造草", "草坪", "塑胶", "围网", "木地板",
    "PVC", "弹性地板", "颗粒", "橡胶", "游泳", "篮球", "足球",
    "网球", "排球", "羽毛球", "田径", "乒乓", "体操",
]


# ============================================================
#  HTTP 会话
# ============================================================
def make_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5,
                  status_forcelist=[429, 500, 502, 503, 504])
    s.mount('https://', HTTPAdapter(max_retries=retry))
    s.mount('http://',  HTTPAdapter(max_retries=retry))
    s.headers.update(HEADERS)
    return s


SESSION = make_session()


# ============================================================
#  工具函数
# ============================================================
def make_id(code: str) -> str:
    clean = re.sub(r'[^A-Za-z0-9]', '', code)
    return clean[:30] if clean else hashlib.md5(code.encode()).hexdigest()[:12]


def guess_type(code: str) -> str:
    cu = code.upper()
    for prefix, t in TYPE_MAP.items():
        if cu.startswith(prefix.upper().strip()):
            return t
    return "国家标准"


def guess_category(title: str, summary: str = "") -> str:
    text = title + summary
    for kw, cat in CATEGORY_MAP.items():
        if kw in text:
            return cat
    return "综合"


def guess_tags(title: str, summary: str = "") -> list:
    candidates = [
        "体育", "运动", "塑胶", "合成材料", "人造草", "照明", "木地板",
        "PVC", "围网", "健身", "器材", "颗粒", "橡胶", "游泳", "篮球",
        "足球", "网球", "田径", "排球", "羽毛球", "跑道", "场地", "操场",
        "中小学", "学校", "安全", "施工", "验收", "设计", "检测",
    ]
    text = title + summary
    return [t for t in candidates if t in text][:6]


def log(msg: str):
    ts = datetime.now().strftime('%H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def is_sports_related(text: str) -> bool:
    return any(kw in text for kw in SPORTS_KEYWORDS_CHECK)


def norm_status(raw: str) -> str:
    raw = str(raw).strip()
    if any(x in raw for x in ['现行', '有效', '执行', '施行']):
        return '现行'
    if any(x in raw for x in ['废止', '作废', '撤销', '废弃']):
        return '废止'
    if any(x in raw for x in ['即将', '待实施', '未实施']):
        return '即将实施'
    return '现行'


def norm_date(raw) -> str:
    if not raw:
        return None
    raw = str(raw).strip()
    if raw.isdigit() and len(raw) >= 10:
        try:
            dt = datetime.fromtimestamp(int(raw) / (1000 if len(raw) > 10 else 1))
            return dt.strftime('%Y-%m-%d')
        except Exception:
            pass
    cleaned = re.sub(r'[^\d]', '', raw)
    if len(cleaned) == 8:
        return f"{cleaned[:4]}-{cleaned[4:6]}-{cleaned[6:]}"
    return None


def is_mandatory(code: str) -> bool:
    c = code.upper().replace(' ', '')
    if re.match(r'^GB\d', c) and '/T' not in c:
        return True
    if c.startswith('JGJ'):
        return True
    return False


def norm_code(c: str) -> str:
    return c.upper().replace(' ', '').replace('\t', '')


# ============================================================
#  来源一：全国标准信息公共服务平台（国标/行标/地标）
# ============================================================
def fetch_samr(keyword: str, page: int = 1) -> list:
    url = "https://std.samr.gov.cn/gb/search/gbQueryPage"
    payload = {
        "searchText": keyword,
        "status":     "",
        "sortField":  "ISSUE_DATE",
        "sortType":   "desc",
        "pageSize":   50,
        "pageIndex":  page,
    }
    results = []
    try:
        resp = SESSION.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        rows = (data.get('rows')
                or data.get('data', {}).get('rows', [])
                or [])
        for row in rows:
            code  = (row.get('STD_CODE') or row.get('stdCode') or '').strip()
            title = (row.get('STD_NAME') or row.get('stdName') or '').strip()
            if not code or not title or not is_sports_related(title):
                continue
            hcno = row.get('PLAN_CODE') or row.get('hcno') or ''
            results.append({
                'code':          code,
                'title':         title,
                'status':        norm_status(row.get('STD_STATUS') or ''),
                'issueDate':     norm_date(row.get('ISSUE_DATE') or row.get('issueDate')),
                'implementDate': norm_date(row.get('IMPL_DATE')  or row.get('implDate')),
                'abolishDate':   norm_date(row.get('ABOL_DATE')  or row.get('abolDate')),
                'issuedBy':      (row.get('ISSUE_DEPT') or row.get('issueDept') or '').strip(),
                'isMandatory':   is_mandatory(code),
                'readUrl':       f"https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno={hcno}" if hcno else None,
                'pdfUrl':        f"https://openstd.samr.gov.cn/bzgk/gb/viewGbInfo?id={hcno}&type=2" if hcno else None,
                'downloadUrl':   f"https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno={hcno}" if hcno else "https://openstd.samr.gov.cn/",
                '_source':       'samr',
            })
    except requests.exceptions.ConnectionError:
        log(f"  ⚠️  samr 连接失败，跳过: {keyword}")
    except Exception as e:
        log(f"  ⚠️  samr 失败 [{keyword}]: {e}")
    return results


# ============================================================
#  来源二：全国团体标准信息平台（团标 T/）
# ============================================================
def fetch_ttbz(keyword: str) -> list:
    url = "https://www.ttbz.org.cn/api/search/standard"
    payload = {"keyword": keyword, "pageIndex": 1, "pageSize": 30}
    results = []
    try:
        resp = SESSION.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get('Data') or data.get('data') or []
        for row in rows:
            code  = (row.get('StdCode') or row.get('stdCode') or '').strip()
            title = (row.get('StdName') or row.get('stdName') or '').strip()
            if not code or not title or not is_sports_related(title):
                continue
            results.append({
                'code':          code,
                'title':         title,
                'status':        norm_status(row.get('Status') or '现行'),
                'issueDate':     norm_date(row.get('IssueDate') or row.get('issueDate')),
                'implementDate': norm_date(row.get('ImplementDate') or row.get('implDate')),
                'abolishDate':   None,
                'issuedBy':      (row.get('OrgName') or row.get('orgName') or '').strip(),
                'isMandatory':   False,
                'readUrl':       f"https://www.ttbz.org.cn/StandardManage/Search/?searchKey={code}",
                'pdfUrl':        None,
                'downloadUrl':   'https://www.ttbz.org.cn/',
                '_source':       'ttbz',
            })
    except requests.exceptions.ConnectionError:
        log(f"  ⚠️  ttbz 连接失败，跳过: {keyword}")
    except Exception as e:
        log(f"  ⚠️  ttbz 失败 [{keyword}]: {e}")
    return results


# ============================================================
#  状态核查：核查现有标准的最新状态
# ============================================================
def check_status(std: dict) -> dict:
    code = std.get('code', '')
    if not code or std.get('type') not in ('国家标准', '行业标准'):
        return None
    try:
        resp = SESSION.post(
            "https://std.samr.gov.cn/gb/search/gbQueryPage",
            json={"searchText": code, "pageSize": 5, "pageIndex": 1},
            timeout=12
        )
        rows = resp.json().get('rows') or []
        for row in rows:
            rc = (row.get('STD_CODE') or '').strip()
            if rc and norm_code(rc) == norm_code(code):
                new_status = norm_status(row.get('STD_STATUS', ''))
                if new_status and new_status != std.get('status'):
                    updated = dict(std)
                    updated['status'] = new_status
                    if new_status == '废止':
                        d = norm_date(row.get('ABOL_DATE'))
                        updated['abolishDate'] = d or datetime.now().strftime('%Y-%m-%d')
                    return updated
    except Exception:
        pass
    return None


# ============================================================
#  补全国家标准精准链接
# ============================================================
def fetch_gb_urls(code: str) -> dict:
    urls = {}
    try:
        resp = SESSION.post(
            "https://openstd.samr.gov.cn/bzgk/gb/gbQuery",
            json={"searchText": code, "pageSize": 5, "pageIndex": 1},
            timeout=15
        )
        items = resp.json().get('rows') or []
        for item in items:
            item_code = (item.get('STD_CODE') or '').strip()
            if norm_code(item_code) == norm_code(code):
                hcno = item.get('PLAN_CODE') or item.get('hcno') or ''
                if hcno:
                    urls['hcno']    = hcno
                    urls['readUrl'] = f"https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno={hcno}"
                    urls['pdfUrl']  = f"https://openstd.samr.gov.cn/bzgk/gb/viewGbInfo?id={hcno}&type=2"
                break
    except Exception as e:
        log(f"  ⚠️  fetch_gb_urls({code}): {e}")
    return urls


# ============================================================
#  数据合并
# ============================================================
def merge(existing: list, new_items: list) -> tuple:
    index = {norm_code(s['code']): i for i, s in enumerate(existing)}
    added = updated = 0
    for item in new_items:
        cn = norm_code(item['code'])
        if cn in index:
            pos = index[cn]
            orig = existing[pos]
            changed = False
            for f in ('status', 'abolishDate', 'implementDate', 'readUrl', 'pdfUrl'):
                nv = item.get(f)
                if nv and nv != orig.get(f):
                    orig[f] = nv
                    changed = True
            if changed:
                updated += 1
        else:
            entry = build_entry(item)
            existing.append(entry)
            index[cn] = len(existing) - 1
            added += 1
    return existing, added, updated


def build_entry(item: dict) -> dict:
    code  = item.get('code', '')
    title = item.get('title', '')
    t     = guess_type(code)
    return {
        'id':            make_id(code),
        'code':          code,
        'title':         title,
        'english':       '',
        'type':          t,
        'status':        item.get('status', '现行'),
        'issueDate':     item.get('issueDate') or None,
        'implementDate': item.get('implementDate') or None,
        'abolishDate':   item.get('abolishDate') or None,
        'replaces':      None,
        'replacedBy':    None,
        'issuedBy':      item.get('issuedBy', ''),
        'category':      guess_category(title),
        'tags':          guess_tags(title),
        'summary':       f"规定了{title}的相关技术要求、试验方法、检验规则及使用要求。（摘要待人工完善）",
        'isMandatory':   item.get('isMandatory', False),
        'scope':         f"适用于{title}相关场合",
        'isFree':        bool(item.get('readUrl') and 'openstd' in item.get('readUrl', '')),
        'readUrl':       item.get('readUrl') or None,
        'pdfUrl':        item.get('pdfUrl') or None,
        'downloadUrl':   item.get('downloadUrl', ''),
    }


# ============================================================
#  保存
# ============================================================
def save_db(db: dict, standards: list, dry_run: bool):
    today = datetime.now().strftime('%Y-%m-%d')
    db['standards'] = standards
    db['updated']   = today
    db['version']   = today.replace('-', '.')
    db['total']     = len(standards)
    if dry_run:
        log(f"\n🔵 [预览] 不写入文件，最终数量: {len(standards)}")
        return
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    log(f"\n✅ 已写入 {DATA_FILE}，共 {len(standards)} 条，版本 {today}")


# ============================================================
#  主流程
# ============================================================
def run(dry_run: bool = False, check_only: bool = False):
    log("=" * 55)
    log("体育标准数据库自动抓取 & 更新工具")
    log(f"模式: {'[预览]' if dry_run else '[仅核查]' if check_only else '[完整抓取]'}")
    log("=" * 55)

    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        db = json.load(f)
    standards = db.get('standards', [])
    log(f"📦 现有标准数: {len(standards)}")

    # 阶段一：状态核查
    log("\n🔍 阶段一：核查现有标准状态…")
    changed = 0
    for i, std in enumerate(standards):
        upd = check_status(std)
        if upd:
            standards[i] = upd
            changed += 1
            log(f"  🔄 {std['code']}  {std.get('status')} → {upd['status']}")
        time.sleep(0.4)
    log(f"  ✅ 状态变更: {changed} 条")

    if check_only:
        save_db(db, standards, dry_run)
        return

    # 阶段二：抓取新标准
    log(f"\n🌐 阶段二：抓取新标准（{len(SEARCH_KEYWORDS)} 个关键词）…")
    all_new = []
    for i, kw in enumerate(SEARCH_KEYWORDS, 1):
        log(f"  [{i:02d}/{len(SEARCH_KEYWORDS)}] 「{kw}」")
        items = fetch_samr(kw)
        log(f"       samr +{len(items)}")
        all_new.extend(items)
        time.sleep(0.8)
        t_items = fetch_ttbz(kw)
        log(f"       ttbz +{len(t_items)}")
        all_new.extend(t_items)
        time.sleep(0.8)

    log(f"\n  原始抓取: {len(all_new)} 条（含重复）")

    # 阶段三：合并去重
    log("\n🔀 阶段三：合并去重入库…")
    standards, added, updated_n = merge(standards, all_new)
    log(f"  ✅ 新增 {added} 条 | 更新 {updated_n} 条 | 总量 {len(standards)} 条")

    save_db(db, standards, dry_run)


def enrich_urls():
    """为所有免费国家标准补全精准阅读/下载链接"""
    log("=" * 55)
    log("补全国家标准精准阅读/下载链接")
    log("=" * 55)
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        db = json.load(f)
    standards = db.get('standards', [])
    enriched = 0
    for i, std in enumerate(standards):
        if std.get('type') != '国家标准' or std.get('readUrl'):
            continue
        urls = fetch_gb_urls(std['code'])
        if urls:
            standards[i].update(urls)
            standards[i]['isFree'] = True
            enriched += 1
            log(f"  ✅ {std['code']} → {urls.get('readUrl', '')}")
        time.sleep(0.6)
    db['standards'] = standards
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    log(f"\n✅ 补全完成，共补全 {enriched} 条")


# ============================================================
#  入口
# ============================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='体育标准自动抓取更新工具')
    parser.add_argument('--dry',    action='store_true', help='预览模式，不写入文件')
    parser.add_argument('--check',  action='store_true', help='仅核查现有标准状态')
    parser.add_argument('--enrich', action='store_true', help='补全国家标准的精准阅读/下载链接')
    args = parser.parse_args()
    if args.enrich:
        enrich_urls()
    else:
        run(dry_run=args.dry, check_only=args.check)
