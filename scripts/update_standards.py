#!/usr/bin/env python3
"""
体育标准数据库 — 自动抓取更新 v22（核心问题修复版）
修复内容：
1. 彻底解决「搜木地板出不来木质地板」双向搜索匹配问题
2. 新增关键词「合成材料跑道」，全链路适配
3. 彻底修复替代旧标准号不正确、自替代问题
4. 保留手动修改保护、发布单位原文、50页限制等所有原有规则
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

# ===================== 修复1：彻底解决替代标准号不正确问题 =====================
def auto_fill_replaces(standards):
    """
    仅兜底填充：只有官网没抓到真实替代号时，才自动补全
    严格校验同标准号不同年份，彻底杜绝错误替代、自替代
    """
    groups = {}
    for s in standards:
        code = s.get('code', '')
        # 严格拆分【标准号主体+4位年份】，只匹配GB/T 1234-2024格式
        m = re.match(r'^([A-Z]+\/?T?\s*\d+(?:\.\d+)?)\s*[－\-–]\s*(\d{4})$', code.strip())
        if m:
            base = re.sub(r'\s+', '', m.group(1)).upper()
            try:
                year = int(m.group(2))
            except:
                continue
            if base not in groups:
                groups[base] = []
            groups[base].append({'std': s, 'year': year, 'code': code})
    
    updated = 0
    for base, versions in groups.items():
        if len(versions) < 2:
            continue
        # 严格按年份升序排序，旧版本在前，新版本在后
        versions.sort(key=lambda x: x['year'])
        version_count = len(versions)
        
        for i, ver in enumerate(versions):
            s = ver['std']
            # 只有官网没抓到真实替代号，才自动填充
            if i > 0 and not s.get('replaces'):
                # 严格校验：年份不同、标准主体完全一致，才生成替代关系
                if versions[i-1]['year'] != ver['year'] and versions[i-1]['code'] != ver['code']:
                    s['replaces'] = versions[i-1]['code']
                    updated += 1
            # 只有官网没抓到真实被替代号，才自动填充
            if i < version_count - 1 and not s.get('replacedBy'):
                if versions[i+1]['year'] != ver['year'] and versions[i+1]['code'] != ver['code']:
                    s['replacedBy'] = versions[i+1]['code']
                    updated += 1
            # 只有新版本是现行，才标记旧版本为废止
            if (i < version_count - 1 
                and s.get('status') == '现行' 
                and versions[i+1]['std'].get('status') == '现行'
                and versions[i+1]['code'] != ver['code']):
                s['status'] = '废止'
                updated += 1
    return updated

# ===================== 修复2：新增关键词「合成材料跑道」+ 木地板全量关键词 =====================
KEYWORDS = [
    # 新增关键词
    "合成材料跑道",
    # 原有核心关键词
    "健身", "健身器材", "五体球",
    "体育馆", "人造草", "木质地板", "木地板",
    "合成材料面层", "塑胶跑道", "聚氨酯跑道",
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
    "水球", "板球", "马球", "藤球", "门球", "地掷球",

    "武术", "散打", "跆拳道", "空手道", "柔道", "摔跤", "拳击",
    "体操", "竞技体操", "艺术体操", "蹦床",
    "田径", "跑步", "跳高", "跳远",
    "游泳", "跳水", "花样游泳",
    "赛艇", "皮划艇", "帆船", "帆板",
    "滑雪", "滑冰", "冰壶",
    "自行车", "山地自行车", "小轮车",
    "射击", "击剑", "马术", "铁人三项", "现代五项",
    "飞盘", "滑板", "攀岩", "轮滑", "钓鱼", "拔河"
]

# ===================== 体育过滤 + 新增合成材料跑道 + 木地板全量术语 =====================
SPORTS_TERMS = [
    # 新增术语
    "合成材料跑道",
    # 原有术语
    "健身","健身器材","五体球",
    "体育馆", "人造草", "木质地板", "木地板",
    "合成材料面层","塑胶跑道","聚氨酯跑道","橡胶面层",
    "人造草坪","人造草皮","人工草坪",
    "体育照明","运动场照明",
    "运动地胶","PVC运动地板","运动地板",
    "体育围网","运动场围网","球场围网",
    "健身路径","健身步道",
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

BLACKLIST = ["电动自行车", "电动车", "电动二轮车", "电动单车"]

def is_sports(title):
    if not title:
        return False
    title = title.lower()
    title_clean = title.replace('　', ' ').replace('，', ',').replace('。', '.')
    for bk in BLACKLIST:
        if bk.lower() in title_clean:
            return False
    return any(term.lower() in title_clean for term in SPORTS_TERMS)

# ===================== 修复3：木地板分类+标签强制统一，解决搜索问题 =====================
def guess_category(text):
    """所有木质地板相关，强制归为「木地板」，合成材料跑道归为合成材料面层"""
    if not text:
        return "综合"
    cm = {
        "体育馆":"场地设计",
        "人造草":"人造草坪",
        "木质地板":"木地板",
        "体育木地板":"木地板",
        "运动木地板":"木地板",
        "体育用木质地板":"木地板",
        "木地板":"木地板",
        "合成材料跑道":"合成材料面层",
        "合成材料":"合成材料面层",
        "塑胶跑道":"合成材料面层",
        "照明":"灯光照明",
        "围网":"围网",
        "健身":"健身器材",
        "健身器材":"健身器材"
    }
    for kw, cat in cm.items():
        if kw in text:
            return cat
    return "综合"

def guess_tags(text):
    """所有木地板/木质地板相关，强制加上双向标签，确保搜索全匹配"""
    if not text:
        return []
    tags_pool = ["体育","运动","健身","健身器材","五体球","体育馆","人造草","木地板","木质地板","塑胶","照明","围网","合成材料跑道"]
    base_tags = [t for t in tags_pool if t in text][:8]
    
    # 强制双向标签：只要有木地板/木质地板，两个标签都加上
    if "木地板" in text or "木质地板" in text:
        if "木地板" not in base_tags:
            base_tags.append("木地板")
        if "木质地板" not in base_tags:
            base_tags.append("木质地板")
    
    return base_tags

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36'

def make_session():
    s = requests.Session()
    retry = Retry(
        total=5, 
        backoff_factor=1.5, 
        status_forcelist=[429,500,502,503,504],
        allowed_methods=["GET"]
    )
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
    except Exception as e:
        log(f"会话初始化警告：{str(e)}")
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
        print(f"日志写入失败：{e}")

def make_id(code):
    code_clean = re.sub(r'[^A-Za-z0-9]', '', code.strip().replace('　', ''))[:30]
    if code_clean:
        return code_clean
    return hashlib.md5((code or 'empty').encode()).hexdigest()[:12]

def norm_code(c):
    if not c:
        return ''
    c_clean = c.replace('　', ' ').replace('－', '-').strip()
    return re.sub(r'\s+', '', c_clean).upper()

def clean_samr_code(raw):
    if not raw:
        return ''
    raw = re.sub(r'<[^>]+>', '', raw).strip()
    raw = re.sub(r'GB(\d)', r'GB \1', raw)
    raw = re.sub(r'GB/T', 'GB/T ', raw)
    raw = raw.replace('　', ' ').replace('－', '-')
    return re.sub(r'\s+', ' ', raw).strip()

def clean_sacinfo(raw):
    if not raw:
        return ''
    raw_clean = re.sub(r'<[^>]+>', '', raw).strip()
    return raw_clean.replace('　', ' ').strip()

def norm_status(raw):
    if not raw:
        return '现行'
    r = str(raw).strip().lower()
    if any(x in r for x in ['现行','有效']):
        return '现行'
    if any(x in r for x in ['废止','作废']):
        return '废止'
    if any(x in r for x in ['即将','待实施']):
        return '即将实施'
    return '现行'

def norm_date(raw):
    if not raw:
        return None
    s = str(raw).strip()
    s_clean = s.replace('０','0').replace('１','1').replace('２','2').replace('３','3').replace('４','4')
    s_clean = s_clean.replace('５','5').replace('６','6').replace('７','7').replace('８','8').replace('９','9')
    s_clean = s_clean.replace('－', '-').replace('　', ' ')
    d = re.sub(r'[^\d]', '', s_clean)
    if len(d)>=8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return None

def is_mandatory(code):
    c = norm_code(code)
    if not c:
        return False
    if re.match(r'^GB\d', c) and '/T' not in c:
        return True
    if c.startswith('JGJ'):
        return True
    return False

def guess_type(code):
    cu = norm_code(code)
    if not cu:
        return "国家标准"
    type_map = [
        ("GB/T","国家标准"),
        ("GB","国家标准"),
        ("JGJ","行业标准"),
        ("JG/T","行业标准"),
        ("CJJ","行业标准"),
        ("T/","团体标准"),
        ("DB","地方标准")
    ]
    for p,t in type_map:
        if cu.startswith(norm_code(p)):
            return t
    return "国家标准"

# ===================== 修复4：详情页真实替代号精准匹配，杜绝错误 =====================
def fetch_detail_real_info(std_id, domain):
    """从官网详情页抓取真实的替代号，优先使用，绝不自动生成覆盖"""
    if not std_id or not domain:
        return None, None, None, None
    try:
        url = f"{domain}/gb/search/gbDetailed?id={std_id}"
        resp = SESSION.get(url, timeout=20)
        resp.raise_for_status()
        html = resp.text

        impl_date = None
        m = re.search(r'实施日期[^：:]*[：:]\s*(\d{4}[-\s]?\d{2}[-\s]?\d{2})', html)
        if m:
            impl_date = norm_date(m.group(1))

        # 精准匹配替代旧标准号，支持多标准号
        replaces = None
        m = re.search(r'代替[^：:]*[：:]\s*([^\n<]{5,150})', html)
        if m:
            # 精准匹配标准号格式：GB/T 1234-2024、GB 1234-2024、JGJ 123-2024
            codes = re.findall(r'[A-Z]+\/?T?\s*\d+(?:\.\d+)?\s*[－\-–]\s*\d{4}', m.group(1))
            if codes:
                replaces = '；'.join([clean_samr_code(c) for c in codes])

        # 精准匹配被新标准替代号
        replaced_by = None
        m = re.search(r'被.*代替[^：:]*[：:]\s*([^\n<]{5,150})', html)
        if m:
            codes = re.findall(r'[A-Z]+\/?T?\s*\d+(?:\.\d+)?\s*[－\-–]\s*\d{4}', m.group(1))
            if codes:
                replaced_by = '；'.join([clean_samr_code(c) for c in codes])

        summary = None
        m = re.search(r'标准摘要[^：:]*[：:]\s*([^<]{10,600})', html)
        if m:
            summary = clean_sacinfo(m.group(1)).strip()

        return impl_date, replaces, replaced_by, summary
    except Exception as e:
        if DEBUG_MODE:
            log(f"详情页抓取失败 {std_id}@{domain}：{str(e)}")
        return None, None, None, None

# ===================== 抓取接口 =====================
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
                timeout=20)
            resp.raise_for_status()
            if 'html' in resp.headers.get('content-type','').lower():
                continue

            data = resp.json()
            rows = data.get('rows', [])
            total = int(data.get('total',0))
            total_pages = max(1, (total+49)//50)

            for row in rows:
                code = clean_samr_code(row.get('C_STD_CODE') or row.get('STD_CODE') or '')
                title = clean_sacinfo(row.get('C_C_NAME') or row.get('STD_NAME') or '')
                if not code or not title:
                    continue
                if not is_sports(title):
                    continue

                issue_date = norm_date(row.get('ISSUE_DATE'))
                impl_date = norm_date(row.get('IMPL_DATE'))
                std_id = row.get('id') or row.get('ID') or ''

                # 优先抓取官网真实的替代号、日期、摘要
                if std_id:
                    d_impl, d_rep, d_repd, d_sum = fetch_detail_real_info(std_id, domain)
                    if d_impl:
                        impl_date = d_impl
                    replaces = d_rep
                    replaced_by = d_repd
                    summary = d_sum
                else:
                    replaces = clean_sacinfo(row.get('C_SUPERSEDE_CODE') or '')
                    replaced_by = clean_sacinfo(row.get('C_REPLACED_CODE') or '')
                    summary = ''

                dept1 = str(row.get('ISSUE_DEPT') or '').strip()
                dept2 = str(row.get('ISSUE_UNIT') or '').strip()
                if dept1 and dept2 and dept2 != dept1:
                    issued_by = f"{dept1}、{dept2}"
                else:
                    issued_by = dept1 or dept2

                results.append({
                    "code": code, 
                    "title": title, 
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
        except Exception as e:
            if DEBUG_MODE:
                log(f"抓取失败 {domain} 关键词:{keyword} 页码:{page}：{str(e)}")
            continue
    return results, total_pages

# ===================== 每页限制 50 页 =====================
def fetch_samr_all(keyword):
    all_res = []
    seen = set()
    try:
        res, tp = fetch_samr(keyword, 1)
        for r in res:
            nc = norm_code(r['code'])
            if nc not in seen:
                seen.add(nc)
                all_res.append(r)
    except Exception as e:
        log(f"关键词 {keyword} 初始页抓取失败：{str(e)}")
        return all_res

    max_page = 50
    for p in range(2, min(tp+1, max_page+1)):
        try:
            time.sleep(0.8)
            res,_ = fetch_samr(keyword, p)
            if not res:
                break
            for r in res:
                nc = norm_code(r['code'])
                if nc not in seen:
                    seen.add(nc)
                    all_res.append(r)
        except Exception as e:
            if DEBUG_MODE:
                log(f"关键词 {keyword} 页码 {p} 抓取失败：{str(e)}")
            continue
    return all_res

# ===================== 修复5：手动修改保护 + 替代号绝不覆盖手动修改 =====================
def merge(existing, new_items):
    existing_code_map = { norm_code(s['code']): s for s in existing }
    add, upd = 0, 0

    for item in new_items:
        code_raw = item.get('code', '')
        nc = norm_code(code_raw)
        if not nc:
            continue

        if nc in existing_code_map:
            old = existing_code_map[nc]
            # 状态：只有官网是现行，才覆盖手动的废止
            new_status = item.get('status')
            if new_status and old.get('status') != new_status:
                if new_status == '现行':
                    old['status'] = new_status
                    upd +=1
            # 替代号：只有用户没手动修改，才用官网真实数据
            for f in ['replaces','replacedBy']:
                val = item.get(f)
                if val and not old.get(f):
                    old[f] = val
                    upd +=1
            # 其他字段：非空才更新
            for f in ['issueDate','implementDate','abolishDate','summary']:
                val = item.get(f)
                if val and val != old.get(f):
                    old[f] = val
                    upd +=1
        else:
            existing.append(build_entry(item))
            add +=1

    # 强制去重，同一个标准号只保留1条
    final_standards = []
    code_set = set()
    for s in existing:
        nc = norm_code(s['code'])
        if not nc:
            continue
        if nc not in code_set:
            code_set.add(nc)
            final_standards.append(s)
        else:
            replaced = False
            for i, exist_s in enumerate(final_standards):
                if norm_code(exist_s['code']) == nc:
                    if s.get('status') == '现行' and exist_s.get('status') != '现行':
                        final_standards[i] = s
                        replaced = True
                    break
            if replaced:
                upd +=1

    return final_standards, add, upd

def build_entry(item):
    code = item.get('code','')
    title = clean_sacinfo(item.get('title',''))
    return {
        'id': make_id(code), 
        'code': code, 
        'title': title, 
        'english': '',
        'type': item.get('type') or guess_type(code),
        'status': item.get('status','现行'),
        'issueDate': item.get('issueDate'), 
        'implementDate': item.get('implementDate'),
        'abolishDate': item.get('abolishDate'), 
        'replaces': item.get('replaces'),
        'replacedBy': item.get('replacedBy'), 
        'issuedBy': item.get('issuedBy',''),
        'category': guess_category(title), 
        'tags': guess_tags(title),
        'summary': item.get('summary',''), 
        'isMandatory': is_mandatory(code),
        'scope': '', 
        'localFile': None
    }

def load_db():
    if not DATA_FILE.exists():
        log("新建标准库")
        return {'standards':[]}, []
    try:
        with open(DATA_FILE,'r',encoding='utf-8') as f:
            db = json.load(f)
        if 'standards' not in db:
            db['standards'] = []
        return db, db['standards']
    except Exception as e:
        log(f"加载标准库失败，新建空库：{str(e)}")
        return {'standards':[]}, []

def save_db(db, standards, dry):
    before = len(standards)
    standards = [s for s in standards if is_sports(clean_sacinfo(s.get('title','')))]
    removed = before - len(standards)
    if removed > 0:
        log(f"🗑️ 自动清理：移除 {removed} 条非体育/电动自行车/重复标准")

    db['standards'] = standards
    db['updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db['total'] = len(standards)
    if dry:
        log(f"预览模式：共{len(standards)}条，不保存")
        return
    try:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DATA_FILE,'w',encoding='utf-8') as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
        log(f"✅ 保存成功：共{len(standards)}条（手动修改已保护，替代号已修复）")
    except Exception as e:
        log(f"❌ 保存失败：{str(e)}")

def run(dry=False, debug=False):
    global DEBUG_MODE
    DEBUG_MODE = debug
    log("="*60)
    log("体育标准抓取工具 v22（核心问题修复版）")
    log("已修复：搜索双向匹配、合成材料跑道关键词、替代标准号错误")
    log("="*60)

    db, standards = load_db()
    log(f"当前已有：{len(standards)} 条")

    log("\n开始抓取...")
    all_new = []
    total_kw = len(KEYWORDS)
    for i, kw in enumerate(KEYWORDS,1):
        log(f"[{i}/{total_kw}] 关键词: {kw}")
        try:
            res = fetch_samr_all(kw)
            log(f"   → 抓到 {len(res)} 条")
            all_new.extend(res)
            time.sleep(1)
        except Exception as e:
            log(f"   → 抓取失败：{str(e)}")
            time.sleep(2)
            continue

    log(f"\n抓取完成，去重前总数：{len(all_new)}")
    # 先合并，再兜底补全替代关系（绝不覆盖官网真实数据）
    standards, add, upd = merge(standards, all_new)
    auto_fill_replaces(standards)
    log(f"合并结果：新增 {add} 条，更新 {upd} 条，去重后总计 {len(standards)} 条")

    save_db(db, standards, dry)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry', action='store_true', help='预览不保存')
    parser.add_argument('--debug', action='store_true', help='调试')
    args = parser.parse_args()
    run(dry=args.dry, debug=args.debug)