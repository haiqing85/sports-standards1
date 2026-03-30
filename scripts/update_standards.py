#!/usr/bin/env python3
"""
体育建设标准自动抓取 & 更新脚本 v9（优化版）
======================================
v9 优化：
  1. 强化体育相关过滤，彻底排除非体育/球类无关内容
  2. 支持多发布单位拼接，完整展示联合发布机构
  3. 精准化摘要生成规则，AI摘要提示更精准
  4. 提升搜索完整性，扩大分页抓取范围，匹配官方平台结果数量
  5. 增强反爬适配，优化请求头和重试策略
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
#  自动补全规则一：发布机构推断表（支持多机构联合发布）
# ============================================================
ISSUED_BY_RULES = {
    # 国家体育总局主导的体育专项国标
    'sport_gb': {
        'pattern': r'^GB[\s/]T\s*(22517|36536|36527|37546|34284|38517|34290|40115|32085|28231|3976|36246|14833|19272)',
        'by_year': {2018: '国家市场监督管理总局、国家体育总局', 2001: '国家质量监督检验检疫总局、国家体育总局', 0: '国家技术监督局、国家体育总局'}
    },
}

def infer_issued_by(code, issue_date):
    """根据编号前缀+发布年份推断发布机构（支持多机构），API返回为空时使用"""
    if not code: return ''
    year = 0
    if issue_date:
        try: year = int(str(issue_date)[:4])
        except: pass

    def by_year(mapping):
        for threshold in sorted(mapping.keys(), reverse=True):
            if year >= threshold:
                return mapping[threshold]
        return list(mapping.values())[-1]

    cu = re.sub(r'\s+', '', code).upper()
    issued_bys = []

    # 国家标准 GB / GB/T / GB/Z
    if re.match(r'^GB', cu):
        # 体育相关国标补充国家体育总局
        if re.match(ISSUED_BY_RULES['sport_gb']['pattern'], cu):
            issued_bys.append(by_year(ISSUED_BY_RULES['sport_gb']['by_year']))
        else:
            if year >= 2018: issued_bys.append('国家市场监督管理总局')
            elif year >= 2001: issued_bys.append('国家质量监督检验检疫总局')
            elif year >= 1993: issued_bys.append('国家技术监督局')
            else: issued_bys.append('国家标准化管理委员会')

    # 建工行业标准 JGJ / JG/T / CJJ / CJJ/T（体育场馆建设类补充体育总局）
    if re.match(r'^(JGJ|JGJT|JGT|CJJ|CJJT)', cu):
        base_dept = '住房和城乡建设部' if year >= 2008 else '建设部'
        if any(kw in code for kw in ['体育', '场馆', '运动场']):
            issued_bys.append(f'{base_dept}、国家体育总局')
        else:
            issued_bys.append(base_dept)

    # 团体标准
    if cu.startswith('T/SGTAS'): issued_bys.append('中国运动场地联合会')
    if cu.startswith('T/CECS'):  issued_bys.append('中国工程建设标准化协会')
    if cu.startswith('T/CSUS'):  issued_bys.append('中国城市科学研究会')
    if cu.startswith('T/CAECS'): issued_bys.append('中国建设教育协会')
    if cu.startswith('T/CSTM'):  issued_bys.append('中关村材料试验技术联盟')
    if cu.startswith('T/') and not issued_bys:      issued_bys.append('')

    # 地方标准：各省机构各异，不推断
    if cu.startswith('DB'): return ''

    # 去重并拼接多机构
    unique_bys = list(set([b for b in issued_bys if b]))
    return '、'.join(unique_bys) if unique_bys else ''

# ============================================================
#  自动补全规则二：版本替代关系自动发现
# ============================================================
def auto_fill_replaces(standards):
    """
    扫描全库，自动发现同编号不同年份的版本关系，填写 replaces/replacedBy。
    只填写目前为空的字段，不覆盖已有数据。
    返回更新条数。
    """
    # 按基础编号分组
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
        if len(versions) < 2:
            continue
        versions.sort(key=lambda x: x['year'])
        for i, ver in enumerate(versions):
            s = ver['std']
            # 有前一个版本（本标准替代了旧版）
            if i > 0 and not s.get('replaces'):
                s['replaces'] = versions[i-1]['code']
                updated += 1
            # 有后一个版本（本标准被新版替代）
            if i < len(versions) - 1 and not s.get('replacedBy'):
                s['replacedBy'] = versions[i+1]['code']
                updated += 1
            # 若存在更新版且本标准仍标记为"现行"，自动改为"废止"
            if (i < len(versions) - 1
                    and s.get('status') == '现行'
                    and versions[i+1]['std'].get('status') == '现行'
                    and not s.get('abolishDate')):
                s['status'] = '废止'
                updated += 1
    return updated

# ============================================================
#  自动补全规则三：samr 详情页抓取替代关系
# ============================================================
def fetch_replaces_from_detail(code, row, session):
    """
    尝试从 samr API 返回的字段或详情页解析替代关系。
    row 是 samr API 返回的原始行数据。
    """
    replaces    = None
    replaced_by = None

    # 1. 先尝试从 API 字段直接读取（部分版本的 samr 会返回）
    for fld in ['C_SUPERSEDE_CODE', 'SUPERSEDE_CODE', 'replaceCode', 'substituteCode']:
        v = (row.get(fld) or '').strip()
        if v:
            replaces = clean_code(v)
            break

    for fld in ['C_REPLACED_CODE', 'REPLACED_CODE', 'replacedCode', 'newCode']:
        v = (row.get(fld) or '').strip()
        if v:
            replaced_by = clean_code(v)
            break

    # 2. 尝试从详情页 HTML 解析（耗时，仅在字段仍为空时执行）
    if not replaces and not replaced_by:
        std_id = row.get('id') or row.get('ID') or row.get('PROJECT_ID') or ''
        if std_id:
            try:
                url = f"https://std.samr.gov.cn/gb/search/gbDetailed?id={std_id}"
                resp = session.get(url, headers={
                    'Referer': 'https://std.samr.gov.cn/gb/search',
                    'Accept': 'text/html,application/xhtml+xml,*/*',
                    'User-Agent': UA,
                    'Cookie': 'Hm_lvt_94c0e990ca01a7805bf49ec2cd078620=1715000000; Hm_lpvt_94c0e990ca01a7805bf49ec2cd078620=1715000000'
                }, timeout=15)
                if resp.ok and 'html' in resp.headers.get('content-type','').lower():
                    html = resp.text
                    # 解析「代替」（本标准替代的旧版）
                    m = re.search(r'代替[^：:]*[：:]\s*([^\n<]{5,80})', html)
                    if m:
                        codes = re.findall(
                            r'(?:GB|GB/T|JGJ|JG/T|CJJ|T/)[^\s,，；;、<]{3,25}', m.group(1))
                        if codes: replaces = '；'.join(codes)
                    # 解析「被代替」（本标准已被新版替代）
                    mb = re.search(r'被[^代替]{0,5}代替[^：:]*[：:]\s*([^\n<]{5,80})', html)
                    if mb:
                        codes = re.findall(
                            r'(?:GB|GB/T|JGJ|JG/T|CJJ|T/)[^\s,，；;、<]{3,25}', mb.group(1))
                        if codes: replaced_by = '；'.join(codes)
            except Exception:
                pass

    return replaces, replaced_by

# ============================================================
#  关键词（按品类，精确搜索）
# ============================================================
KEYWORDS = [
    # 合成材料面层/塑胶跑道
    "合成材料面层", "塑胶跑道", "合成材料跑道", "聚氨酯跑道",
    "橡胶面层运动场", "中小学合成材料",
    # 人造草坪
    "人造草坪", "人造草皮", "运动场人造草", "人工草坪",
    # 颗粒填充料
    "颗粒填充料", "草坪填充橡胶",
    # 灯光照明
    "体育场馆照明", "体育照明", "运动场照明",
    "体育建筑电气",
    # 木地板
    "体育木地板", "运动木地板", "体育用木质地板",
    # PVC运动地板/弹性地板
    "运动地胶", "PVC运动地板", "弹性运动地板",
    "卷材运动地板",
    # 围网（限定体育/运动场相关）
    "体育围网", "运动场围网", "球场围网",
    # 健身器材/健身步道
    "室外健身器材", "健身路径", "公共健身器材",
    "健身步道", "步道", "健身",
    # 体育器材
    "体育器材", "学校体育器材",
    # 游泳场地
    "游泳场地", "游泳馆", "游泳池水质",
    # 球类场地（细分，精准化）
    "足球场地", "足球场", "足球",
    "篮球场地", "篮球场", "篮球",
    "网球场地", "网球场", "网球",
    "田径场地", "田径场",
    "排球场地", "排球",
    "羽毛球场地", "羽毛球",
    "乒乓球场地", "乒乓球",
    "手球场", "手球",
    "棒球场", "棒球",
    "冰球场", "冰球",
    "曲棍球场", "曲棍球",
    "保龄球", "壁球", "高尔夫",
    # 综合体育/场馆（精准化）
    "体育场地", "运动场地", "体育场馆建设",
    "体育建筑设计", "体育公园", "全民健身设施",
    "学校操场", "体育设施建设",
    "体育", "运动场",
]

# ============================================================
#  体育标准精确过滤词组（强化版，排除非体育相关内容）
# ============================================================
SPORTS_TERMS = [
    # 核心体育场地设施
    "体育场地","运动场地","体育场馆","体育建筑","体育公园","全民健身",
    "体育设施","体育场","运动场","学校操场",
    # 合成材料/塑胶跑道
    "合成材料面层","合成材料跑道","塑胶跑道","聚氨酯跑道","橡胶面层",
    # 人造草坪
    "人造草坪","人造草皮","人工草坪","运动场人造草",
    # 颗粒填充料
    "颗粒填充料","草坪填充",
    # 照明
    "体育场馆照明","体育照明","运动场照明","体育场地照明","体育建筑电气",
    # 地板类
    "体育木地板","运动木地板","体育用木质地板","体育馆木地板",
    "运动地胶","PVC运动地板","体育地板","运动地板","弹性运动地板",
    "卷材运动地板","聚氯乙烯运动",
    # 围网（限定体育场景）
    "体育围网","运动场围网","球场围网","体育场围网",
    # 健身相关
    "室外健身器材","健身路径","公共健身器材","户外健身器材",
    "健身步道","步道（健身）","健身设施",
    # 体育器材
    "体育器材","学校体育器材","篮球架","足球门","排球架","乒乓球台",
    # 球类（限定体育场地/设施）
    "足球场地","篮球场","网球场","田径场地","排球场地","羽毛球场地",
    "乒乓球场地","手球场","棒球场","冰球场","曲棍球场",
    "足球场","排球场","羽毛球场","乒乓球场",
    # 游泳场地
    "游泳场地","游泳馆","游泳池（体育）","泳池（竞赛）",
    # 排除非体育关键词（反向过滤）
    "安防","消防","交通","电力","通信","医疗","化工","机械","汽车","食品",
    "农业","林业","渔业","环保","水利","煤炭","石油","天然气","冶金","纺织"
]

# 宽松关键词集合：搜索这些词时仍需基础体育关联验证
BROAD_KEYWORDS = {
    "体育", "足球", "足球场", "足球场地",
    "篮球", "篮球场", "篮球场地",
    "网球", "网球场", "网球场地",
    "排球", "羽毛球", "乒乓球",
    "手球", "手球场", "棒球", "棒球场",
    "冰球", "冰球场", "曲棍球", "曲棍球场",
    "保龄球", "壁球", "高尔夫",
    "围网", "健身步道", "步道", "健身",
    "运动场",
}

def is_sports(title):
    """强化版体育内容过滤：包含体育相关词 且 不包含非体育领域词"""
    if not title: return False
    
    # 先检查是否包含非体育领域词（直接排除）
    exclude_terms = [t for t in SPORTS_TERMS if t.startswith(('安防','消防','交通','电力'))]
    if any(term in title for term in exclude_terms):
        return False
    
    # 检查是否包含核心体育相关词
    sports_core_terms = [t for t in SPORTS_TERMS if not t.startswith(('安防','消防','交通','电力'))]
    return any(term in title for term in sports_core_terms)

def is_sports_for_keyword(title, keyword):
    """宽松关键词时仍验证体育关联性，彻底排除无关内容"""
    if not title or not title.strip():
        return False
    
    # 即使是宽松关键词，也要确保标题和体育相关
    if keyword in BROAD_KEYWORDS:
        # 球类关键词必须包含场地/设施相关词
        ball_kw = ['足球','篮球','网球','排球','羽毛球','乒乓球','手球','棒球','冰球','曲棍球']
        if keyword in ball_kw:
            return any(t in title for t in ['场地','场','馆','运动','体育']) and is_sports(title)
        # 其他宽松关键词直接验证体育关联性
        return is_sports(title)
    
    return is_sports(title)

# 更真实的浏览器请求头（提升反爬适配）
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0'

def make_session():
    """强化session配置：增加重试次数、更真实的请求头、cookie"""
    s = requests.Session()
    # 增加重试次数和退避策略
    retry = Retry(
        total=5, 
        backoff_factor=2, 
        status_forcelist=[429,500,502,503,504],
        allowed_methods=["GET", "POST"]
    )
    s.mount('https://', HTTPAdapter(max_retries=retry))
    s.mount('http://',  HTTPAdapter(max_retries=retry))
    s.headers.update({
        'User-Agent': UA,
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
    })
    # 添加模拟cookie
    s.cookies.set('Hm_lvt_94c0e990ca01a7805bf49ec2cd078620', str(int(time.time()) - 3600))
    s.cookies.set('Hm_lpvt_94c0e990ca01a7805bf49ec2cd078620', str(int(time.time())))
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
    return re.sub(r'[^A-Za-z0-9]', '', code)[:30] or hashlib.md5(code.encode()).hexdigest()[:12]

def norm_code(c):
    return re.sub(r'\s+', '', c).upper()

def clean_code(raw):
    if not raw: return ''
    return re.sub(r'[^\u4e00-\u9fa5A-Za-z0-9/\-]', '', raw).strip()

def clean_sacinfo(raw):
    if not raw: return ''
    return re.sub(r'</?sacinfo>', '', raw).strip()

def clean_samr_code(raw):
    if not raw: return ''
    parts = re.findall(r'<sacinfo>(.*?)</sacinfo>', raw)
    if parts:
        prefix = ''.join(parts[:-1]).strip()
        number = parts[-1].strip()
        slash_map = {'GBT':'GB/T','GBZ':'GB/Z','JGT':'JG/T','GAT':'GA/T'}
        prefix = slash_map.get(prefix, prefix)
        return f"{prefix} {number}".strip() if prefix else number
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

def guess_category(text):
    cm = {
        "合成材料":"合成材料面层","塑胶跑道":"合成材料面层",
        "人造草":"人造草坪","草坪":"人造草坪",
        "照明":"灯光照明","灯光":"灯光照明",
        "木地板":"木地板",
        "地胶":"PVC运动地胶","弹性地板":"PVC运动地胶","运动地板":"PVC运动地胶",
        "围网":"围网",
        "健身器材":"健身路径","健身路径":"健身路径",
        "体育器材":"体育器材",
        "颗粒填充":"颗粒填充料",
        "游泳":"游泳场地",
        "体育建筑":"场地设计","体育公园":"场地设计",
        "足球":"足球场地","篮球":"篮球场地","网球":"网球场地",
        "排球":"排球场地","羽毛球":"羽毛球场地","乒乓球":"乒乓球场地"
    }
    for kw, cat in cm.items():
        if kw in text: return cat
    return "综合"

def guess_tags(text):
    base_tags = ["体育","运动","塑胶","合成材料","人造草","照明",
                 "木地板","围网","健身","颗粒","游泳","篮球","足球",
                 "网球","田径","排球","羽毛球","跑道","场地","学校"]
    # 只保留和文本强相关的标签
    matched_tags = [t for t in base_tags if t in text]
    # 球类标签补充
    ball_tags = {'足球':'足球','篮球':'篮球','网球':'网球','排球':'排球',
                 '羽毛球':'羽毛球','乒乓球':'乒乓球','手球':'手球','棒球':'棒球',
                 '冰球':'冰球','曲棍球':'曲棍球'}
    for ball, tag in ball_tags.items():
        if ball in text and tag not in matched_tags:
            matched_tags.append(tag)
    return matched_tags[:6]

def build_entry(item):
    code  = item.get('code','')
    title = clean_sacinfo(item.get('title',''))

    # ── 自动补全：发布机构（支持多机构） ──────────────────────────────────
    # 优先用 API 返回值，为空时按编号+年份推断
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
        'implementDate': item.get('implementDate') or None,
        'abolishDate':   item.get('abolishDate') or None,
        'replaces':      item.get('replaces') or None,      
        'replacedBy':    item.get('replacedBy') or None,
        'issuedBy':      issued_by,
        'category':      item.get('category') or guess_category(title),
        'tags':          item.get('tags') or guess_tags(title),
        'summary':       item.get('summary') or '',
        'isMandatory':   item.get('isMandatory', is_mandatory(code)),
        'scope':         '',
        'localFile':     item.get('localFile') or None,
    }

# ============================================================
#  来源一：std.samr.gov.cn（关键词精确搜索，提升完整性）
# ============================================================
def fetch_samr(keyword, page=1):
    """
    优化版samr抓取：
    1. 更精准的请求参数，确保searchText生效
    2. 支持多发布机构解析
    3. 增强反爬适配
    """
    results = []
    total_pages = 1

    # 方式一：POST JSON（主要方式）
    try:
        # 更精准的请求体（匹配官方前端参数）
        req_data = {
            "searchText": keyword,
            "stdCode": "",
            "stdName": "",
            "status": "",
            "issueDept": "",
            "implementDateStart": "",
            "implementDateEnd": "",
            "issueDateStart": "",
            "issueDateEnd": "",
            "sortField": "ISSUE_DATE",
            "sortType": "desc",
            "pageSize": 50,
            "pageIndex": page,
            "isExact": True  # 精确匹配关键词
        }
        
        resp = SESSION.post(
            "https://std.samr.gov.cn/gb/search/gbQueryPage",
            json=req_data,
            headers={
                'Referer':       'https://std.samr.gov.cn/gb/search',
                'Origin':        'https://std.samr.gov.cn',
                'Content-Type':  'application/json',
                'Accept':        'application/json, text/plain, */*',
                'X-Requested-With': 'XMLHttpRequest',
                'Sec-Ch-Ua': '"Chromium";v="128", "Not;A=Brand";v="24", "Microsoft Edge";v="128"',
                'Sec-Ch-Ua-Mobile': '?0',
                'Sec-Ch-Ua-Platform': '"Windows"',
            },
            timeout=30
        )
        
        if resp.ok:
            ct = resp.headers.get('content-type','')
            if 'html' in ct.lower():
                if DEBUG_MODE: log(f"    [DEBUG] samr返回HTML，尝试解析…")
                # 处理反爬返回HTML的情况
                return results, total_pages
            
            try:
                data = resp.json()
                rows = data.get('rows') or []
                total = int(data.get('total') or 0)
                if total > 0:
                    total_pages = max(1, -(-total // 50))  # 向上取整
                if DEBUG_MODE:
                    log(f"    [DEBUG] samr p{page}: rows={len(rows)} total={total} total_pages={total_pages}")
                
                for row in rows:
                    code  = clean_samr_code(
                        row.get('C_STD_CODE') or row.get('STD_CODE') or row.get('stdCode') or ''
                    ).strip()
                    title = clean_sacinfo(
                        row.get('C_C_NAME') or row.get('STD_NAME') or row.get('stdName') or ''
                    ).strip()
                    
                    # 过滤空数据和非体育内容
                    if not code or not title: continue
                    if not is_sports_for_keyword(title, keyword): continue

                    # ── 发布机构：解析多机构字段 ───────────────────
                    # 读取所有可能的发布机构字段
                    issued_dept_fields = [
                        row.get('ISSUE_DEPT'), row.get('C_ISSUE_DEPT'),
                        row.get('PUBLISH_DEPT'), row.get('C_PUBLISH_DEPT')
                    ]
                    issued_by_parts = [str(f).strip() for f in issued_dept_fields if f and str(f).strip()]
                    # 去重并拼接多机构
                    issued_by = '、'.join(list(set(issued_by_parts))) if issued_by_parts else ''
                    # 为空时推断
                    if not issued_by:
                        issue_date = norm_date(row.get('ISSUE_DATE') or row.get('issueDate'))
                        issued_by = infer_issued_by(code, issue_date)

                    # ── 替代关系 ───────────────────────────────
                    replaces_val = clean_code(
                        row.get('C_SUPERSEDE_CODE') or row.get('SUPERSEDE_CODE') or
                        row.get('replaceCode') or ''
                    ).strip() or None
                    replaced_by_val = clean_code(
                        row.get('C_REPLACED_CODE') or row.get('REPLACED_CODE') or
                        row.get('replacedCode') or ''
                    ).strip() or None

                    # ── 日期处理 ───────────────────────────────
                    issue_date = norm_date(row.get('ISSUE_DATE') or row.get('issueDate'))
                    implement_date = norm_date(row.get('ACT_DATE') or row.get('IMPL_DATE'))
                    abolish_date = norm_date(row.get('ABOL_DATE'))

                    # ── 强制属性 ───────────────────────────────
                    is_mandatory_val = is_mandatory(code) or '强制' in (row.get('STD_NATURE') or '')

                    results.append({
                        'code':          code,
                        'title':         title,
                        'status':        norm_status(row.get('STATE') or row.get('STD_STATUS') or ''),
                        'issueDate':     issue_date,
                        'implementDate': implement_date,
                        'abolishDate':   abolish_date,
                        'issuedBy':      issued_by,
                        'replaces':      replaces_val,
                        'replacedBy':    replaced_by_val,
                        'isMandatory':   is_mandatory_val,
                    })
                    
            except Exception as e:
                if DEBUG_MODE: log(f"    [DEBUG] samr JSON解析异常: {e}")
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] samr请求异常: {e}")

    return results, total_pages

def fetch_samr_all(keyword):
    """抓取关键词的全部分页（提升分页上限，确保完整性）"""
    all_results = []
    seen = set()

    # 首次抓取
    results, total_pages = fetch_samr(keyword, 1)
    for r in results:
        if r['code'] not in seen:
            seen.add(r['code'])
            all_results.append(r)

    # 扩大分页抓取范围（从10页提升到50页，匹配官方平台结果数量）
    max_pages = min(total_pages + 1, 50)  # 最多抓取50页
    if total_pages > 1:
        log(f"         关键词[{keyword}]总页数:{total_pages}，继续抓取…")
    
    for page in range(2, max_pages):
        time.sleep(1.2)  # 增加延迟，避免反爬
        results, _ = fetch_samr(keyword, page)
        if not results: break
        for r in results:
            if r['code'] not in seen:
                seen.add(r['code'])
                all_results.append(r)

    log(f"         关键词[{keyword}]最终抓取到{len(all_results)}条有效标准")
    return all_results

# ============================================================
#  来源二：ttbz.org.cn 团标平台（优化过滤）
# ============================================================
def fetch_ttbz(keyword):
    results = []
    try:
        resp = SESSION.post(
            "https://www.ttbz.org.cn/api/search/standard",
            json={"keyword": keyword, "pageIndex": 1, "pageSize": 50},  # 增加每页数量
            headers={
                'Referer':      'https://www.ttbz.org.cn/',
                'Origin':       'https://www.ttbz.org.cn',
                'Content-Type': 'application/json',
            },
            timeout=25
        )
        if resp.ok:
            ct = resp.headers.get('content-type','')
            if 'json' in ct:
                data = resp.json()
                rows = data.get('Data') or data.get('data') or []
                for row in rows:
                    code  = (row.get('StdCode') or row.get('stdCode') or '').strip()
                    title = (row.get('StdName') or row.get('stdName') or '').strip()
                    if not code or not title: continue
                    if not is_sports_for_keyword(title, keyword): continue
                    
                    # 解析多发布机构
                    org_fields = [row.get('OrgName'), row.get('PublishOrg'), row.get('IssuedOrg')]
                    org_parts = [str(f).strip() for f in org_fields if f and str(f).strip()]
                    issued_by = '、'.join(list(set(org_parts))) if org_parts else ''

                    results.append({
                        'code':          code,
                        'title':         title,
                        'type':          '团标',
                        'status':        norm_status(row.get('Status') or '现行'),
                        'issueDate':     norm_date(row.get('IssueDate')),
                        'implementDate': norm_date(row.get('ImplementDate')),
                        'issuedBy':      issued_by,
                        'isMandatory':   False,
                    })
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] ttbz异常: {e}")
    return results

# ============================================================
#  来源三：dbba.sacinfo.org.cn 地方标准（优化过滤）
# ============================================================
def fetch_dbba(keyword):
    results = []
    try:
        resp = SESSION.get(
            'https://dbba.sacinfo.org.cn/api/standard/list',
            params={"searchText": keyword, "pageSize": 50, "pageNum": 1},  # 增加每页数量
            headers={'Referer':'https://dbba.sacinfo.org.cn/'},
            timeout=25
        )
        if resp.ok:
            ct = resp.headers.get('content-type','')
            if 'json' in ct:
                data = resp.json()
                items = (data.get('data') or {}).get('list') or []
                for item in items:
                    code  = (item.get('stdCode') or '').strip()
                    title = (item.get('stdName') or '').strip()
                    if not code or not title: continue
                    if not is_sports_for_keyword(title, keyword): continue
                    
                    # 解析多发布机构
                    publish_dept = item.get('publishDept') or ''
                    issue_dept = item.get('issueDept') or ''
                    org_parts = [d.strip() for d in [publish_dept, issue_dept] if d.strip()]
                    issued_by = '、'.join(list(set(org_parts))) if org_parts else ''

                    results.append({
                        'code':          code,
                        'title':         title,
                        'type':          '地方标准',
                        'status':        norm_status(item.get('status') or ''),
                        'issueDate':     norm_date(item.get('publishDate')),
                        'implementDate': norm_date(item.get('implementDate')),
                        'issuedBy':      issued_by,
                        'isMandatory':   False,
                    })
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] dbba异常: {e}")
    return results

# ============================================================
#  摘要自动生成（精准化版）
# ============================================================
# 分类摘要模板库（更精准，贴合实际标准内容）
SUMMARY_TEMPLATES = {
    "合成材料面层": "本标准规定了合成材料运动场地面层的原材料要求、产品技术指标、试验方法、检验规则及施工验收要求，适用于各类体育场馆、学校及公共体育设施的合成材料（塑胶）运动场地面层工程。",
    "塑胶跑道":     "本标准规定了田径场地塑胶跑道面层的技术要求、试验方法、检验规则和施工验收规范，明确了拉伸强度、拉断伸长率、冲击吸收等关键性能指标，适用于各类田径场塑胶跑道的建设与质量验收。",
    "人造草坪":     "本标准规定了运动场地用人造草坪的技术要求、试验方法、检验规则及铺装验收要求，涵盖草丝性能、填充颗粒、底布性能等指标，适用于足球场、橄榄球场等运动场地人造草坪系统的建设与验收。",
    "灯光照明":     "本标准规定了体育场馆照明的照度标准、均匀度、眩光限制、显色性等照明质量指标及节能要求，明确了不同运动项目的照明设计参数，适用于各类室内外体育场馆的照明设计、施工和检测。",
    "木地板":       "本标准规定了体育场馆用木质地板的材料要求、尺寸偏差、物理力学性能、试验方法及安装验收规范，适用于篮球馆、排球馆、羽毛球馆等室内体育场馆运动木地板的生产、安装和质量验收。",
    "PVC运动地胶":  "本标准规定了聚氯乙烯（PVC）弹性运动地板的技术要求、试验方法、检验规则及铺设验收要求，包括冲击吸收、垂直变形、尺寸稳定性等关键指标，适用于室内体育场馆、健身房等运动场地的弹性地板工程。",
    "围网":         "本标准规定了体育场地围网的材料要求、结构强度、防腐性能、试验方法及安装验收规范，适用于足球场、篮球场、网球场等各类运动场地防护围网的制造、安装和质量验收。",
    "健身路径":     "本标准规定了室外健身器材的安全要求、技术要求、试验方法及使用年限，涵盖结构强度、稳定性、耐候性等关键指标，适用于公园、社区、学校等公共场所室外健身器材的生产、安装和安全管理。",
    "健身步道":     "本标准规定了健身步道的建设技术要求，包括路面材质、宽度、坡度、防滑性能、配套设施等指标，明确了不同类型健身步道的建设标准，适用于城市绿道、公园、社区等健身步道的规划设计和建设。",
    "体育器材":     "本标准规定了各类体育器材的产品分类、技术要求、试验方法、检验规则及安全要求，适用于学校、体育场馆、社区等场所使用的体育器材生产制造和质量验收。",
    "颗粒填充料":   "本标准规定了人造草坪填充用橡胶颗粒的技术要求、试验方法、检验规则及环保要求，包括有害物质限量、粒径分布、回弹性能等指标，适用于运动场地人造草坪填充颗粒的生产和质量控制。",
    "游泳场地":     "本标准规定了游泳场地（馆）的设计规范、水质标准、设施配置、安全防护及运营管理要求，适用于各类室内外游泳池、游泳馆的规划设计、施工建设和日常运营管理。",
    "足球场地":     "本标准规定了足球场地的尺寸要求、场地表面性能、排水系统、照明要求及配套设施标准，适用于各级别足球场地（天然草和人工草）的设计、建设和验收。",
    "篮球场地":     "本标准规定了篮球场地的尺寸规格、面层材料性能、场地标线、照明要求及配套设施标准，适用于室内外篮球场地的设计、建设和质量验收。",
    "网球场地":     "本标准规定了网球场地的尺寸要求、面层材料性能、场地标线、排水系统及照明要求，适用于各类硬地、草地、红土网球场地的设计、建设和验收。",
    "田径场地":     "本标准规定了田径场地的跑道尺寸、弯道半径、坡度、面层性能及配套设施标准，明确了不同级别田径场地的建设要求，适用于各类室内外田径场地的设计、建设和验收。",
    "场地设计":     "本标准规定了体育建筑与运动场地的规划设计原则、功能布局、技术指标及配套设施要求，适用于体育场、体育馆、游泳馆、全民健身中心等体育建筑的规划设计和建设。",
    "综合":         "本标准为体育建设领域的技术规范，规定了{title}的技术要求、试验方法、检验规则和验收标准，适用于相关体育场地设施的规划设计、施工建设、质量检验和运营管理。",
}

def generate_summary_by_rule(std):
    """
    精准化摘要生成：
    1. 优先匹配分类模板
    2. 结合标准编号和标题细节
    3. 填充个性化内容，避免千篇一律
    """
    title    = std.get('title', '')
    category = std.get('category', '')
    code     = std.get('code', '')

    # 1. 直接用 category 匹配模板
    if category and category in SUMMARY_TEMPLATES:
        template = SUMMARY_TEMPLATES[category]
        # 替换模板中的变量
        return template.format(title=title[:20])

    # 2. 按标题关键词精准匹配
    kw_map = [
        (["塑胶跑道","合成材料跑道","聚氨酯跑道"], "塑胶跑道"),
        (["合成材料面层","合成材料运动场"], "合成材料面层"),
        (["人造草","人工草坪","草坪系统"], "人造草坪"),
        (["照明","灯光","采光"], "灯光照明"),
        (["木地板","木质地板","运动木"], "木地板"),
        (["弹性地板","PVC","运动地胶","卷材"], "PVC运动地胶"),
        (["围网","防护网"], "围网"),
        (["健身步道","步道"], "健身步道"),
        (["健身器材","健身路径","健身设施"], "健身路径"),
        (["颗粒填充","橡胶颗粒"], "颗粒填充料"),
        (["游泳","游泳馆","泳池"], "游泳场地"),
        (["足球场","足球场地"], "足球场地"),
        (["篮球场","篮球场地"], "篮球场地"),
        (["网球场","网球场地"], "网球场地"),
        (["田径场","田径场地","跑道"], "田径场地"),
        (["体育建筑","体育场馆设计","体育公园","全民健身"], "场地设计"),
        (["体育器材","体育用品"], "体育器材"),
    ]
    for keywords, tpl_key in kw_map:
        if any(kw in title for kw in keywords):
            template = SUMMARY_TEMPLATES.get(tpl_key, SUMMARY_TEMPLATES["综合"])
            return template.format(title=title[:20])

    # 3. 按标准编号特征补充精准内容
    cu = re.sub(r'\s+', '', code).upper()
    if 'JGJ' in cu or 'JGT' in cu or 'CJJ' in cu:
        return f"本标准（{code}）为住房和城乡建设部发布的行业标准，规定了{title[:30]}的技术要求、设计规范和验收标准，适用于相关体育建筑与运动场地的工程建设和质量验收。"
    elif 'GB/T' in cu:
        return f"本标准（{code}）为国家推荐性标准，规定了{title[:30]}的技术要求、试验方法和检验规则，是体育建设领域的重要技术依据，适用于相关体育场地设施的建设和质量控制。"
    elif 'GB' in cu:
        return f"本标准（{code}）为国家强制性标准，规定了{title[:30]}的技术要求、试验方法和检验规则，适用于相关体育场地设施的建设、生产和质量监督。"

    # 4. 通用模板（个性化填充）
    return f"本标准规定了{title[:30]}的技术要求、试验方法和检验规则，明确了相关产品或工程的质量指标和验收要求，适用于体育场地设施的设计、建设、检验和验收。"

def auto_fill_summary(standards):
    """
    全库扫描，对缺摘要的标准用精准规则模板自动生成摘要
    已有摘要的不覆盖
    """
    filled = 0
    for s in standards:
        if not s.get('summary', '').strip():
            summary = generate_summary_by_rule(s)
            if summary:
                s['summary'] = summary
                filled += 1
    log(f"  📝 规则模板补全摘要：{filled} 条")
    return filled

# ============================================================
#  AI摘要补全（精准化提示词）
# ============================================================
def ai_enrich_standard(std):
    provider = 'qwen' if QWEN_KEY else ('deepseek' if DEEPSEEK_KEY else None)
    if not provider: return None
    
    # 精准化提示词：限定体育建设领域，要求准确、专业
    prompt = (f"你是中国体育建设标准领域的专家，请基于以下标准信息，用2-3句话准确描述该标准的核心内容、技术要求和适用范围，内容必须与体育建设相关，语言专业且简洁。\n"
              f"标准编号：{std.get('code','')}\n"
              f"标准名称：{std.get('title','')}\n"
              f"要求：1. 只返回摘要内容，不要多余解释；2. 内容必须准确对应该标准；3. 突出体育建设相关的技术要求；4. 字数控制在100-150字。")
    
    try:
        if provider == 'deepseek':
            resp = SESSION.post(
                'https://api.deepseek.com/chat/completions',
                json={
                    "model":"deepseek-chat",
                    "messages":[{"role":"user","content":prompt}],
                    "max_tokens":200,
                    "temperature":0.1,  # 降低随机性，提升准确性
                    "top_p":0.9
                },
                headers={
                    'Authorization':f'Bearer {DEEPSEEK_KEY}',
                    'Content-Type':'application/json'
                }, 
                timeout=30)
            if resp.ok:
                content = resp.json().get('choices',[{}])[0].get('message',{}).get('content','').strip()
                # 过滤无关内容
                if '非体育' not in content and len(content) > 50:
                    return content
        
        else:  # qwen
            resp = SESSION.post(
                'https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation',
                json={
                    "model":"qwen-turbo",
                    "input":{"messages":[{"role":"user","content":prompt}]},
                    "parameters":{
                        "max_tokens":200,
                        "temperature":0.1,
                        "top_p":0.9
                    }
                },
                headers={
                    'Authorization':f'Bearer {QWEN_KEY}',
                    'Content-Type':'application/json'
                }, 
                timeout=30)
            if resp.ok:
                content = resp.json().get('output',{}).get('text','').strip()
                # 过滤无关内容
                if '非体育' not in content and len(content) > 50:
                    return content
                    
    except Exception as e:
        if DEBUG_MODE: log(f"    AI失败: {e}")
    return None

def ai_enrich_batch(standards, force=False):
    provider = 'DeepSeek' if DEEPSEEK_KEY else ('通义千问' if QWEN_KEY else None)
    if not provider:
        log("  ⚠️  未配置AI Key，跳过AI摘要")
        return standards
    log(f"🤖 AI摘要（{provider}，{'强制全部更新' if force else '仅补缺'}）…")
    enriched = 0
    for i, std in enumerate(standards):
        if not force and std.get('summary','').strip():
            continue
        
        # 生成AI摘要
        ai_summary = ai_enrich_standard(std)
        if ai_summary and len(ai_summary) > 50:
            standards[i]['summary'] = ai_summary
            enriched += 1
            log(f"  ✅ [{std['code']}] {ai_summary[:40]}…")
        time.sleep(1.0)  # 增加延迟，避免API限流
    
    log(f"  完成：AI精准补全/更新 {enriched} 条摘要")
    return standards

# ============================================================
#  核查状态
# ============================================================
def check_status_online(std):
    code = std.get('code','')
    if not code: return None
    try:
        results, _ = fetch_samr(code, 1)
        for r in results:
            if norm_code(r['code']) == norm_code(code):
                ns = r['status']
                if ns and ns != std.get('status'):
                    upd = dict(std)
                    upd['status'] = ns
                    if ns == '废止':
                        upd['abolishDate'] = r.get('abolishDate') or datetime.now().strftime('%Y-%m-%d')
                    return upd
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] 状态核查异常: {e}")
    return None

# ============================================================
#  主流程
# ============================================================
def load_existing():
    """加载已有标准库"""
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding='utf-8'))
        except Exception:
            log("⚠️  现有数据文件损坏，将重建")
    return []

def save_standards(standards, dry_run=False):
    """保存标准库"""
    if dry_run:
        log(f"📤 预览保存：共{len(standards)}条标准（Dry Run模式，未实际写入）")
        return
    try:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(
            json.dumps(standards, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )
        log(f"📤 已保存：{len(standards)} 条标准到 {DATA_FILE}")
    except Exception as e:
        log(f"❌ 保存失败: {e}")

def clean_non_sports(standards):
    """清理库中非体育标准"""
    before = len(standards)
    filtered = [s for s in standards if is_sports(s.get('title',''))]
    after = len(filtered)
    log(f"🧹 清理非体育标准：{before} → {after} 条")
    return filtered

def main():
    parser = argparse.ArgumentParser(description='体育建设标准自动更新脚本')
    parser.add_argument('--check', action='store_true', help='仅核查标准状态')
    parser.add_argument('--ai', action='store_true', help='启用AI补全摘要')
    parser.add_argument('--debug', action='store_true', help='调试模式')
    parser.add_argument('--dry', action='store_true', help='Dry Run：预览不写入')
    args = parser.parse_args()

    global DEBUG_MODE
    DEBUG_MODE = args.debug

    log("="*60)
    log("🏟️  体育建设标准自动抓取 & 更新脚本 v9")
    log("="*60)

    # 加载已有数据
    existing = load_existing()
    log(f"📥 加载已有标准：{len(existing)} 条")

    # 启动时清理非体育标准
    existing = clean_non_sports(existing)

    if args.check:
        # 仅核查状态
        log("🔍 开始核查标准在线状态…")
        updated = 0
        for i, std in enumerate(existing):
            new_std = check_status_online(std)
            if new_std:
                existing[i] = new_std
                updated += 1
                log(f"  📝 [{std['code']}] 状态更新：{std['status']} → {new_std['status']}")
        log(f"✅ 状态核查完成，更新 {updated} 条")
        save_standards(existing, args.dry)
        return

    # 完整抓取流程
    log("🌐 开始抓取各来源标准…")
    all_new = []
    seen_codes = set([norm_code(s.get('code','')) for s in existing])

    # 1. 抓取samr.gov.cn（核心来源）
    log("🔹 抓取全国标准信息公共服务平台（samr.gov.cn）…")
    for i, keyword in enumerate(KEYWORDS):
        log(f"  [{i+1}/{len(KEYWORDS)}] 关键词：{keyword}")
        results = fetch_samr_all(keyword)
        for r in results:
            nc = norm_code(r['code'])
            if nc not in seen_codes:
                seen_codes.add(nc)
                entry = build_entry(r)
                all_new.append(entry)
                if DEBUG_MODE:
                    log(f"    新增: {entry['code']} - {entry['title'][:30]}…")
        time.sleep(0.8)  # 关键词间延迟

    # 2. 抓取ttbz.org.cn（团标）
    log("🔹 抓取团体标准平台（ttbz.org.cn）…")
    for keyword in KEYWORDS[:20]:  # 前20个核心关键词
        results = fetch_ttbz(keyword)
        for r in results:
            nc = norm_code(r['code'])
            if nc not in seen_codes:
                seen_codes.add(nc)
                entry = build_entry(r)
                all_new.append(entry)
        time.sleep(0.5)

    # 3. 抓取dbba.sacinfo.org.cn（地方标准）
    log("🔹 抓取地方标准平台（dbba.sacinfo.org.cn）…")
    for keyword in KEYWORDS[:15]:  # 前15个核心关键词
        results = fetch_dbba(keyword)
        for r in results:
            nc = norm_code(r['code'])
            if nc not in seen_codes:
                seen_codes.add(nc)
                entry = build_entry(r)
                all_new.append(entry)
        time.sleep(0.5)

    # 合并新旧数据
    log(f"🔹 合并数据：已有{len(existing)}条 + 新增{len(all_new)}条")
    combined = existing + all_new

    # 去重（按code）
    unique_combined = []
    seen_unique = set()
    for s in combined:
        nc = norm_code(s.get('code',''))
        if nc not in seen_unique:
            seen_unique.add(nc)
            unique_combined.append(s)
    log(f"🔹 去重后总数：{len(unique_combined)} 条")

    # 自动补全替代关系
    replaces_updated = auto_fill_replaces(unique_combined)
    log(f"🔗 自动补全版本替代关系：{replaces_updated} 条")

    # 自动补全摘要（规则版）
    auto_fill_summary(unique_combined)

    # AI补全摘要（可选）
    if args.ai:
        unique_combined = ai_enrich_batch(unique_combined)

    # 最终清理非体育标准
    final = clean_non_sports(unique_combined)

    # 保存结果
    save_standards(final, args.dry)

    log("="*60)
    log("✅ 全部完成！")
    log(f"📊 最终统计：{len(final)} 条体育建设标准")
    log("="*60)

if __name__ == '__main__':
    main()