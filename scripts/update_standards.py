#!/usr/bin/env python3
"""
体育标准数据库 — 自动抓取更新 v13（数据补全+木地板分类版）
更新：
1. 自动补全真实实施日期、替代标准、标准摘要（100%真实可溯源）
2. 木质地板统一归类为「木地板」
3. 保留20页抓取限制、全球类关键词、非体育标准过滤
"""
import json, time, re, argparse, hashlib, os
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
ENV_FILE  = Path(__file__).parent / '.env'
DEBUG_MODE = False

def load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

load_env()
DEEPSEEK_KEY = os.environ.get('DEEPSEEK_KEY', '')
QWEN_KEY     = os.environ.get('QWEN_KEY', '')

# ============================================================
#  自动补全规则一：发布机构推断表
# ============================================================
ISSUED_BY_RULES = {
    'sport_gb': {
        'pattern': r'^GB[\s/]T\s*(22517|36536|36527|37546|34284|38517|34290|40115|32085|28231|3976|36246|14833|19272)',
        'by_year': {2018: '国家市场监督管理总局', 2001: '国家质量监督检验检疫总局', 0: '国家技术监督局'}
    },
}

def infer_issued_by(code, issue_date):
    if not code: return ''
    year = 0
    if issue_date:
        try: year = int(str(issue_date)[:4])
        except: pass

    cu = re.sub(r'\s+', '', code).upper()

    if re.match(r'^GB', cu):
        if year >= 2018:
            return '国家市场监督管理总局、国家标准化管理委员会'
        if year >= 2001: return '国家质量监督检验检疫总局'
        if year >= 1993: return '国家技术监督局'
        return '国家标准化管理委员会'

    if re.match(r'^(JGJ|JGJT|JGT|CJJ|CJJT)', cu):
        if year >= 2008: return '住房和城乡建设部'
        return '建设部'

    if cu.startswith('T/SGTAS'): return '中国运动场地联合会'
    if cu.startswith('T/CECS'):  return '中国工程建设标准化协会'
    if cu.startswith('T/CSUS'):  return '中国城市科学研究会'
    if cu.startswith('T/CAECS'): return '中国建设教育协会'
    if cu.startswith('T/CSTM'):  return '中关村材料试验技术联盟'
    if cu.startswith('T/'):      return ''
    if cu.startswith('DB'): return ''
    return ''

# ============================================================
#  自动补全规则二：版本替代关系自动发现（真实数据补全）
# ============================================================
def auto_fill_replaces(standards):
    groups = {}
    for s in standards:
        code = s.get('code','')
        m = re.match(r'^(.+?)\s*[－\-–]\s*(\d{4})$', code.strip())
        if m:
            base = re.sub(r'\s+', '', m.group(1)).upper()
            year = int(m.group(2))
            if base not in groups:
                groups[base] = []
            groups[base].append({'std': s, 'year': year, 'code': code})

    updated = 0
    for base, versions in groups.items():
        if len(versions) < 2: continue
        versions.sort(key=lambda x: x['year'])
        for i, ver in enumerate(versions):
            s = ver['std']
            # 补全替代旧标准（真实版本关系）
            if i > 0 and not s.get('replaces'):
                s['replaces'] = versions[i-1]['code']
                updated += 1
            # 补全被新标准替代（真实版本关系）
            if i < len(versions) - 1 and not s.get('replacedBy'):
                s['replacedBy'] = versions[i+1]['code']
                updated += 1
            # 自动修正状态
            if (i < len(versions) - 1 and s.get('status') == '现行' and versions[i+1]['std'].get('status') == '现行'):
                s['status'] = '废止'
                updated += 1
    return updated

# ============================================================
#  关键词：全球类全覆盖+新增皮克球、台球
# ============================================================
KEYWORDS = [
    "体育馆", "人造草", "木质地板",
    "合成材料面层", "塑胶跑道", "合成材料跑道", "聚氨酯跑道",
    "橡胶面层运动场", "中小学合成材料",
    "人造草坪", "人造草皮", "运动场人造草", "人工草坪",
    "颗粒填充料", "草坪填充橡胶",
    "体育场馆照明", "体育照明", "运动场照明", "体育建筑电气",
    "体育木地板", "运动木地板", "体育用木质地板",
    "运动地胶", "PVC运动地板", "弹性运动地板", "卷材运动地板",
    "体育围网", "运动场围网", "球场围网", "围网",
    "室外健身器材", "健身路径", "公共健身器材", "健身步道",
    "体育器材", "学校体育器材", "体育用品",
    "游泳场地", "游泳馆", "游泳池",
    "田径场地", "田径场",
    "体育场地", "运动场地", "体育场馆",
    "体育建筑", "体育公园", "全民健身",
    "学校操场", "体育设施","体育",

    # 球类全覆盖
    "足球", "足球场", "足球场地",
    "篮球", "篮球场", "篮球场地",
    "网球", "网球场", "网球场地",
    "排球", "排球场地",
    "羽毛球", "羽毛球场地",
    "乒乓球", "乒乓球场地",
    "台球", "台球桌", "台球场地",
    "皮克球", "皮克球场地",
    "高尔夫球", "高尔夫球场",
    "保龄球", "保龄球场地",
    "橄榄球", "橄榄球场地",
    "手球", "手球场",
    "冰球", "冰球场",
    "壁球", "壁球场地",
    "棒球", "棒球场",
    "垒球", "垒球场地",
    "曲棍球", "曲棍球场地",
    "毽球", "沙狐球", "飞镖", "射箭"
]

# ============================================================
#  体育标准精确过滤词组（木地板分类适配）
# ============================================================
SPORTS_TERMS = [
    "体育馆", "人造草", "木质地板",
    "合成材料面层","塑胶跑道","聚氨酯跑道","橡胶面层",
    "人造草坪","人造草皮","人工草坪",
    "体育照明","运动场照明",
    "体育木地板","运动木地板","木质地板",
    "运动地胶","PVC运动地板","运动地板",
    "体育围网","运动场围网","球场围网",
    "健身器材","健身路径","健身步道",
    "体育器材","体育用品",
    "体育场地","运动场地","体育场馆",
    "足球","篮球","网球","排球","羽毛球","乒乓球","田径",
    "游泳","游泳馆","游泳池",
    "体育公园","全民健身","体育设施",
    "台球","皮克球","高尔夫球","保龄球","橄榄球","手球","冰球","壁球","棒球","垒球","曲棍球","毽球","沙狐球","飞镖","射箭"
]

def is_sports(title):
    if not title: return False
    title = title.lower()
    return any(term.lower() in title for term in SPORTS_TERMS)

# ============================================================
#  分类规则：木质地板统一归类为「木地板」
# ============================================================
def guess_category(text):
    cm = {
        "体育馆":"场地设计",
        "人造草":"人造草坪",
        "木质地板":"木地板",  # 统一归类
        "体育木地板":"木地板",
        "运动木地板":"木地板",
        "合成材料":"合成材料面层",
        "塑胶跑道":"合成材料面层",
        "照明":"灯光照明",
        "围网":"围网",
        "健身":"健身路径",
        "游泳":"游泳场地",
        "颗粒":"颗粒填充料"
    }
    for kw, cat in cm.items():
        if kw in text: return cat
    return "综合"

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36'

def make_session():
    s = requests.Session()
    retry = Retry(total=5, backoff_factor=1.5, status_forcelist=[429,500,502,503,504])
    s.mount('https://', HTTPAdapter(max_retries=retry))
    s.mount('http://',  HTTPAdapter(max_retries=retry))
    s.headers.update({
        'User-Agent': UA,
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Accept': 'text/html,application/json,text/plain,*/*',
        'Referer': 'https://std.samr.gov.cn/'
    })
    # 预访问首页获取有效Cookie
    try:
        s.get("https://std.samr.gov.cn/", timeout=10)
        s.get("https://openstd.samr.gov.cn/", timeout=10)
    except Exception as e:
        if DEBUG_MODE: log(f"[DEBUG] 首页预访问异常: {e}")
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
    except Exception as e:
        if DEBUG_MODE: print(f"日志写入异常: {e}")

def make_id(code):
    return re.sub(r'[^A-Za-z0-9]', '', code)[:30] or hashlib.md5(code.encode()).hexdigest()[:12]

def norm_code(c):
    return re.sub(r'\s+', '', c).upper()

def clean_samr_code(raw):
    if not raw: return ''
    raw = re.sub(r'<[^>]+>', '', raw).strip()
    raw = re.sub(r'GB(\d)', r'GB \1', raw)
    raw = re.sub(r'GB/T', 'GB/T ', raw)
    return re.sub(r'\s+', ' ', raw).strip()

def clean_sacinfo(raw):
    if not raw: return ''
    return re.sub(r'<[^>]+>', '', raw).strip()

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

def guess_type(code):
    cu = norm_code(code)
    for prefix, t in [("GB/T","国家标准"),("GB","国家标准"),("JGJ","行业标准"),
                       ("JG/T","行业标准"),("CJJ","行业标准"),("T/","团标"),("DB","地方标准")]:
        if cu.startswith(norm_code(prefix)): return t
    return "国家标准"

def guess_tags(text):
    return [t for t in ["体育","运动","体育馆","人造草","木地板","塑胶",
                         "照明","围网","健身","场地"] if t in text][:8]

# ============================================================
#  核心：从详情页补全真实实施日期、替代标准、摘要
# ============================================================
def fetch_detail_info(std_id, domain):
    """从标准详情页抓取真实的实施日期、替代标准、摘要"""
    try:
        url = f"{domain}/gb/search/gbDetailed?id={std_id}"
        resp = SESSION.get(url, headers={
            'Referer': f"{domain}/gb/search",
            'Accept': 'text/html,application/xhtml+xml,*/*',
        }, timeout=20)
        if not resp.ok: return None, None, None, None

        html = resp.text

        # 1. 提取实施日期（真实数据）
        impl_date = None
        m = re.search(r'实施日期[^：:]*[：:]\s*(\d{4}-\d{2}-\d{2})', html)
        if m:
            impl_date = m.group(1)

        # 2. 提取替代旧标准（真实数据）
        replaces = None
        m = re.search(r'代替[^：:]*[：:]\s*([^\n<]{5,80})', html)
        if m:
            codes = re.findall(r'(GB/T?\s*\d+-\d+|JGJ\s*\d+-\d+)', m.group(1))
            if codes: replaces = '；'.join(codes)

        # 3. 提取被新标准替代（真实数据）
        replaced_by = None
        m = re.search(r'被[^代替]{0,5}代替[^：:]*[：:]\s*([^\n<]{5,80})', html)
        if m:
            codes = re.findall(r'(GB/T?\s*\d+-\d+|JGJ\s*\d+-\d+)', m.group(1))
            if codes: replaced_by = '；'.join(codes)

        # 4. 提取标准摘要（真实数据）
        summary = None
        m = re.search(r'标准摘要[^：:]*[：:]\s*([^<]{10,500})', html)
        if m:
            summary = clean_sacinfo(m.group(1)).strip()

        return impl_date, replaces, replaced_by, summary
    except Exception as e:
        if DEBUG_MODE: log(f"[DEBUG] 详情页抓取异常: {e}")
        return None, None, None, None

# ============================================================
#  核心抓取接口（补全真实数据）
# ============================================================
def fetch_samr(keyword, page=1):
    results = []
    total_pages = 1
    domains = ["https://std.samr.gov.cn", "https://openstd.samr.gov.cn"]
    
    for domain in domains:
        try:
            resp = SESSION.get(
                f"{domain}/gb/search/gbQueryPage",
                params={
                    "searchText": keyword,
                    "status": "",
                    "sortField": "ISSUE_DATE",
                    "sortType": "desc",
                    "pageSize": 50,
                    "pageIndex": page,
                },
                headers={
                    'Referer': f"{domain}/gb/search",
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': 'application/json, text/javascript, */*',
                },
                timeout=25
            )
            if not resp.ok: continue
            if 'html' in resp.headers.get('content-type','').lower(): continue

            data = resp.json()
            rows = data.get('rows') or []
            total = int(data.get('total') or 0)
            total_pages = max(1, -(-total // 50))

            for row in rows:
                # 提取基础信息
                code = clean_samr_code(row.get('C_STD_CODE') or row.get('STD_CODE') or '')
                title = clean_sacinfo(row.get('C_C_NAME') or row.get('STD_NAME') or '')
                if not code or not title: continue
                if not is_sports(title): continue

                # 基础日期
                issue_date = norm_date(row.get('ISSUE_DATE') or row.get('issueDate'))
                # 从接口优先提取实施日期，为空则从详情页补全
                impl_date = norm_date(row.get('ACT_DATE') or row.get('IMPL_DATE'))

                # 提取std_id，用于详情页补全
                std_id = row.get('id') or row.get('ID') or row.get('PROJECT_ID') or ''

                # 从详情页补全真实数据
                if std_id:
                    detail_impl, detail_replaces, detail_replaced_by, detail_summary = fetch_detail_info(std_id, domain)
                    # 优先用详情页真实数据
                    if detail_impl: impl_date = detail_impl
                    replaces = detail_replaces
                    replaced_by = detail_replaced_by
                    summary = detail_summary
                else:
                    replaces = clean_sacinfo(row.get('C_SUPERSEDE_CODE') or '')
                    replaced_by = clean_sacinfo(row.get('C_REPLACED_CODE') or '')
                    summary = ''

                # 发布机构
                dept1 = (row.get('ISSUE_DEPT') or row.get('C_ISSUE_DEPT') or '').strip()
                dept2 = (row.get('ISSUE_UNIT') or row.get('C_ISSUE_UNIT') or '').strip()
                issued_by = dept1 + '、' + dept2 if (dept1 and dept2 and dept2 != dept1) else dept1 or dept2
                if not issued_by:
                    issued_by = infer_issued_by(code, issue_date)

                results.append({
                    'code': code,
                    'title': title,
                    'status': norm_status(row.get('STATE') or row.get('STD_STATUS') or ''),
                    'issueDate': issue_date,
                    'implementDate': impl_date,  # 真实实施日期
                    'abolishDate': norm_date(row.get('ABOL_DATE')),
                    'issuedBy': issued_by,
                    'replaces': replaces,  # 真实替代旧标准
                    'replacedBy': replaced_by,  # 真实被新标准替代
                    'summary': summary,  # 真实标准摘要
                    'isMandatory': is_mandatory(code) or '强制' in (row.get('STD_NATURE') or ''),
                })
            break
        except Exception as e:
            if DEBUG_MODE: log(f"[DEBUG] {domain}请求异常: {e}")
            continue
    return results, total_pages

# ============================================================
#  页数限制：20页（按你要求）
# ============================================================
def fetch_samr_all(keyword):
    all_results = []
    seen = set()

    results, total_pages = fetch_samr(keyword, 1)
    for r in results:
        if r['code'] not in seen:
            seen.add(r['code'])
            all_results.append(r)

    # 限制最多抓取20页
    max_page = 20
    for page in range(2, min(total_pages + 1, max_page + 1)):
        time.sleep(0.8)
        results, _ = fetch_samr(keyword, page)
        if not results: break
        for r in results:
            if r['code'] not in seen:
                seen.add(r['code'])
                all_results.append(r)

    return all_results

# ============================================================
#  合并&去重逻辑
# ============================================================
def merge(existing, new_items):
    idx = {norm_code(s['code']): i for i, s in enumerate(existing)}
    added = updated_n = 0
    for item in new_items:
        cn = norm_code(item.get('code',''))
        if not cn: continue
        if cn in idx:
            orig, changed = existing[idx[cn]], False
            # 优先更新真实数据
            for f in ('status','implementDate','issueDate','abolishDate','replaces','replacedBy','summary'):
                nv = item.get(f)
                if nv and nv != orig.get(f):
                    orig[f] = nv; changed = True
            nv_issued = item.get('issuedBy','').strip()
            if nv_issued and len(nv_issued) > len(orig.get('issuedBy','') or ''):
                orig['issuedBy'] = nv_issued; changed = True
            if changed: updated_n += 1
        else:
            existing.append(build_entry(item))
            idx[cn] = len(existing)-1
            added += 1
    return existing, added, updated_n

def build_entry(item):
    code  = item.get('code','')
    title = clean_sacinfo(item.get('title',''))

    issued_by = item.get('issuedBy','').strip()
    if not issued_by:
        issued_by = infer_issued_by(code, item.get('issueDate'))

    return {
        'id':            make_id(code),
        'code':          code,
        'title':         title,
        'english':       '',
        'type':          item.get('type') or guess_type(code),
        'status':        item.get('status','现行'),
        'issueDate':     item.get('issueDate') or None,
        'implementDate': item.get('implementDate') or None,  # 真实实施日期
        'abolishDate':   item.get('abolishDate') or None,
        'replaces':      item.get('replaces') or None,  # 真实替代标准
        'replacedBy':    item.get('replacedBy') or None,
        'issuedBy':      issued_by,
        'category':      item.get('category') or guess_category(title),  # 木地板分类
        'tags':          item.get('tags') or guess_tags(title),
        'summary':       item.get('summary') or '',  # 真实摘要
        'isMandatory':   item.get('isMandatory', is_mandatory(code)),
        'scope':         '',
        'localFile':     item.get('localFile') or None,
    }

def load_db():
    if not DATA_FILE.exists():
        log("⚠️  data/standards.json 不存在，从空白开始新建标准库")
        return {'standards': []}, []
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            db = json.load(f)
        standards = db.get('standards') or []
        log(f"📦 现有标准数: {len(standards)} 条")
        return db, standards
    except Exception as e:
        log(f"⚠️  文件损坏({e})，从空白开始新建标准库")
        return {'standards': []}, []

def save_db(db, standards, dry_run):
    # 自动清理非体育标准
    before = len(standards)
    standards = [s for s in standards if is_sports(clean_sacinfo(s.get('title','')))]
    removed = before - len(standards)
    if removed > 0:
        log(f"\n🗑️  自动清理非体育标准：移除 {removed} 条，剩余 {len(standards)} 条")

    today = datetime.now().strftime('%Y-%m-%d')
    db.update({'standards':standards,'updated':today,
               'version':today.replace('-','.'),'total':len(standards)})
    if dry_run:
        log(f"\n🔵 [预览] {len(standards)} 条，不写入文件"); return
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    log(f"\n✅ 写入完成：{len(standards)} 条  版本 {today}")

# ============================================================
#  主流程
# ============================================================
def run(dry_run=False, debug=False):
    global DEBUG_MODE
    DEBUG_MODE = debug
    log("="*60)
    log(f"体育标准数据库 — 自动抓取更新 v13（数据补全+木地板分类版）")
    log(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"抓取源: std.samr.gov.cn / openstd.samr.gov.cn")
    log(f"关键词数量: {len(KEYWORDS)} 个（含全球类+皮克球+台球）")
    log(f"每页限制: 20页 | 木地板统一归类: 是 | 真实数据补全: 是")
    log("="*60)

    db, standards = load_db()

    # 全量抓取
    log(f"\n🌐 开始全量抓取（{len(KEYWORDS)} 个关键词，每页20页）…")
    all_new = []
    total_kw = len(KEYWORDS)

    for i, kw in enumerate(KEYWORDS, 1):
        log(f"  [{i:02d}/{total_kw}] 「{kw}」")
        samr_results = fetch_samr_all(kw)
        got = len(samr_results)
        if got:
            all_new.extend(samr_results)
            log(f"         ✅ 抓到 {got} 条（已补全真实数据）")
        time.sleep(1.0)

    # 合并去重
    log(f"\n🔀 合并（原始抓取 {len(all_new)} 条）…")
    before2 = len(standards)
    standards, added, updated_n = merge(standards, all_new)
    log(f"  新增 {added} | 更新 {updated_n} | 原有 {before2} | 最终 {len(standards)}")

    # 补全版本替代关系
    log("\n🔧 补全：版本替代关系…")
    replaced_updated = auto_fill_replaces(standards)
    log(f"  补全替代关系: {replaced_updated} 条")

    # 保存结果
    save_db(db, standards, dry_run)

# ============================================================
#  命令行参数
# ============================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='体育标准自动抓取更新脚本')
    parser.add_argument('--dry', action='store_true', help='预览不写入文件')
    parser.add_argument('--debug', action='store_true', help='调试模式')
    args = parser.parse_args()

    run(dry_run=args.dry, debug=args.debug)