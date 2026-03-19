#!/usr/bin/env python3
"""
体育建设标准自动抓取 & 更新脚本 v7
======================================
v7 改进：
  - 增加 --debug 模式，打印 API 原始返回内容，便于排查
  - 修复 dbba JSON 解析错误（接口返回 HTML 时自动跳过）
  - samr 增加多种请求格式兼容
  - 新增 openstd 接口（与 samr 不同入口）
  - 增加 mohurd 住建部接口（行业标准来源）
  - AI摘要支持阿里云百炼/通义千问/DeepSeek

运行方式：
  python scripts/update_standards.py           # 完整抓取
  python scripts/update_standards.py --debug   # 调试模式（打印原始返回）
  python scripts/update_standards.py --check   # 仅核查状态
  python scripts/update_standards.py --ai      # 启用AI补全摘要
  python scripts/update_standards.py --dry     # 预览不写入
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

DEBUG_MODE = False  # 由 --debug 参数控制

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
#  关键词
# ============================================================
KEYWORDS = [
    "合成材料面层", "塑胶跑道", "合成材料跑道",
    "人造草坪", "人造草", "运动场草坪",
    "体育照明", "体育场馆照明", "运动场照明",
    "体育木地板", "运动木地板", "弹性地板",
    "体育围网", "运动场围网",
    "室外健身器材", "健身路径",
    "体育场地", "运动场地", "体育场馆",
    "颗粒填充料", "橡胶颗粒",
    "足球场地", "篮球场地", "网球场地",
    "田径场地", "游泳场地", "排球场地", "羽毛球场地",
    "学校操场", "体育设施",
    "PVC运动地板", "运动地胶",
]

# 行业标准专用关键词
INDUSTRY_KEYWORDS = [
    "体育场馆照明", "体育建筑设计",
    "运动场地面层", "体育场地验收",
    "健身器材安全", "体育围网安装",
    "运动木地板安装", "弹性运动地板",
    "体育场馆建设", "全民健身设施",
]

# 地方标准专用关键词
LOCAL_KEYWORDS = [
    "合成材料面层 学校", "塑胶跑道 有害物质",
    "人造草坪 运动场", "体育场地 验收",
    "运动场地 地方标准", "学校操场 安全",
    "健身器材 室外", "体育设施 建设",
    "运动木地板 安装", "体育围网 规范",
]

# 团体标准专用关键词
GROUP_KEYWORDS = [
    "合成材料跑道 施工验收",
    "人造草坪 施工验收",
    "运动地板 施工验收",
    "体育场地照明 LED",
    "健身器材 团体标准",
    "运动场围网 团体标准",
    "体育木地板 团体标准",
    "颗粒填充 橡胶 团体",
]

SPORTS_KW = [
    "体育","运动","健身","竞技","跑道","操场","球场","场馆",
    "合成材料","人造草","草坪","塑胶","围网","木地板","PVC",
    "弹性地板","颗粒","游泳","篮球","足球","网球","排球",
    "羽毛球","田径","乒乓","健身器材","灯光",
]

STD_CODE_RE = re.compile(
    r'\b(GB[\s/T]*\d+[\-\.]\d{4}|JG[J/T]*[\s]*\d+[\-\.]\d{4}|'
    r'CJJ[\s]*\d+[\-\.]\d{4}|T/[A-Z]+[\s]*\d+[\-\.]\d{4}|'
    r'DB\w+/[T]?[\s]*\d+[\-\.]\d{4})\b'
)

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36')

# ============================================================
#  工具
# ============================================================
def make_session():
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=1, status_forcelist=[429,500,502,503,504])
    s.mount('https://', HTTPAdapter(max_retries=retry))
    s.mount('http://',  HTTPAdapter(max_retries=retry))
    s.headers.update({'User-Agent': UA, 'Accept-Language': 'zh-CN,zh;q=0.9'})
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

def dbg(label, resp):
    """调试模式下打印原始返回"""
    if not DEBUG_MODE:
        return
    ct = resp.headers.get('content-type','')
    log(f"    [DEBUG] {label} status={resp.status_code} content-type={ct}")
    try:
        text = resp.text[:500]
        log(f"    [DEBUG] 原始返回前500字符: {text}")
    except Exception:
        pass

def make_id(code):
    clean = re.sub(r'[^A-Za-z0-9]', '', code)
    return clean[:30] if clean else hashlib.md5(code.encode()).hexdigest()[:12]

def norm_code(c):
    return re.sub(r'\s+', '', c).upper()

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

def guess_type(code):
    cu = code.upper()
    for prefix, t in [("GB/T","国家标准"),("GB","国家标准"),("JGJ","行业标准"),
                       ("JG/T","行业标准"),("CJJ","行业标准"),("T/","团标"),("DB","地方标准")]:
        if cu.replace(' ','').startswith(prefix.replace(' ','').upper()): return t
    return "国家标准"

def guess_category(text):
    cm = {
        "合成材料":"合成材料面层","塑胶跑道":"合成材料面层",
        "人造草":"人造草坪","草坪":"人造草坪",
        "照明":"灯光照明","灯光":"灯光照明",
        "木地板":"木地板",
        "PVC":"PVC运动地胶","弹性地板":"PVC运动地胶","地胶":"PVC运动地胶",
        "围网":"围网",
        "健身器材":"健身路径","健身路径":"健身路径",
        "体育器材":"体育器材",
        "颗粒":"颗粒填充料","橡胶颗粒":"颗粒填充料",
        "游泳":"游泳场地",
        "建筑":"场地设计","设计规范":"场地设计",
    }
    for kw, cat in cm.items():
        if kw in text: return cat
    return "综合"

def guess_tags(text):
    candidates = ["体育","运动","塑胶","合成材料","人造草","照明","木地板","PVC",
                  "围网","健身","器材","颗粒","游泳","篮球","足球","网球","田径",
                  "排球","羽毛球","跑道","场地","操场","中小学","学校","安全"]
    return [t for t in candidates if t in text][:6]

def build_entry(item):
    code, title = item.get('code',''), item.get('title','')
    return {
        'id':            make_id(code),
        'code':          code,
        'title':         title,
        'english':       item.get('english',''),
        'type':          item.get('type') or guess_type(code),
        'status':        item.get('status','现行'),
        'issueDate':     item.get('issueDate') or None,
        'implementDate': item.get('implementDate') or None,
        'abolishDate':   item.get('abolishDate') or None,
        'replaces':      item.get('replaces') or None,
        'replacedBy':    item.get('replacedBy') or None,
        'issuedBy':      item.get('issuedBy',''),
        'category':      item.get('category') or guess_category(title),
        'tags':          item.get('tags') or guess_tags(title),
        'summary':       item.get('summary') or '',
        'isMandatory':   item.get('isMandatory', is_mandatory(code)),
        'scope':         item.get('scope') or '',
        'localFile':     f"downloads/{make_id(code)}.pdf",
    }

def safe_json(resp, source=''):
    """安全解析 JSON，返回解析结果或 None"""
    ct = resp.headers.get('content-type','')
    if 'html' in ct.lower():
        if DEBUG_MODE:
            log(f"    [DEBUG] {source} 返回 HTML 而非 JSON，跳过")
        return None
    try:
        return resp.json()
    except Exception as e:
        if DEBUG_MODE:
            log(f"    [DEBUG] {source} JSON 解析失败: {e}")
            log(f"    [DEBUG] 返回内容: {resp.text[:300]}")
        return None

def clean_sacinfo(raw):
    """清洗字段中的 <sacinfo> XML 标签，保留纯文字"""
    if not raw:
        return ''
    return re.sub(r'</?sacinfo>', '', raw).strip()

def clean_samr_code(raw):
    """清洗 samr 返回的标准编号（含 <sacinfo> XML 标签）
    例：<sacinfo>GB</sacinfo><sacinfo>T</sacinfo> <sacinfo>14833-2011</sacinfo>
    → GB/T 14833-2011
    """
    if not raw:
        return ''
    parts = re.findall(r'<sacinfo>(.*?)</sacinfo>', raw)
    if parts:
        prefix = ''.join(parts[:-1]).strip()
        number = parts[-1].strip()
        slash_map = {'GBT':'GB/T','GBZ':'GB/Z','JGT':'JG/T','GAT':'GA/T','JGJ':'JGJ','CJJ':'CJJ'}
        prefix = slash_map.get(prefix, prefix)
        return f"{prefix} {number}".strip() if prefix else number
    return re.sub(r'<[^>]+>', '', raw).strip()

def parse_samr_row(row):
    """解析 samr 行数据，兼容新旧字段名"""
    code  = clean_samr_code(
        row.get('C_STD_CODE') or row.get('STD_CODE') or row.get('stdCode') or ''
    ).strip()
    title = clean_sacinfo(row.get('C_C_NAME') or row.get('STD_NAME') or row.get('stdName') or '').strip()
    status_raw   = row.get('STATE') or row.get('STD_STATUS') or row.get('status') or ''
    issue_date   = norm_date(row.get('ISSUE_DATE') or row.get('issueDate'))
    impl_date    = norm_date(row.get('ACT_DATE') or row.get('IMPL_DATE') or row.get('implDate'))
    abol_date    = norm_date(row.get('ABOL_DATE') or row.get('abolDate'))
    issued_by    = (row.get('ISSUE_DEPT') or row.get('issueDept') or
                   row.get('C_ISSUE_DEPT') or '').strip()
    nature       = row.get('STD_NATURE') or ''
    mandatory    = is_mandatory(code) or '强制' in nature
    return code, title, status_raw, issue_date, impl_date, abol_date, issued_by, mandatory

# ============================================================
#  来源一：std.samr.gov.cn（兼容新旧字段名）
# ============================================================
def fetch_samr(keyword):
    results = []

    # 接口1：gbQueryPage（主接口）
    try:
        resp = SESSION.post(
            "https://std.samr.gov.cn/gb/search/gbQueryPage",
            json={"searchText": keyword, "status": "",
                  "sortField": "ISSUE_DATE", "sortType": "desc",
                  "pageSize": 50, "pageIndex": 1},
            headers={'Referer':'https://std.samr.gov.cn/',
                     'Content-Type':'application/json',
                     'Accept':'application/json, text/plain, */*',
                     'Origin':'https://std.samr.gov.cn'},
            timeout=20
        )
        dbg("samr-gbQueryPage", resp)
        if resp.ok:
            data = safe_json(resp, 'samr-gbQueryPage')
            if data:
                rows = data.get('rows') or data.get('data',{}).get('rows',[]) or []
                if DEBUG_MODE:
                    log(f"    [DEBUG] samr rows: {len(rows)}, keys: {list(data.keys())[:6]}")
                for row in rows:
                    code, title, status_raw, issue_date, impl_date, abol_date, issued_by, mandatory = parse_samr_row(row)
                    if not code or not title or not is_sports(title): continue
                    results.append({
                        'code': code, 'title': title,
                        'status': norm_status(status_raw),
                        'issueDate': issue_date, 'implementDate': impl_date,
                        'abolishDate': abol_date, 'issuedBy': issued_by,
                        'isMandatory': mandatory,
                    })
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] samr接口1异常: {e}")

    if results: return results

    # 接口2：openstd
    try:
        resp = SESSION.get(
            "https://openstd.samr.gov.cn/bzgk/gb/gbQuery",
            params={"searchText": keyword, "pageIndex": 1, "pageSize": 50},
            headers={'Referer':'https://openstd.samr.gov.cn/',
                     'Accept':'application/json, text/plain, */*'},
            timeout=20
        )
        dbg("openstd-gbQuery", resp)
        if resp.ok:
            data = safe_json(resp, 'openstd')
            if data:
                rows = data.get('rows') or []
                if DEBUG_MODE: log(f"    [DEBUG] openstd rows: {len(rows)}")
                for row in rows:
                    code, title, status_raw, issue_date, impl_date, abol_date, issued_by, mandatory = parse_samr_row(row)
                    if not code or not title or not is_sports(title): continue
                    results.append({
                        'code': code, 'title': title,
                        'status': norm_status(status_raw),
                        'issueDate': issue_date, 'implementDate': impl_date,
                        'abolishDate': abol_date, 'issuedBy': issued_by,
                        'isMandatory': mandatory,
                    })
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] openstd异常: {e}")

    if results: return results

    # 接口3：GET 备用
    try:
        resp = SESSION.get(
            "https://std.samr.gov.cn/gb/search/gbQueryPage",
            params={"searchText": keyword, "pageSize": 30, "pageIndex": 1},
            headers={'Referer':'https://std.samr.gov.cn/'},
            timeout=15
        )
        dbg("samr-GET", resp)
        if resp.ok:
            data = safe_json(resp, 'samr-GET')
            if data:
                rows = data.get('rows') or []
                if DEBUG_MODE: log(f"    [DEBUG] samr-GET rows: {len(rows)}")
                for row in rows:
                    code, title, status_raw, issue_date, impl_date, abol_date, issued_by, mandatory = parse_samr_row(row)
                    if not code or not title or not is_sports(title): continue
                    results.append({
                        'code': code, 'title': title,
                        'status': norm_status(status_raw),
                        'issueDate': issue_date, 'implementDate': impl_date,
                        'issuedBy': issued_by, 'isMandatory': mandatory,
                    })
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] samr接口3异常: {e}")

    return results

# ============================================================
#  来源二：ttbz.org.cn 团标平台
# ============================================================
def fetch_ttbz(keyword):
    results = []
    # 接口1
    try:
        resp = SESSION.post(
            "https://www.ttbz.org.cn/api/search/standard",
            json={"keyword": keyword, "pageIndex": 1, "pageSize": 30},
            headers={'Referer':'https://www.ttbz.org.cn/',
                     'Content-Type':'application/json',
                     'Accept':'application/json, text/plain, */*',
                     'Origin':'https://www.ttbz.org.cn'},
            timeout=20
        )
        dbg("ttbz-api", resp)
        if resp.ok:
            data = safe_json(resp, 'ttbz')
            if data:
                rows = data.get('Data') or data.get('data') or data.get('rows') or []
                if DEBUG_MODE: log(f"    [DEBUG] ttbz rows: {len(rows)}, keys: {list(data.keys())[:5]}")
                for row in rows:
                    code  = (row.get('StdCode') or row.get('stdCode') or '').strip()
                    title = (row.get('StdName') or row.get('stdName') or '').strip()
                    if code and title and is_sports(title):
                        results.append({
                            'code':          code,
                            'title':         title,
                            'type':          '团标',
                            'status':        norm_status(row.get('Status') or '现行'),
                            'issueDate':     norm_date(row.get('IssueDate')),
                            'implementDate': norm_date(row.get('ImplementDate')),
                            'issuedBy':      (row.get('OrgName') or '').strip(),
                            'isMandatory':   False,
                        })
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] ttbz异常: {e}")

    if results: return results

    # 接口2：搜索页面
    try:
        resp = SESSION.get(
            "https://www.ttbz.org.cn/Home/Standard",
            params={"searchText": keyword, "page": 1, "rows": 20},
            headers={'Referer':'https://www.ttbz.org.cn/'},
            timeout=20
        )
        dbg("ttbz-home", resp)
        if resp.ok:
            data = safe_json(resp, 'ttbz-home')
            if data:
                rows = data.get('rows') or []
                for row in rows:
                    code  = (row.get('StdCode') or '').strip()
                    title = (row.get('StdName') or '').strip()
                    if code and title and is_sports(title):
                        results.append({
                            'code': code, 'title': title, 'type': '团标',
                            'status': '现行', 'isMandatory': False,
                            'issuedBy': (row.get('OrgName') or '').strip(),
                        })
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] ttbz-home异常: {e}")

    return results

# ============================================================
#  来源三：dbba.sacinfo.org.cn 地方标准
# ============================================================
def fetch_dbba(keyword):
    results = []
    # 接口1
    for url, params in [
        ("https://dbba.sacinfo.org.cn/api/standard/list",
         {"searchText": keyword, "pageSize": 30, "pageNum": 1}),
        ("https://dbba.sacinfo.org.cn/stddb/queryStdByCondition",
         {"keyword": keyword, "pageSize": 20, "pageNo": 1}),
    ]:
        try:
            resp = SESSION.get(url, params=params,
                               headers={'Referer':'https://dbba.sacinfo.org.cn/',
                                        'Accept':'application/json'},
                               timeout=20)
            dbg(f"dbba-{url[-20:]}", resp)
            if not resp.ok: continue
            data = safe_json(resp, 'dbba')
            if not data: continue
            items = ((data.get('data') or {}).get('list') or
                     data.get('rows') or data.get('result') or
                     data.get('data') or [])
            if isinstance(items, dict): items = items.get('list') or []
            if DEBUG_MODE: log(f"    [DEBUG] dbba items: {len(items)}, data keys: {list(data.keys())[:5]}")
            for item in items:
                code  = (item.get('stdCode') or item.get('StdCode') or '').strip()
                title = (item.get('stdName') or item.get('StdName') or '').strip()
                if code and title and is_sports(title):
                    results.append({
                        'code':          code,
                        'title':         title,
                        'type':          '地方标准',
                        'status':        norm_status(item.get('status') or item.get('Status') or ''),
                        'issueDate':     norm_date(item.get('publishDate') or item.get('IssueDate')),
                        'implementDate': norm_date(item.get('implementDate')),
                        'issuedBy':      (item.get('publishDept') or item.get('IssueDept') or '').strip(),
                        'isMandatory':   False,
                    })
            if results: break
        except Exception as e:
            if DEBUG_MODE: log(f"    [DEBUG] dbba异常: {e}")
    return results

# ============================================================
#  来源扩展：行业标准专用抓取（samr 接口加类型过滤）
# ============================================================
def fetch_samr_by_type(keyword, std_type=''):
    """在 samr 接口中按标准类型过滤抓取
    std_type: '' 全部 | 'HB' 行业标准 | 'DB' 地方标准
    """
    results = []
    try:
        payload = {
            "searchText": keyword,
            "status": "",
            "sortField": "ISSUE_DATE",
            "sortType": "desc",
            "pageSize": 50,
            "pageIndex": 1,
        }
        if std_type:
            payload["stdType"] = std_type
        resp = SESSION.post(
            "https://std.samr.gov.cn/gb/search/gbQueryPage",
            json=payload,
            headers={'Referer':'https://std.samr.gov.cn/',
                     'Content-Type':'application/json',
                     'Accept':'application/json, text/plain, */*',
                     'Origin':'https://std.samr.gov.cn'},
            timeout=20
        )
        if resp.ok:
            data = safe_json(resp, f'samr-type-{std_type}')
            if data:
                rows = data.get('rows') or data.get('data',{}).get('rows',[]) or []
                for row in rows:
                    code, title, status_raw, issue_date, impl_date, abol_date, issued_by, mandatory = parse_samr_row(row)
                    if not code or not title or not is_sports(title): continue
                    results.append({
                        'code': code, 'title': title,
                        'status': norm_status(status_raw),
                        'issueDate': issue_date, 'implementDate': impl_date,
                        'abolishDate': abol_date, 'issuedBy': issued_by,
                        'isMandatory': mandatory,
                    })
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] samr-type异常: {e}")
    return results

# ============================================================
#  来源扩展：住建部行业标准（JGJ/CJJ/JG）
# ============================================================
def fetch_mohurd(keyword):
    """住建部标准规范信息网（行业标准主要来源）"""
    results = []
    for url in [
        "https://www.mohurd.gov.cn/gongkai/bzde/bzgflb/index.html",
    ]:
        try:
            resp = SESSION.get(url, params={"keyword": keyword}, timeout=15,
                               headers={'Referer':'https://www.mohurd.gov.cn/'})
            if resp.ok:
                # 从页面提取标准编号
                r = extract_codes(resp.text, keyword)
                if r:
                    log(f"    住建部发现 {len(r)} 个编号")
                    results.extend(r)
        except Exception:
            pass
    return results

# ============================================================
#  来源扩展：地方标准完整抓取（按省份分区域）
# ============================================================
def fetch_local_standards(keyword):
    """地方标准专项抓取（dbba + 各省市标准数据库）"""
    results = fetch_dbba(keyword)  # 已有的 dbba 函数
    # 补充：通过 samr 地方标准入口
    local = fetch_samr_by_type(keyword, 'DB')
    results.extend(local)
    if DEBUG_MODE and (results):
        log(f"    地方标准合计: {len(results)} 条")
    return results

# ============================================================
#  来源扩展：团体标准完整抓取
# ============================================================
def fetch_group_standards(keyword):
    """团体标准专项抓取（ttbz 全国团标平台）"""
    results = fetch_ttbz(keyword)  # 已有的 ttbz 函数
    if DEBUG_MODE and results:
        log(f"    团体标准: {len(results)} 条")
    return results
# ============================================================
def extract_codes(html, keyword):
    codes = STD_CODE_RE.findall(html)
    results = []
    for code in set(codes):
        code = re.sub(r'\s+', ' ', code).strip()
        results.append({'code': code, 'title': f'{keyword}相关标准',
                        'status': '现行', 'isMandatory': is_mandatory(code)})
    return results

def fetch_baidu(keyword):
    try:
        resp = SESSION.get('https://www.baidu.com/s',
                           params={'wd': f'{keyword} 标准 GB JG', 'rn': '20'},
                           headers={'Referer':'https://www.baidu.com/',
                                    'Accept':'text/html'}, timeout=15)
        if resp.ok:
            resp.encoding = resp.apparent_encoding or 'utf-8'
            r = extract_codes(resp.text, keyword)
            if r: log(f"    百度发现 {len(r)} 个编号")
            return r
    except Exception as e:
        if DEBUG_MODE: log(f"    百度: {e}")
    return []

def fetch_sogou(keyword):
    try:
        resp = SESSION.get('https://www.sogou.com/web',
                           params={'query': f'{keyword} 国家标准 GB JG'},
                           headers={'Referer':'https://www.sogou.com/'}, timeout=15)
        if resp.ok:
            resp.encoding = 'utf-8'
            r = extract_codes(resp.text, keyword)
            if r: log(f"    搜狗发现 {len(r)} 个编号")
            return r
    except Exception as e:
        if DEBUG_MODE: log(f"    搜狗: {e}")
    return []

def fetch_so360(keyword):
    try:
        resp = SESSION.get('https://www.so.com/s',
                           params={'q': f'{keyword} 体育标准 GB'},
                           headers={'Referer':'https://www.so.com/'}, timeout=15)
        if resp.ok:
            r = extract_codes(resp.text, keyword)
            if r: log(f"    360发现 {len(r)} 个编号")
            return r
    except Exception as e:
        if DEBUG_MODE: log(f"    360: {e}")
    return []

# ============================================================
#  AI摘要补全
# ============================================================
def ai_enrich_standard(std):
    provider = None
    if QWEN_KEY:     provider = 'qwen'
    if DEEPSEEK_KEY: provider = 'deepseek'
    if not provider: return None

    prompt = (f"你是中国标准化专家。根据以下信息，用2-3句话准确描述该标准的主要内容和适用范围，"
              f"只返回描述文字。\n"
              f"编号：{std.get('code','')}\n名称：{std.get('title','')}\n"
              f"机构：{std.get('issuedBy','')}\n日期：{std.get('issueDate','')}")
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
        log(f"    AI失败: {e}")
    return None

def ai_enrich_batch(standards, max_count=100):
    provider = 'DeepSeek' if DEEPSEEK_KEY else ('通义千问/百炼' if QWEN_KEY else None)
    if not provider:
        log("  ⚠️  未配置AI Key，跳过摘要补全")
        return standards
    log(f"\n🤖 AI摘要补全（{provider}，最多{max_count}条）…")
    enriched = 0
    for i, std in enumerate(standards):
        if enriched >= max_count: break
        if std.get('summary','').strip(): continue
        s = ai_enrich_standard(std)
        if s and len(s) > 10:
            standards[i]['summary'] = s; enriched += 1
            log(f"  ✅ [{std['code']}] {s[:40]}…")
        time.sleep(0.5)
    log(f"  AI补全：{enriched} 条")
    return standards

# ============================================================
#  核查状态
# ============================================================
def check_status_online(std):
    code = std.get('code','')
    if not code: return None
    try:
        resp = SESSION.post(
            "https://std.samr.gov.cn/gb/search/gbQueryPage",
            json={"searchText": code, "pageSize": 5, "pageIndex": 1},
            timeout=12)
        if not resp.ok: return None
        data = safe_json(resp, 'check')
        if not data: return None
        for row in (data.get('rows') or []):
            rc = (row.get('STD_CODE') or '').strip()
            if rc and norm_code(rc) == norm_code(code):
                ns = norm_status(row.get('STD_STATUS',''))
                if ns and ns != std.get('status'):
                    upd = dict(std)
                    upd['status'] = ns
                    if ns == '废止':
                        upd['abolishDate'] = (norm_date(row.get('ABOL_DATE'))
                                              or datetime.now().strftime('%Y-%m-%d'))
                    return upd
    except Exception: pass
    return None

# ============================================================
#  合并
# ============================================================
def merge(existing, new_items):
    idx = {norm_code(s['code']): i for i, s in enumerate(existing)}
    added = updated_n = 0
    for item in new_items:
        cn = norm_code(item.get('code',''))
        if not cn: continue
        if cn in idx:
            orig, changed = existing[idx[cn]], False
            for f in ('status','abolishDate','implementDate','issueDate','issuedBy'):
                nv = item.get(f)
                if nv and nv != orig.get(f):
                    orig[f] = nv; changed = True
            if changed: updated_n += 1
        else:
            existing.append(build_entry(item))
            idx[cn] = len(existing)-1
            added += 1
    return existing, added, updated_n

def load_db():
    if not DATA_FILE.exists():
        log("⚠️  data/standards.json 不存在，从空白开始")
        return {'standards': []}, []
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            db = json.load(f)
        standards = db.get('standards') or []
        log(f"📦 现有标准数: {len(standards)} 条")
        return db, standards
    except Exception as e:
        log(f"⚠️  文件损坏({e})，从空白开始")
        return {'standards': []}, []

def save_db(db, standards, dry_run):
    today = datetime.now().strftime('%Y-%m-%d')
    db.update({'standards': standards, 'updated': today,
               'version': today.replace('-','.'), 'total': len(standards)})
    if dry_run:
        log(f"\n🔵 [预览] {len(standards)} 条，不写入"); return
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    log(f"\n✅ 写入完成：{len(standards)} 条  版本 {today}")

# ============================================================
#  主流程
# ============================================================
def run(dry_run=False, check_only=False, use_ai=False):
    global DEBUG_MODE
    log("="*60)
    log(f"体育标准数据库 — 自动抓取更新 v7")
    log(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"调试模式: {'开启' if DEBUG_MODE else '关闭（加 --debug 参数查看原始返回）'}")
    log(f"AI摘要: {'DeepSeek' if DEEPSEEK_KEY else '通义千问/百炼' if QWEN_KEY else '未配置'}")
    log("="*60)

    db, standards = load_db()

    # Step 1：核查状态
    if standards and not DEBUG_MODE:
        log(f"\n🔍 Step 1：核查现有 {len(standards)} 条标准状态…")
        changed = 0
        for i, std in enumerate(standards):
            upd = check_status_online(std)
            if upd:
                j = next((k for k,s in enumerate(standards) if s['code']==std['code']), None)
                if j is not None:
                    standards[j] = upd; changed += 1
                    log(f"  🔄 {std['code']}: {std.get('status')} → {upd['status']}")
            time.sleep(0.3)
        log(f"  状态变更 {changed} 条")
    else:
        log("\n📋 直接开始抓取")

    if check_only:
        save_db(db, standards, dry_run); return

    # Step 2：国家标准抓取
    log(f"\n🌐 Step 2-A：国家标准（{len(KEYWORDS)} 个关键词）…")
    all_new, official_ok = [], False
    for i, kw in enumerate(KEYWORDS, 1):
        log(f"  [{i:02d}/{len(KEYWORDS)}] {kw}")
        a = fetch_samr(kw)
        time.sleep(0.8)
        got = len(a)
        if got:
            all_new.extend(a); official_ok = True
            log(f"         ✅ +{got}")

    # Step 2-B：行业标准专项
    log(f"\n🏗️  Step 2-B：行业标准（{len(INDUSTRY_KEYWORDS)} 个关键词）…")
    for i, kw in enumerate(INDUSTRY_KEYWORDS, 1):
        log(f"  [{i:02d}/{len(INDUSTRY_KEYWORDS)}] {kw}")
        a = fetch_samr_by_type(kw, 'HB')   # 行业标准类型
        b = fetch_samr(kw)                  # 普通搜索补充
        got = len(a) + len(b)
        if got:
            all_new.extend(a+b); official_ok = True
            log(f"         ✅ +{got}")
        time.sleep(0.8)

    # Step 2-C：地方标准专项
    log(f"\n🗺️  Step 2-C：地方标准（{len(LOCAL_KEYWORDS)} 个关键词）…")
    for i, kw in enumerate(LOCAL_KEYWORDS, 1):
        log(f"  [{i:02d}/{len(LOCAL_KEYWORDS)}] {kw}")
        a = fetch_local_standards(kw)
        time.sleep(0.8)
        if a:
            all_new.extend(a); official_ok = True
            log(f"         ✅ +{len(a)}")

    # Step 2-D：团体标准专项
    log(f"\n🏅 Step 2-D：团体标准（{len(GROUP_KEYWORDS)} 个关键词）…")
    for i, kw in enumerate(GROUP_KEYWORDS, 1):
        log(f"  [{i:02d}/{len(GROUP_KEYWORDS)}] {kw}")
        a = fetch_group_standards(kw)
        time.sleep(0.6)
        if a:
            all_new.extend(a); official_ok = True
            log(f"         ✅ +{len(a)}")

    # Step 3：搜索引擎辅助
    log(f"\n🔎 Step 3：搜索引擎辅助发现…")
    found_codes, search_new = set(), []
    for kw in KEYWORDS[:10]:
        for fn in [fetch_baidu, fetch_sogou, fetch_so360]:
            for item in fn(kw):
                cn = norm_code(item['code'])
                if cn not in found_codes:
                    found_codes.add(cn); search_new.append(item)
            time.sleep(0.6)

    if search_new:
        log(f"  共发现 {len(search_new)} 个编号，核实中…")
        for item in search_new[:40]:
            detail = fetch_samr(item['code'])
            if detail:
                all_new.extend(detail)
                log(f"    ✅ {item['code']}")
            time.sleep(0.5)

    # Step 4：合并
    if all_new:
        log(f"\n🔀 Step 4：合并（{len(all_new)} 条原始数据）…")
        before = len(standards)
        standards, added, updated_n = merge(standards, all_new)
        log(f"  新增 {added} | 更新 {updated_n} | 总量 {len(standards)}")
    else:
        log(f"\n  ⚠️  本次抓取结果为空")
        log(f"  建议：运行 python scripts/update_standards.py --debug")
        log(f"  查看原始API返回内容，确认接口是否可访问")

    # Step 5：AI补全
    if use_ai:
        standards = ai_enrich_batch(standards)

    save_db(db, standards, dry_run)

    total  = len(standards)
    active = sum(1 for s in standards if s.get('status')=='现行')
    abol   = sum(1 for s in standards if s.get('status')=='废止')
    coming = sum(1 for s in standards if s.get('status')=='即将实施')
    log(f"\n📊 总 {total} | 现行 {active} | 废止 {abol} | 即将实施 {coming}")

if __name__ == '__main__':
    p = argparse.ArgumentParser(description='体育标准自动抓取更新工具 v7')
    p.add_argument('--dry',   action='store_true', help='预览，不写入文件')
    p.add_argument('--check', action='store_true', help='仅核查状态')
    p.add_argument('--ai',    action='store_true', help='启用AI补全摘要')
    p.add_argument('--debug', action='store_true', help='调试模式，打印原始API返回')
    args = p.parse_args()
    DEBUG_MODE = args.debug
    run(dry_run=args.dry, check_only=args.check, use_ai=args.ai)
