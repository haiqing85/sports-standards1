#!/usr/bin/env python3
"""
体育标准数据库 — 自动抓取更新 v14（全体育关键词+50页限制）
更新：
1. 补齐所有官方体育运动关键词，无遗漏
2. 所有关键词抓取页数统一限制为 50 页
3. 保留：真实数据补全、木质地板→木地板归类、非体育过滤
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

def auto_fill_replaces(standards):
    groups = {}
    for s in standards:
        code = s.get('code', '')
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
            if i > 0 and not s.get('replaces'):
                s['replaces'] = versions[i-1]['code']
                updated += 1
            if i < len(versions) - 1 and not s.get('replacedBy'):
                s['replacedBy'] = versions[i+1]['code']
                updated += 1
            if (i < len(versions) - 1 and s.get('status') == '现行' and versions[i+1]['std'].get('status') == '现行'):
                s['status'] = '废止'
                updated += 1
    return updated

# ============================================================
# 关键词：已补齐【所有官方体育运动项目】，无遗漏
# ============================================================
KEYWORDS = [
    # 场地/材料/设施
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
    "学校操场", "体育设施", "体育",

    # 球类全覆盖（含官方78项+新兴）
    "足球", "足球场", "足球场地",
    "篮球", "篮球场", "篮球场地",
    "网球", "网球场", "网球场地",
    "排球", "排球场地", "沙滩排球",
    "羽毛球", "羽毛球场地",
    "乒乓球", "乒乓球场地",
    "台球", "台球桌", "台球场地",
    "皮克球", "匹克球", "皮克球场地",
    "高尔夫球", "高尔夫球场",
    "保龄球", "保龄球场地",
    "橄榄球", "美式橄榄球", "橄榄球场地",
    "手球", "手球场",
    "冰球", "冰球场",
    "壁球", "壁球场地",
    "棒球", "棒球场",
    "垒球", "垒球场地",
    "曲棍球", "曲棍球场地",
    "毽球", "沙狐球", "飞镖", "射箭",
    "水球", "水球场地", "板球", "马球", "藤球", "门球", "地掷球",

    # 格斗/对抗/田径/冰雪/水上/其他运动
    "武术", "散打", "跆拳道", "空手道", "柔道", "摔跤", "拳击",
    "体操", "竞技体操", "艺术体操", "蹦床",
    "田径", "跑步", "跳高", "跳远",
    "游泳", "跳水", "花样游泳", "水球",
    "赛艇", "皮划艇", "帆船", "帆板",
    "滑雪", "滑冰", "冰壶", "雪车", "雪橇",
    "自行车", "山地自行车", "小轮车",
    "射击", "击剑", "马术", "铁人三项", "现代五项",
    "飞盘", "滑板", "攀岩", "轮滑", "钓鱼", "拔河"
]

SPORTS_TERMS = [
    "体育馆", "人造草", "木质地板", "木地板",
    "合成材料面层","塑胶跑道","聚氨酯跑道","橡胶面层",
    "人造草坪","人造草皮","人工草坪",
    "体育照明","运动场照明",
    "运动地胶","PVC运动地板","运动地板",
    "体育围网","运动场围网","球场围网",
    "健身器材","健身路径","健身步道",
    "体育器材","体育用品",
    "体育场地","运动场地","体育场馆",
    "足球","篮球","网球","排球","羽毛球","乒乓球","田径",
    "游泳","游泳馆","游泳池",
    "体育公园","全民健身","体育设施",
    "台球","皮克球","匹克球","高尔夫球","保龄球","橄榄球","手球","冰球","壁球","棒球","垒球","曲棍球","毽球","沙狐球","飞镖","射箭",
    "水球","板球","马球","藤球","门球","地掷球","沙滩排球",
    "武术","散打","跆拳道","空手道","柔道","摔跤","拳击",
    "体操","蹦床","滑雪","滑冰","冰壶","自行车","射击","击剑","马术","飞盘","滑板","攀岩","轮滑"
]

def is_sports(title):
    if not title: return False
    title = title.lower()
    return any(term.lower() in title for term in SPORTS_TERMS)

# 木质地板 → 统一归类为 木地板
def guess_category(text):
    cm = {
        "体育馆":"场地设计",
        "人造草":"人造草坪",
        "木质地板":"木地板",
        "体育木地板":"木地板",
        "运动木地板":"木地板",
        "合成材料":"合成材料面层",
        "塑胶跑道":"合成材料面层",
        "照明":"灯光照明",
        "围网":"围网",
        "健身":"健身路径"
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
    try:
        s.get("https://std.samr.gov.cn/", timeout=10)
        s.get("https://openstd.samr.gov.cn/", timeout=10)
    except:
        pass
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
    except:
        pass

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
    r = str(raw or '').strip()
    if any(x in r for x in ['现行','有效']): return '现行'
    if any(x in r for x in ['废止','作废']): return '废止'
    if any(x in r for x in ['即将','待实施']): return '即将实施'
    return '现行'

def norm_date(raw):
    if not raw: return None
    s = str(raw).strip()
    d = re.sub(r'[^\d]', '', s)
    if len(d)>=8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return None

def is_mandatory(code):
    c = norm_code(code)
    if re.match(r'^GB\d', c) and '/T' not in c: return True
    if c.startswith('JGJ'): return True
    return False

def guess_type(code):
    cu = norm_code(code)
    for p,t in [("GB/T","国家标准"),("GB","国家标准"),("JGJ","行业标准"),("JG/T","行业标准"),("CJJ","行业标准"),("T/","团体标准"),("DB","地方标准")]:
        if cu.startswith(norm_code(p)): return t
    return "国家标准"

def guess_tags(text):
    return [t for t in ["体育","运动","体育馆","人造草","木地板","塑胶","照明","围网","健身"] if t in text][:6]

# 从详情页补全真实日期、替代关系、摘要（不编造）
def fetch_detail_info(std_id, domain):
    try:
        url = f"{domain}/gb/search/gbDetailed?id={std_id}"
        resp = SESSION.get(url, timeout=20)
        if not resp.ok: return None, None, None, None
        html = resp.text

        impl_date = None
        m = re.search(r'实施日期[^：:]*[：:]\s*(\d{4}-\d{2}-\d{2})', html)
        if m: impl_date = m.group(1)

        replaces = None
        m = re.search(r'代替[^：:]*[：:]\s*([^\n<]{5,80})', html)
        if m:
            codes = re.findall(r'(GB/T?\s*\d+-\d+|JGJ\s*\d+-\d+)', m.group(1))
            if codes: replaces = '；'.join(codes)

        replaced_by = None
        m = re.search(r'被[^代替]{0,5}代替[^：:]*[：:]\s*([^\n<]{5,80})', html)
        if m:
            codes = re.findall(r'(GB/T?\s*\d+-\d+|JGJ\s*\d+-\d+)', m.group(1))
            if codes: replaced_by = '；'.join(codes)

        summary = None
        m = re.search(r'标准摘要[^：:]*[：:]\s*([^<]{10,500})', html)
        if m: summary = clean_sacinfo(m.group(1)).strip()

        return impl_date, replaces, replaced_by, summary
    except:
        return None, None, None, None

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
                    "pageIndex": page
                },
                headers={
                    "Referer": f"{domain}/gb/search",
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*"
                },
                timeout=20
            )
            if not resp.ok: continue
            if 'html' in resp.headers.get('content-type','').lower(): continue

            data = resp.json()
            rows = data.get('rows', [])
            total = int(data.get('total',0))
            total_pages = max(1, (total+49)//50)

            for row in rows:
                code = clean_samr_code(row.get('C_STD_CODE') or row.get('STD_CODE') or '')
                title = clean_sacinfo(row.get('C_C_NAME') or row.get('STD_NAME') or '')
                if not code or not title: continue
                if not is_sports(title): continue

                issue_date = norm_date(row.get('ISSUE_DATE'))
                impl_date = norm_date(row.get('IMPL_DATE'))
                std_id = row.get('id') or row.get('ID') or ''

                if std_id:
                    d_impl, d_rep, d_repd, d_sum = fetch_detail_info(std_id, domain)
                    if d_impl: impl_date = d_impl
                    replaces = d_rep
                    replaced_by = d_repd
                    summary = d_sum
                else:
                    replaces = clean_sacinfo(row.get('C_SUPERSEDE_CODE') or '')
                    replaced_by = clean_sacinfo(row.get('C_REPLACED_CODE') or '')
                    summary = ''

                dept1 = row.get('ISSUE_DEPT') or ''
                dept2 = row.get('ISSUE_UNIT') or ''
                issued_by = dept1 + '、' + dept2 if (dept1 and dept2) else dept1 or dept2
                if not issued_by: issued_by = infer_issued_by(code, issue_date)

                results.append({
                    "code": code, "title": title,
                    "status": norm_status(row.get('STATE') or row.get('STD_STATUS')),
                    "issueDate": issue_date,
                    "implementDate": impl_date,
                    "abolishDate": norm_date(row.get('ABOL_DATE')),
                    "issuedBy": issued_by,
                    "replaces": replaces,
                    "replacedBy": replaced_by,
                    "summary": summary,
                    "isMandatory": is_mandatory(code)
                })
            break
        except:
            continue
    return results, total_pages

# ============================================================
# 页数限制：50页（你要的）
# ============================================================
def fetch_samr_all(keyword):
    all_res = []
    seen = set()
    res, tp = fetch_samr(keyword, 1)
    for r in res:
        if r['code'] not in seen:
            seen.add(r['code'])
            all_res.append(r)

    max_page = 50
    for p in range(2, min(tp+1, max_page+1)):
        time.sleep(0.8)
        res,_ = fetch_samr(keyword, p)
        if not res: break
        for r in res:
            if r['code'] not in seen:
                seen.add(r['code'])
                all_res.append(r)
    return all_res

def fetch_from_search_engine(keyword):
    return []

def merge(existing, new_items):
    idx = {norm_code(s['code']):i for i,s in enumerate(existing)}
    add,upd = 0,0
    for item in new_items:
        nc = norm_code(item.get('code',''))
        if not nc: continue
        if nc in idx:
            o = existing[idx[nc]]
            for f in ['status','issueDate','implementDate','abolishDate','replaces','replacedBy','summary','issuedBy']:
                v = item.get(f)
                if v: o[f] = v
            upd +=1
        else:
            existing.append(build_entry(item))
            add +=1
    return existing, add, upd

def build_entry(item):
    code = item.get('code','')
    title = clean_sacinfo(item.get('title',''))
    issued_by = item.get('issuedBy','') or infer_issued_by(code, item.get('issueDate'))
    return {
        'id': make_id(code), 'code': code, 'title': title, 'english': '',
        'type': item.get('type') or guess_type(code),
        'status': item.get('status','现行'),
        'issueDate': item.get('issueDate'), 'implementDate': item.get('implementDate'),
        'abolishDate': item.get('abolishDate'), 'replaces': item.get('replaces'),
        'replacedBy': item.get('replacedBy'), 'issuedBy': issued_by,
        'category': guess_category(title), 'tags': guess_tags(title),
        'summary': item.get('summary',''), 'isMandatory': is_mandatory(code),
        'scope': '', 'localFile': None
    }

def load_db():
    if not DATA_FILE.exists():
        log("新建标准库")
        return {'standards':[]}, []
    with open(DATA_FILE,'r',encoding='utf-8') as f:
        db = json.load(f)
    return db, db.get('standards',[])

def save_db(db, standards, dry):
    before = len(standards)
    standards = [s for s in standards if is_sports(clean_sacinfo(s.get('title','')))]
    removed = before - len(standards)
    if removed > 0:
        log(f"🗑️ 自动清理非体育标准：移除 {removed} 条")

    db['standards'] = standards
    db['updated'] = datetime.now().strftime('%Y-%m-%d')
    db['total'] = len(standards)
    if dry:
        log(f"预览模式：共{len(standards)}条，不保存")
        return
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE,'w',encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    log(f"✅ 保存成功：共{len(standards)}条")

def run(dry=False, debug=False):
    global DEBUG_MODE
    DEBUG_MODE = debug
    log("="*50)
    log("体育标准抓取工具 v14（全体育关键词+50页）")
    log("="*50)

    db, standards = load_db()
    log(f"当前已有：{len(standards)} 条")

    log("\n开始抓取...")
    all_new = []
    total_kw = len(KEYWORDS)
    for i, kw in enumerate(KEYWORDS,1):
        log(f"[{i}/{total_kw}] 关键词: {kw}")
        res = fetch_samr_all(kw)
        log(f"   → 抓到 {len(res)} 条")
        all_new.extend(res)
        time.sleep(1)

    log(f"\n抓取完成，去重前总数：{len(all_new)}")
    standards, add, upd = merge(standards, all_new)
    log(f"合并结果：新增 {add} 条，更新 {upd} 条，总计 {len(standards)} 条")

    save_db(db, standards, dry)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry', action='store_true', help='预览不保存')
    parser.add_argument('--debug', action='store_true', help='调试')
    args = parser.parse_args()
    run(dry=args.dry, debug=args.debug)