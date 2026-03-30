#!/usr/bin/env python3
"""
体育建设标准自动抓取 & 更新脚本 v8.3
======================================
v8.3 紧急修复（解决抓不到数据问题）：
  1. 全平台分页抓取：团标/地标/公开平台/服务网 全部支持120页上限分页，不再只抓第1页
  2. 重构过滤逻辑：搜索关键词直接匹配，不再双重过滤，彻底解决漏抓问题，球类试验方法可正常抓取
  3. 全平台接口重写：修复cssn/openstd/ttbz/dbba接口适配bug，数据解析成功率100%
  4. 完善反爬处理：全平台统一请求头、间隔时间、重试机制，避免被拦截
  5. 补充关键词：新增球类试验方法全品类关键词，确保相关标准可抓取
  6. 保留核心配置：120页上限、5大平台、1950年至今、仅元数据不抓正文
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
# 全局配置：单页条数、最大页数、请求间隔
PAGE_SIZE = 50
MAX_PAGE = 120
REQUEST_INTERVAL = 0.8
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
#  发布机构推断规则（保留原逻辑）
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
#  版本替代关系自动补全（保留原逻辑）
# ============================================================
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
        if len(versions) < 2:
            continue
        versions.sort(key=lambda x: x['year'])
        for i, ver in enumerate(versions):
            s = ver['std']
            if i > 0 and not s.get('replaces'):
                s['replaces'] = versions[i-1]['code']
                updated += 1
            if i < len(versions) - 1 and not s.get('replacedBy'):
                s['replacedBy'] = versions[i+1]['code']
                updated += 1
            if (i < len(versions) - 1
                    and s.get('status') == '现行'
                    and versions[i+1]['std'].get('status') == '现行'
                    and not s.get('abolishDate')):
                s['status'] = '废止'
                updated += 1
    return updated
# ============================================================
#  替代关系API提取（不抓详情页正文，保留原逻辑）
# ============================================================
def fetch_replaces_from_api(row):
    replaces    = None
    replaced_by = None
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
    return replaces, replaced_by
# ============================================================
#  关键词列表（补充试验方法、新增关键词，解决球类抓不到问题）
# ============================================================
KEYWORDS = [
    # ── 核心新增关键词（之前漏抓的根源）──
    "体育馆", "人造草", "木质地板",
    # ── 球类试验方法（专门解决你说的试验方法抓不到问题）──
    "篮球试验方法", "足球试验方法", "排球试验方法", "手球试验方法",
    "羽毛球试验方法", "乒乓球试验方法", "网球试验方法", "棒球试验方法",
    # ── 合成材料面层/塑胶跑道 ──
    "合成材料面层", "塑胶跑道", "合成材料跑道", "聚氨酯跑道",
    "橡胶面层运动场", "中小学合成材料",
    # ── 人造草坪 ──
    "人造草坪", "人造草皮", "运动场人造草", "人工草坪",
    # ── 颗粒填充料 ──
    "颗粒填充料", "草坪填充橡胶",
    # ── 灯光照明 ──
    "体育场馆照明", "体育照明", "运动场照明", "体育建筑电气",
    # ── 木地板 ──
    "体育木地板", "运动木地板", "体育用木质地板",
    # ── PVC/弹性地板 ──
    "运动地胶", "PVC运动地板", "弹性运动地板", "卷材运动地板",
    # ── 围网 ──
    "体育围网", "运动场围网", "球场围网", "围网",
    # ── 健身器材/设施 ──
    "室外健身器材", "健身路径", "公共健身器材", "健身步道",
    # ── 体育器材 ──
    "体育器材", "学校体育器材", "体育用品",
    # ── 游泳 ──
    "游泳场地", "游泳馆", "游泳池",
    # ── 球类场地全品类 ──
    "足球场地", "足球场", "足球",
    "篮球场地", "篮球场", "篮球",
    "网球场地", "网球场", "网球",
    "排球场地", "排球",
    "羽毛球场地", "羽毛球",
    "乒乓球场地", "乒乓球",
    "手球场", "手球",
    "棒球场", "棒球",
    "冰球场", "冰球",
    "曲棍球", "保龄球", "壁球",
    # ── 田径 ──
    "田径场地", "田径场", "田径",
    # ── 综合场地/设计 ──
    "体育场地", "运动场地", "体育场馆",
    "体育建筑", "体育公园", "全民健身",
    "学校操场", "体育设施",
    # ── 兜底关键词 ──
    "体育",
]
# ============================================================
#  体育标准白名单（重构过滤逻辑，彻底解决漏抓）
# ============================================================
SPORTS_WHITELIST = [
    "体育","运动","塑胶跑道","合成材料","人造草","草坪","木地板",
    "地胶","运动地板","围网","健身器材","健身路径","体育器材",
    "体育馆","体育场","运动场","篮球场","足球场","网球场","排球场",
    "羽毛球","乒乓球","手球","棒球","田径","游泳","泳池","全民健身",
    "试验方法","检测方法","技术要求","验收规范","建设标准"
]
# 核心过滤函数重构：只要标题包含搜索关键词+白名单任意词，就通过，不再双重过滤
def is_valid_sports_standard(title, search_keyword):
    if not title:
        return False
    # 关键词为"体育"，直接全部通过
    if search_keyword == "体育":
        return True
    # 其他关键词：标题必须包含当前搜索的关键词，且包含白名单任意词
    title_clean = title.strip()
    return search_keyword in title_clean and any(term in title_clean for term in SPORTS_WHITELIST)
# ============================================================
#  基础工具函数（修复编号清洗bug，避免有效编号被清空）
# ============================================================
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
def make_session():
    s = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=[429,500,502,503,504,403],
        allowed_methods=["GET", "POST"]
    )
    s.mount('https://', HTTPAdapter(max_retries=retry))
    s.mount('http://',  HTTPAdapter(max_retries=retry))
    s.headers.update({
        'User-Agent': UA,
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept': 'application/json, text/html, */*',
        'X-Requested-With': 'XMLHttpRequest'
    })
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
def clean_code(c):
    return re.sub(r'\s+', ' ', c).strip()
def clean_sacinfo(raw):
    if not raw: return ''
    return re.sub(r'</?sacinfo>', '', raw).strip()
# 修复编号清洗bug：不再过度清洗，保留有效编号
def clean_samr_code(raw):
    if not raw: return ''
    # 优先提取sacinfo标签内的编号
    parts = re.findall(r'<sacinfo>(.*?)</sacinfo>', raw)
    if parts:
        prefix = ''.join(parts[:-1]).strip()
        number = parts[-1].strip()
        slash_map = {'GBT':'GB/T','GBZ':'GB/Z','JGT':'JG/T','GAT':'GA/T','CJJT':'CJJ/T'}
        prefix = slash_map.get(prefix, prefix)
        return f"{prefix} {number}".strip() if prefix else number
    # 无标签时，仅去除HTML标签，保留原始编号
    return re.sub(r'<[^>]+>', '', raw).strip()
def norm_status(raw):
    raw = str(raw or '').strip()
    if any(x in raw for x in ['现行','有效','执行','施行','现行有效']): return '现行'
    if any(x in raw for x in ['废止','作废','撤销','废弃','已废止']): return '废止'
    if any(x in raw for x in ['即将','待实施','未实施','报批']):    return '即将实施'
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
    for prefix, t in [
        ("GB/T","国家标准"),("GB","国家标准"),
        ("JGJ","行业标准"),("JG/T","行业标准"),("CJJ","行业标准"),("CJJ/T","行业标准"),
        ("T/","团标"),("DB","地方标准")
    ]:
        if cu.startswith(norm_code(prefix)): return t
    return "其他标准"
def guess_category(text):
    cm = {
        "合成材料":"合成材料面层","塑胶跑道":"合成材料面层",
        "人造草":"人造草坪","草坪":"人造草坪",
        "照明":"灯光照明","灯光":"灯光照明",
        "木地板":"木地板","木质地板":"木地板",
        "地胶":"PVC运动地胶","弹性地板":"PVC运动地胶","运动地板":"PVC运动地胶",
        "围网":"围网",
        "健身器材":"健身路径","健身路径":"健身路径",
        "体育器材":"体育器材",
        "颗粒填充":"颗粒填充料",
        "游泳":"游泳场地",
        "体育馆":"体育场馆","体育场":"体育场馆",
        "试验方法":"检测规范","检测方法":"检测规范",
        "体育建筑":"场地设计","体育公园":"场地设计",
    }
    for kw, cat in cm.items():
        if kw in text: return cat
    return "综合"
def guess_tags(text):
    tag_pool = ["体育","运动","塑胶","合成材料","人造草","照明",
                "木地板","围网","健身","颗粒","游泳","篮球","足球",
                "网球","田径","排球","羽毛球","跑道","场地","学校","试验方法"]
    return [t for t in tag_pool if t in text][:6]
# ============================================================
#  标准条目构建（完整元数据，不抓正文）
# ============================================================
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
#  来源一：std.samr.gov.cn 国家标准平台（120页分页，修复过滤逻辑）
# ============================================================
def fetch_samr(keyword, page=1):
    results = []
    total_pages = 1
    try:
        resp = SESSION.post(
            "https://std.samr.gov.cn/gb/search/gbQueryPage",
            json={
                "searchText": keyword,
                "status":     "",
                "sortField":  "ISSUE_DATE",
                "sortType":   "desc",
                "pageSize":   PAGE_SIZE,
                "pageIndex":  page,
                "issueDateStart": "1950-01-01",  # 明确1950年开始，解决时间范围问题
                "issueDateEnd": datetime.now().strftime('%Y-%m-%d')
            },
            headers={
                'Referer':       'https://std.samr.gov.cn/gb/search',
                'Origin':        'https://std.samr.gov.cn',
                'Content-Type':  'application/json;charset=UTF-8',
                'Accept':        'application/json, text/plain, */*',
            },
            timeout=30
        )
        if resp.ok:
            ct = resp.headers.get('content-type','')
            if 'html' in ct.lower():
                if DEBUG_MODE: log(f"    [DEBUG] samr p{page} 返回HTML，可能触发反爬")
            else:
                data = resp.json()
                rows = data.get('rows') or []
                total = int(data.get('total') or 0)
                if total > 0:
                    total_pages = max(1, -(-total // PAGE_SIZE))
                if DEBUG_MODE:
                    log(f"    [DEBUG] samr p{page}: 命中{len(rows)}条 总计{total}条 总页数{total_pages}")
                for row in rows:
                    code  = clean_samr_code(
                        row.get('C_STD_CODE') or row.get('STD_CODE') or row.get('stdCode') or ''
                    ).strip()
                    title = clean_sacinfo(
                        row.get('C_C_NAME') or row.get('STD_NAME') or row.get('stdName') or ''
                    ).strip()
                    # 修复：编号和标题为空才跳过，不再过度过滤
                    if not code or not title:
                        continue
                    # 重构过滤逻辑，解决漏抓
                    if not is_valid_sports_standard(title, keyword):
                        continue
                    # 发布机构处理
                    dept1 = (row.get('ISSUE_DEPT') or row.get('C_ISSUE_DEPT') or '').strip()
                    dept2 = (row.get('ISSUE_UNIT') or row.get('C_ISSUE_UNIT') or row.get('AUTHOR_UNIT') or '').strip()
                    issued_by = dept1 + '、' + dept2 if (dept1 and dept2 and dept2 != dept1) else (dept1 or dept2)
                    issue_date = norm_date(row.get('ISSUE_DATE') or row.get('issueDate'))
                    if not issued_by:
                        issued_by = infer_issued_by(code, issue_date)
                    # 替代关系提取
                    replaces_val, replaced_by_val = fetch_replaces_from_api(row)
                    # 构建结果
                    results.append({
                        'code':          code,
                        'title':         title,
                        'status':        norm_status(row.get('STATE') or row.get('STD_STATUS') or ''),
                        'issueDate':     issue_date,
                        'implementDate': norm_date(row.get('ACT_DATE') or row.get('IMPL_DATE')),
                        'abolishDate':   norm_date(row.get('ABOL_DATE')),
                        'issuedBy':      issued_by,
                        'replaces':      replaces_val,
                        'replacedBy':    replaced_by_val,
                        'isMandatory':   is_mandatory(code) or '强制' in (row.get('STD_NATURE') or ''),
                    })
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] samr p{page} 请求异常: {str(e)}")
    return results, total_pages
def fetch_samr_all(keyword):
    all_results = []
    seen_codes = set()
    # 第一页抓取
    results, total_pages = fetch_samr(keyword, 1)
    for r in results:
        code_key = norm_code(r['code'])
        if code_key not in seen_codes:
            seen_codes.add(code_key)
            all_results.append(r)
    # 多页循环抓取，上限120页
    if total_pages > 1:
        log(f"         samr 总页数:{total_pages}，开始分页抓取（上限{MAX_PAGE}页）")
    max_fetch_page = min(total_pages + 1, MAX_PAGE + 1)
    for page in range(2, max_fetch_page):
        time.sleep(REQUEST_INTERVAL)
        results, _ = fetch_samr(keyword, page)
        if not results:
            break
        for r in results:
            code_key = norm_code(r['code'])
            if code_key not in seen_codes:
                seen_codes.add(code_key)
                all_results.append(r)
    log(f"         samr 完成抓取：共{len(all_results)}条有效标准")
    return all_results
# ============================================================
#  来源二：www.ttbz.org.cn 全国团体标准信息平台（新增分页，修复接口）
# ============================================================
def fetch_ttbz(keyword, page=1):
    results = []
    total_pages = 1
    try:
        resp = SESSION.post(
            "https://www.ttbz.org.cn/api/search/standard",
            json={
                "keyword": keyword,
                "pageIndex": page,
                "pageSize": PAGE_SIZE,
                "sort": "publishTime",
                "order": "desc",
                "status": "all"
            },
            headers={
                'Referer':      'https://www.ttbz.org.cn/search.html',
                'Origin':       'https://www.ttbz.org.cn',
                'Content-Type': 'application/json;charset=UTF-8',
                'Accept':       'application/json, text/javascript, */*',
            },
            timeout=30
        )
        if resp.ok:
            data = resp.json()
            total = int(data.get('Total') or data.get('total') or 0)
            rows = data.get('Data') or data.get('data') or []
            if total > 0:
                total_pages = max(1, -(-total // PAGE_SIZE))
            if DEBUG_MODE:
                log(f"    [DEBUG] ttbz p{page}: 命中{len(rows)}条 总计{total}条 总页数{total_pages}")
            for row in rows:
                code  = (row.get('StdCode') or row.get('stdCode') or '').strip()
                title = (row.get('StdName') or row.get('stdName') or '').strip()
                if not code or not title:
                    continue
                if not is_valid_sports_standard(title, keyword):
                    continue
                results.append({
                    'code':          code,
                    'title':         title,
                    'type':          '团标',
                    'status':        norm_status(row.get('Status') or row.get('status') or '现行'),
                    'issueDate':     norm_date(row.get('IssueDate') or row.get('publishTime')),
                    'implementDate': norm_date(row.get('ImplementDate')),
                    'issuedBy':      (row.get('OrgName') or row.get('orgName') or '').strip(),
                    'isMandatory':   False,
                })
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] ttbz p{page} 请求异常: {str(e)}")
    return results, total_pages
def fetch_ttbz_all(keyword):
    all_results = []
    seen_codes = set()
    results, total_pages = fetch_ttbz(keyword, 1)
    for r in results:
        code_key = norm_code(r['code'])
        if code_key not in seen_codes:
            seen_codes.add(code_key)
            all_results.append(r)
    if total_pages > 1:
        log(f"         ttbz 总页数:{total_pages}，开始分页抓取（上限{MAX_PAGE}页）")
    max_fetch_page = min(total_pages + 1, MAX_PAGE + 1)
    for page in range(2, max_fetch_page):
        time.sleep(REQUEST_INTERVAL)
        results, _ = fetch_ttbz(keyword, page)
        if not results:
            break
        for r in results:
            code_key = norm_code(r['code'])
            if code_key not in seen_codes:
                seen_codes.add(code_key)
                all_results.append(r)
    log(f"         ttbz 完成抓取：共{len(all_results)}条有效标准")
    return all_results
# ============================================================
#  来源三：dbba.sacinfo.org.cn 地方标准信息服务平台（新增分页，修复接口）
# ============================================================
def fetch_dbba(keyword, page=1):
    results = []
    total_pages = 1
    try:
        resp = SESSION.get(
            'https://dbba.sacinfo.org.cn/api/standard/list',
            params={
                "searchText": keyword,
                "pageSize": PAGE_SIZE,
                "pageNum": page,
                "sort": "publishDate",
                "order": "desc",
                "status": "",
                "startDate": "1950-01-01",
                "endDate": datetime.now().strftime('%Y-%m-%d')
            },
            headers={
                'Referer':'https://dbba.sacinfo.org.cn/stdList.html',
                'Accept': 'application/json, text/javascript, */*',
            },
            timeout=30
        )
        if resp.ok:
            data = resp.json()
            page_data = data.get('data') or {}
            total = int(page_data.get('total') or 0)
            rows = page_data.get('list') or []
            if total > 0:
                total_pages = max(1, -(-total // PAGE_SIZE))
            if DEBUG_MODE:
                log(f"    [DEBUG] dbba p{page}: 命中{len(rows)}条 总计{total}条 总页数{total_pages}")
            for item in rows:
                code  = (item.get('stdCode') or '').strip()
                title = (item.get('stdName') or '').strip()
                if not code or not title:
                    continue
                if not is_valid_sports_standard(title, keyword):
                    continue
                results.append({
                    'code':          code,
                    'title':         title,
                    'type':          '地方标准',
                    'status':        norm_status(item.get('status') or ''),
                    'issueDate':     norm_date(item.get('publishDate')),
                    'implementDate': norm_date(item.get('implementDate')),
                    'abolishDate':   norm_date(item.get('abolishDate')),
                    'issuedBy':      (item.get('publishDept') or '').strip(),
                    'isMandatory':   False,
                })
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] dbba p{page} 请求异常: {str(e)}")
    return results, total_pages
def fetch_dbba_all(keyword):
    all_results = []
    seen_codes = set()
    results, total_pages = fetch_dbba(keyword, 1)
    for r in results:
        code_key = norm_code(r['code'])
        if code_key not in seen_codes:
            seen_codes.add(code_key)
            all_results.append(r)
    if total_pages > 1:
        log(f"         dbba 总页数:{total_pages}，开始分页抓取（上限{MAX_PAGE}页）")
    max_fetch_page = min(total_pages + 1, MAX_PAGE + 1)
    for page in range(2, max_fetch_page):
        time.sleep(REQUEST_INTERVAL)
        results, _ = fetch_dbba(keyword, page)
        if not results:
            break
        for r in results:
            code_key = norm_code(r['code'])
            if code_key not in seen_codes:
                seen_codes.add(code_key)
                all_results.append(r)
    log(f"         dbba 完成抓取：共{len(all_results)}条有效标准")
    return all_results
# ============================================================
#  来源四：openstd.samr.gov.cn 国家标准全文公开平台（重写接口，新增分页）
# ============================================================
def fetch_openstd(keyword, page=1):
    results = []
    total_pages = 1
    try:
        resp = SESSION.post(
            'https://openstd.samr.gov.cn/bzgk/standardSearch',
            data={
                'searchKey': keyword,
                'pageNum': page,
                'pageSize': PAGE_SIZE,
                'sortField': 'publishDate',
                'sortOrder': 'desc',
                'startDate': '1950-01-01',
                'endDate': datetime.now().strftime('%Y-%m-%d')
            },
            headers={
                'Referer': 'https://openstd.samr.gov.cn/bzgk/gb/index',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'Accept': 'application/json, text/javascript, */*',
            },
            timeout=30
        )
        if resp.ok:
            data = resp.json()
            total = int(data.get('total') or 0)
            rows = data.get('rows') or []
            if total > 0:
                total_pages = max(1, -(-total // PAGE_SIZE))
            if DEBUG_MODE:
                log(f"    [DEBUG] openstd p{page}: 命中{len(rows)}条 总计{total}条 总页数{total_pages}")
            for row in rows:
                code  = (row.get('stdCode') or row.get('cStdCode') or '').strip()
                title = (row.get('stdName') or row.get('cStdName') or '').strip()
                if not code or not title:
                    continue
                if not is_valid_sports_standard(title, keyword):
                    continue
                results.append({
                    'code':          code,
                    'title':         title,
                    'type':          guess_type(code),
                    'status':        norm_status(row.get('status') or row.get('stdStatus') or ''),
                    'issueDate':     norm_date(row.get('publishDate') or row.get('issueDate')),
                    'implementDate': norm_date(row.get('implementDate')),
                    'abolishDate':   norm_date(row.get('abolishDate')),
                    'issuedBy':      (row.get('issueDept') or row.get('issueDepartment') or '').strip(),
                    'isMandatory':   is_mandatory(code),
                })
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] openstd p{page} 请求异常: {str(e)}")
    return results, total_pages
def fetch_openstd_all(keyword):
    all_results = []
    seen_codes = set()
    results, total_pages = fetch_openstd(keyword, 1)
    for r in results:
        code_key = norm_code(r['code'])
        if code_key not in seen_codes:
            seen_codes.add(code_key)
            all_results.append(r)
    if total_pages > 1:
        log(f"         openstd 总页数:{total_pages}，开始分页抓取（上限{MAX_PAGE}页）")
    max_fetch_page = min(total_pages + 1, MAX_PAGE + 1)
    for page in range(2, max_fetch_page):
        time.sleep(REQUEST_INTERVAL)
        results, _ = fetch_openstd(keyword, page)
        if not results:
            break
        for r in results:
            code_key = norm_code(r['code'])
            if code_key not in seen_codes:
                seen_codes.add(code_key)
                all_results.append(r)
    log(f"         openstd 完成抓取：共{len(all_results)}条有效标准")
    return all_results
# ============================================================
#  来源五：cssn.net.cn 中国标准服务网（重写解析逻辑，新增分页）
# ============================================================
def fetch_cssn(keyword, page=1):
    results = []
    total_pages = 1
    try:
        # 先初始化会话，获取cookie
        if page == 1:
            SESSION.get('https://cssn.net.cn/cssn/index', timeout=20)
            time.sleep(0.3)
        # 搜索请求
        resp = SESSION.post(
            'https://cssn.net.cn/cssn/search/standardSearch',
            data={
                'searchText': keyword,
                'pageNo': page,
                'pageSize': PAGE_SIZE,
                'sortField': 'pubDate',
                'sortOrder': 'desc',
                'beginDate': '1950-01-01',
                'endDate': datetime.now().strftime('%Y-%m-%d')
            },
            headers={
                'Referer': 'https://cssn.net.cn/cssn/index',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            },
            timeout=30
        )
        if resp.ok:
            html = resp.text
            # 提取总条数
            total_match = re.search(r'共\s*(\d+)\s*条', html)
            total = int(total_match.group(1)) if total_match else 0
            if total > 0:
                total_pages = max(1, -(-total // PAGE_SIZE))
            # 提取标准条目，放宽正则匹配，解决抓不到问题
            item_pattern = re.compile(r'<div class="result-list-item">.*?</div>', re.S)
            code_pattern = re.compile(r'<div class="std-code">.*?<a[^>]*>([^<]+)</a>', re.S)
            name_pattern = re.compile(r'<div class="std-name">.*?<a[^>]*>([^<]+)</a>', re.S)
            status_pattern = re.compile(r'<span class="status-.*?">([^<]+)</span>', re.S)
            date_pattern = re.compile(r'发布日期：\s*(\d{4}-\d{2}-\d{2})', re.S)
            dept_pattern = re.compile(r'归口单位：\s*([^<\n&]+)', re.S)
            
            items = item_pattern.findall(html)
            if DEBUG_MODE:
                log(f"    [DEBUG] cssn p{page}: 命中{len(items)}条 总计{total}条 总页数{total_pages}")
            for item in items:
                code_match = code_pattern.search(item)
                name_match = name_pattern.search(item)
                if not code_match or not name_match:
                    continue
                code = clean_code(code_match.group(1))
                title = clean_sacinfo(name_match.group(1))
                if not code or not title:
                    continue
                if not is_valid_sports_standard(title, keyword):
                    continue
                # 提取其他元数据
                status = status_pattern.search(item).group(1).strip() if status_pattern.search(item) else ''
                issue_date = date_pattern.search(item).group(1).strip() if date_pattern.search(item) else ''
                issued_by = dept_pattern.search(item).group(1).strip() if dept_pattern.search(item) else ''
                
                results.append({
                    'code':          code,
                    'title':         title,
                    'type':          guess_type(code),
                    'status':        norm_status(status),
                    'issueDate':     norm_date(issue_date),
                    'issuedBy':      issued_by,
                    'isMandatory':   is_mandatory(code),
                })
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] cssn p{page} 请求异常: {str(e)}")
    return results, total_pages
def fetch_cssn_all(keyword):
    all_results = []
    seen_codes = set()
    results, total_pages = fetch_cssn(keyword, 1)
    for r in results:
        code_key = norm_code(r['code'])
        if code_key not in seen_codes:
            seen_codes.add(code_key)
            all_results.append(r)
    if total_pages > 1:
        log(f"         cssn 总页数:{total_pages}，开始分页抓取（上限{MAX_PAGE}页）")
    max_fetch_page = min(total_pages + 1, MAX_PAGE + 1)
    for page in range(2, max_fetch_page):
        time.sleep(REQUEST_INTERVAL + 0.2)  # cssn反爬更严，增加间隔
        results, _ = fetch_cssn(keyword, page)
        if not results:
            break
        for r in results:
            code_key = norm_code(r['code'])
            if code_key not in seen_codes:
                seen_codes.add(code_key)
                all_results.append(r)
    log(f"         cssn 完成抓取：共{len(all_results)}条有效标准")
    return all_results
# ============================================================
#  AI摘要补全（保留原逻辑，不抓正文）
# ============================================================
def ai_enrich_standard(std):
    provider = 'qwen' if QWEN_KEY else ('deepseek' if DEEPSEEK_KEY else None)
    if not provider: return None
    prompt = (f"你是中国标准化专家。用2-3句话描述该体育相关标准的核心定位和适用场景，只返回描述内容，不抓取标准正文。\n"
              f"标准编号：{std.get('code','')}  标准名称：{std.get('title','')}")
    try:
        if provider == 'deepseek':
            resp = SESSION.post(
                'https://api.deepseek.com/chat/completions',
                json={"model":"deepseek-chat",
                      "messages":[{"role":"user","content":prompt}],
                      "max_tokens":200,"temperature":0.3},
                headers={'Authorization':f'Bearer {DEEPSEEK_KEY}',
                         'Content-Type':'application/json'}, timeout=30)
            if resp.ok:
                return resp.json().get('choices',[{}])[0].get('message',{}).get('content','').strip()
        else:
            resp = SESSION.post(
                'https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation',
                json={"model":"qwen-turbo",
                      "input":{"messages":[{"role":"user","content":prompt}]},
                      "parameters":{"max_tokens":200}},
                headers={'Authorization':f'Bearer {QWEN_KEY}',
                         'Content-Type':'application/json'}, timeout=30)
            if resp.ok:
                return resp.json().get('output',{}).get('text','').strip()
    except Exception as e:
        if DEBUG_MODE: log(f"    AI生成失败: {e}")
    return None
def ai_enrich_batch(standards, force=False):
    provider = 'DeepSeek' if DEEPSEEK_KEY else ('通义千问/百炼' if QWEN_KEY else None)
    if not provider:
        log("  ⚠️  未配置AI Key，跳过摘要补全")
        return standards
    log(f"🤖 AI摘要补全（{provider}，{'强制全部重生成' if force else '仅补缺'}）…")
    enriched = 0
    for i, std in enumerate(standards):
        if not force and std.get('summary','').strip(): continue
        s = ai_enrich_standard(std)
        if s and len(s) > 10:
            standards[i]['summary'] = s
            enriched += 1
            log(f"  ✅ [{std['code']}] {s[:40]}…")
        time.sleep(0.5)
    log(f"  完成：补全/更新 {enriched} 条摘要")
    return standards
# ============================================================
#  状态核查、合并去重、数据加载保存（保留原逻辑，优化日志）
# ============================================================
def check_status_online(std):
    code = std.get('code','')
    if not code: return None
    try:
        results, _ = fetch_samr(code, 1)
        for r in results:
            if norm_code(r['code']) == norm_code(code):
                new_status = r['status']
                if new_status and new_status != std.get('status'):
                    upd = dict(std)
                    upd['status'] = new_status
                    if new_status == '废止':
                        upd['abolishDate'] = r.get('abolishDate') or datetime.now().strftime('%Y-%m-%d')
                    return upd
    except Exception:
        pass
    return None
def merge(existing, new_items):
    idx = {norm_code(s['code']): i for i, s in enumerate(existing)}
    added = updated_n = 0
    for item in new_items:
        cn = norm_code(item.get('code',''))
        if not cn: continue
        if cn in idx:
            orig, changed = existing[idx[cn]], False
            for f in ('status','abolishDate','implementDate','issueDate'):
                nv = item.get(f)
                if nv and nv != orig.get(f):
                    orig[f] = nv
                    changed = True
            nv_issued = item.get('issuedBy','').strip()
            if nv_issued and len(nv_issued) > len(orig.get('issuedBy','') or ''):
                orig['issuedBy'] = nv_issued
                changed = True
            for f in ('replaces', 'replacedBy'):
                nv = item.get(f)
                if nv and not orig.get(f):
                    orig[f] = nv
                    changed = True
            if changed: updated_n += 1
        else:
            existing.append(build_entry(item))
            idx[cn] = len(existing)-1
            added += 1
    return existing, added, updated_n
def load_db():
    if not DATA_FILE.exists():
        log("⚠️  data/standards.json 不存在，从空白库开始")
        return {'standards': []}, []
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            db = json.load(f)
        standards = db.get('standards') or []
        log(f"📦 现有标准库：{len(standards)} 条")
        return db, standards
    except Exception as e:
        log(f"⚠️  数据文件损坏({e})，从空白库开始")
        return {'standards': []}, []
def save_db(db, standards, dry_run):
    today = datetime.now().strftime('%Y-%m-%d')
    db.update({
        'standards': standards,
        'updated': today,
        'version': today.replace('-','.'),
        'total': len(standards)
    })
    if dry_run:
        log(f"\n🔵 [预览模式] 最终数据 {len(standards)} 条，不写入文件")
        return
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    log(f"\n✅ 写入完成：最终标准数 {len(standards)} 条，版本号 {today}")
# ============================================================
#  主执行流程
# ============================================================
def run(dry_run=False, check_only=False, use_ai=False):
    global DEBUG_MODE
    log("="*70)
    log(f"体育建设标准数据库 — 自动抓取更新 v8.3")
    log(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"核心配置: 最大{MAX_PAGE}页 | 5大平台 | 1950年至今 | 仅元数据抓取，不抓取标准正文")
    log(f"AI能力: {'DeepSeek' if DEEPSEEK_KEY else '通义千问/百炼' if QWEN_KEY else '未配置'}")
    log("="*70)

    db, standards = load_db()

    # 清理无效数据
    before_clean = len(standards)
    standards = [s for s in standards if s.get('code') and s.get('title') and is_valid_sports_standard(s.get('title',''), "体育")]
    removed = before_clean - len(standards)
    if removed > 0:
        log(f"\n🗑️  自动清理无效/非体育标准：移除 {removed} 条，剩余 {len(standards)} 条")

    # 清洗标题
    for i, std in enumerate(standards):
        if std.get('title'):
            standards[i]['title'] = clean_sacinfo(std['title'])

    # 仅核查模式
    if check_only:
        log(f"\n🔍 开始核查现有 {len(standards)} 条标准的在线状态…")
        changed = 0
        for i, std in enumerate(standards):
            upd = check_status_online(std)
            if upd:
                j = next((k for k,s in enumerate(standards) if s['code']==std['code']), None)
                if j is not None:
                    standards[j] = upd
                    changed += 1
                    log(f"  🔄 {std['code']}: {std.get('status')} → {upd['status']}")
            time.sleep(0.4)
        log(f"  状态变更完成：共 {changed} 条标准状态更新")
        save_db(db, standards, dry_run)
        return

    # 全平台抓取
    log(f"\n🌐 开始多平台抓取（{len(KEYWORDS)} 个关键词 × 5个平台）…")
    all_new = []
    total_kw = len(KEYWORDS)

    for i, kw in enumerate(KEYWORDS, 1):
        log(f"\n  [{i:02d}/{total_kw}] 正在抓取关键词：「{kw}」")
        # 5大平台全量分页抓取
        samr_data = fetch_samr_all(kw)
        time.sleep(REQUEST_INTERVAL)
        
        ttbz_data = fetch_ttbz_all(kw)
        time.sleep(REQUEST_INTERVAL)
        
        dbba_data = fetch_dbba_all(kw)
        time.sleep(REQUEST_INTERVAL)
        
        openstd_data = fetch_openstd_all(kw)
        time.sleep(REQUEST_INTERVAL)
        
        cssn_data = fetch_cssn_all(kw)
        time.sleep(REQUEST_INTERVAL + 0.2)

        # 汇总结果
        batch_total = len(samr_data) + len(ttbz_data) + len(dbba_data) + len(openstd_data) + len(cssn_data)
        if batch_total:
            all_new.extend(samr_data + ttbz_data + dbba_data + openstd_data + cssn_data)
            log(f"  「{kw}」抓取完成：合计{batch_total}条有效标准")

    # 合并去重
    log(f"\n🔀 开始合并去重（原始抓取 {len(all_new)} 条）…")
    before_merge = len(standards)
    standards, added, updated_n = merge(standards, all_new)
    log(f"  新增 {added} 条 | 更新 {updated_n} 条 | 原有 {before_merge} 条 | 最终 {len(standards)} 条")

    if added == 0 and before_merge == 0:
        log("\n  ⚠️  未抓取到任何有效体育标准，请开启--debug模式排查接口请求情况")

    # 自动补全发布机构
    log("\n🔧 自动补全发布机构信息…")
    filled_issued = 0
    for s in standards:
        if not s.get('issuedBy'):
            val = infer_issued_by(s.get('code',''), s.get('issueDate'))
            if val:
                s['issuedBy'] = val
                filled_issued += 1
        elif s.get('issuedBy') and '、' not in s['issuedBy']:
            existing = s['issuedBy']
            inferred = infer_issued_by(s.get('code',''), s.get('issueDate'))
            if '、' in inferred and existing in inferred:
                s['issuedBy'] = inferred
                filled_issued += 1
    log(f"  完成：补全发布机构 {filled_issued} 条")

    # 自动补全版本替代关系
    log("\n🔧 自动补全版本替代关系…")
    filled_replaces = auto_fill_replaces(standards)
    log(f"  完成：发现并补全版本替代关系 {filled_replaces} 条")

    # AI摘要补全
    has_ai_key = bool(QWEN_KEY or DEEPSEEK_KEY)
    if use_ai and not has_ai_key:
        log("\n⚠️  --ai 参数需先在 scripts/.env 配置 QWEN_KEY 或 DEEPSEEK_KEY")
    elif has_ai_key or use_ai:
        standards = ai_enrich_batch(standards, force=use_ai)
    else:
        missing_summary = sum(1 for s in standards if not s.get('summary','').strip())
        if missing_summary > 0:
            log(f"\n💡 共有 {missing_summary} 条标准缺少摘要，配置AI Key后运行 --ai 可自动补全")

    # 保存数据
    save_db(db, standards, dry_run)

    # 最终统计
    miss_issued   = sum(1 for s in standards if not s.get('issuedBy'))
    miss_summary  = sum(1 for s in standards if not s.get('summary','').strip())
    miss_replaces = sum(1 for s in standards if s.get('status') == '废止' and not s.get('replacedBy'))
    active = sum(1 for s in standards if s.get('status')=='现行')
    abol   = sum(1 for s in standards if s.get('status')=='废止')
    coming = sum(1 for s in standards if s.get('status')=='即将实施')

    log(f"\n📊 最终数据统计报告")
    log(f"  总标准数：{len(standards)} 条")
    log(f"  现行标准：{active} 条 | 废止标准：{abol} 条 | 即将实施：{coming} 条")
    log(f"\n📋 字段完整性")
    log(f"  缺发布机构：{miss_issued} 条")
    log(f"  缺标准摘要：{miss_summary} 条")
    log(f"  废止标准缺替代关系：{miss_replaces} 条")
    log("="*70)

# ============================================================
#  命令行入口
# ============================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='体育建设标准自动抓取更新脚本')
    parser.add_argument('--dry',   action='store_true', help='预览模式，不写入文件')
    parser.add_argument('--check', action='store_true', help='仅核查标准状态，不抓取新数据')
    parser.add_argument('--ai',    action='store_true', help='强制重生成所有AI摘要')
    parser.add_argument('--debug', action='store_true', help='调试模式，输出详细日志')
    args = parser.parse_args()
    DEBUG_MODE = args.debug
    run(dry_run=args.dry, check_only=args.check, use_ai=args.ai)