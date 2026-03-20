#!/usr/bin/env python3
"""
体育建设标准自动抓取 & 更新脚本 v8
======================================
v8 修复：
  - 修复 samr API searchText 参数无效问题（改用正确的请求格式）
  - 重新验证每个关键词的搜索结果总数
  - 精确过滤：只保留体育建设行业标准
  - 启动时自动清理库中非体育标准

运行方式：
  python scripts/update_standards.py         # 完整抓取
  python scripts/update_standards.py --check # 仅核查状态
  python scripts/update_standards.py --ai    # 启用AI补全摘要
  python scripts/update_standards.py --debug # 调试模式
  python scripts/update_standards.py --dry   # 预览不写入
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
#  samr API 的 ISSUE_DEPT 字段常为空，根据编号前缀+年份精准推断
# ============================================================
ISSUED_BY_RULES = {
    # 国家体育总局主导的体育专项国标
    'sport_gb': {
        'pattern': r'^GB[\s/]T\s*(22517|36536|36527|37546|34284|38517|34290|40115|32085|28231|3976|36246|14833|19272)',
        'by_year': {2018: '国家市场监督管理总局', 2001: '国家质量监督检验检疫总局', 0: '国家技术监督局'}
    },
}

def infer_issued_by(code, issue_date):
    """根据编号前缀+发布年份推断发布机构，API返回为空时使用"""
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

    # 国家标准 GB / GB/T / GB/Z
    if re.match(r'^GB', cu):
        if year >= 2018: return '国家市场监督管理总局'
        if year >= 2001: return '国家质量监督检验检疫总局'
        if year >= 1993: return '国家技术监督局'
        return '国家标准化管理委员会'

    # 建工行业标准 JGJ / JG/T / CJJ / CJJ/T
    if re.match(r'^(JGJ|JGJT|JGT|CJJ|CJJT)', cu):
        if year >= 2008: return '住房和城乡建设部'
        return '建设部'

    # 团体标准
    if cu.startswith('T/SGTAS'): return '中国运动场地联合会'
    if cu.startswith('T/CECS'):  return '中国工程建设标准化协会'
    if cu.startswith('T/CSUS'):  return '中国城市科学研究会'
    if cu.startswith('T/CAECS'): return '中国建设教育协会'
    if cu.startswith('T/CSTM'):  return '中关村材料试验技术联盟'
    if cu.startswith('T/'):      return ''  # 其他团标机构各异，不猜

    # 地方标准：各省机构各异，不推断
    if cu.startswith('DB'): return ''

    return ''

# ============================================================
#  自动补全规则二：版本替代关系自动发现
#  策略：同一基础编号的不同年份，自动建立新旧替代链
#  例：GB/T 14833-2011 → 被 GB/T 14833-2020 替代
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
#  在精确查询阶段，访问详情页补全 replaces/replacedBy
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
                }, timeout=12)
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
    "人造草坪", "人造草皮", "运动场人造草", "人工草坪",“人造草”
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
    # 围网
    "体育围网", "运动场围网", "球场围网",
    # 健身器材
    "室外健身器材", "健身路径", "公共健身器材",
    # 体育器材
    "体育器材", "学校体育器材",
    # 游泳场地
    "游泳场地", "游泳馆", "游泳池水质",
    # 球类场地
    "足球场地", "篮球场地", "网球场地", "田径场地",
    "排球场地", "羽毛球场地", "乒乓球场地",
    # 场地设计/综合
    "体育场地", "运动场地", "体育场馆建设",
    "体育建筑设计", "体育公园", "全民健身设施",
    "学校操场", "体育设施建设",
“体育","足球","篮球","网球","排球","乒乓球","羽毛球","手球","棒球","冰球","围网",
]

# ============================================================
#  体育标准精确过滤词组
# ============================================================
SPORTS_TERMS = [
    "合成材料面层","合成材料跑道","塑胶跑道","聚氨酯跑道","橡胶面层",
    "人造草坪","人造草皮","人工草坪","运动场人造草",
    "颗粒填充料","草坪填充",
    "体育场馆照明","体育照明","运动场照明","体育场地照明","体育建筑电气",
    "体育木地板","运动木地板","体育用木质地板","体育馆木地板","体育馆用木",
    "运动地胶","PVC运动地板","体育地板","运动地板","弹性运动地板",
    "卷材运动地板","聚氯乙烯运动",
    "体育围网","运动场围网","球场围网","体育场围网",
    "室外健身器材","健身路径","公共健身器材","户外健身器材",
    "体育器材","学校体育器材","篮球架","足球门","排球架","乒乓球台",
    "体育场地","运动场地","体育场馆","体育建筑",
    "足球场地","篮球场地","网球场地","田径场地",
    "游泳场地","游泳馆","游泳池",
    "排球场地","羽毛球场地","乒乓球场地",
    "手球场","棒球场","冰球场",
    "学校操场","体育公园","全民健身","体育设施",
    "体育用品","体育场",
“体育","足球","篮球","网球","排球","乒乓球","羽毛球","手球","棒球","冰球","围网",
]

def is_sports(title):
    if not title: return False
    return any(term in title for term in SPORTS_TERMS)

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36'

def make_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=[429,500,502,503,504])
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

def make_id(code):
    return re.sub(r'[^A-Za-z0-9]', '', code)[:30] or hashlib.md5(code.encode()).hexdigest()[:12]

def norm_code(c):
    return re.sub(r'\s+', '', c).upper()

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
    }
    for kw, cat in cm.items():
        if kw in text: return cat
    return "综合"

def guess_tags(text):
    return [t for t in ["体育","运动","塑胶","合成材料","人造草","照明",
                         "木地板","围网","健身","颗粒","游泳","篮球","足球",
                         "网球","田径","排球","羽毛球","跑道","场地","学校"] if t in text][:6]

def build_entry(item):
    code  = item.get('code','')
    title = clean_sacinfo(item.get('title',''))

    # ── 自动补全：发布机构 ──────────────────────────────────
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
        'replaces':      item.get('replaces') or None,      # 抓取时已尽量填充
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
#  来源一：std.samr.gov.cn（关键词精确搜索）
# ============================================================
def fetch_samr(keyword, page=1):
    """
    v8修复：使用正确的请求格式，确保关键词有效过滤
    """
    results = []
    total_pages = 1

    # 方式一：POST JSON（主要方式）
    try:
        resp = SESSION.post(
            "https://std.samr.gov.cn/gb/search/gbQueryPage",
            json={
                "searchText": keyword,
                "status":     "",
                "sortField":  "ISSUE_DATE",
                "sortType":   "desc",
                "pageSize":   50,
                "pageIndex":  page,
            },
            headers={
                'Referer':       'https://std.samr.gov.cn/',
                'Origin':        'https://std.samr.gov.cn',
                'Content-Type':  'application/json',
                'Accept':        'application/json, text/plain, */*',
                'X-Requested-With': 'XMLHttpRequest',
            },
            timeout=25
        )
        if resp.ok:
            ct = resp.headers.get('content-type','')
            if 'html' in ct.lower():
                if DEBUG_MODE: log(f"    [DEBUG] samr返回HTML，跳过")
            else:
                try:
                    data = resp.json()
                    rows = data.get('rows') or []
                    total = int(data.get('total') or 0)
                    if total > 0:
                        total_pages = max(1, -(-total // 50))
                    if DEBUG_MODE:
                        log(f"    [DEBUG] samr p{page}: rows={len(rows)} total={total}")
                    for row in rows:
                        code  = clean_samr_code(
                            row.get('C_STD_CODE') or row.get('STD_CODE') or row.get('stdCode') or ''
                        ).strip()
                        title = clean_sacinfo(
                            row.get('C_C_NAME') or row.get('STD_NAME') or row.get('stdName') or ''
                        ).strip()
                        if not code or not title: continue
                        if not is_sports(title): continue

                        # ── 发布机构：API返回优先，为空则推断 ──
                        issued_by = (row.get('ISSUE_DEPT') or row.get('C_ISSUE_DEPT') or '').strip()
                        issue_date = norm_date(row.get('ISSUE_DATE') or row.get('issueDate'))
                        if not issued_by:
                            issued_by = infer_issued_by(code, issue_date)

                        # ── 替代关系：从API字段读取 ──
                        replaces_val = clean_sacinfo(
                            row.get('C_SUPERSEDE_CODE') or row.get('SUPERSEDE_CODE') or
                            row.get('replaceCode') or ''
                        ).strip() or None
                        replaced_by_val = clean_sacinfo(
                            row.get('C_REPLACED_CODE') or row.get('REPLACED_CODE') or
                            row.get('replacedCode') or ''
                        ).strip() or None

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
                    if DEBUG_MODE: log(f"    [DEBUG] samr JSON解析异常: {e}")
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] samr请求异常: {e}")

    return results, total_pages

def fetch_samr_all(keyword):
    """抓取关键词的全部分页"""
    all_results = []
    seen = set()

    results, total_pages = fetch_samr(keyword, 1)
    for r in results:
        if r['code'] not in seen:
            seen.add(r['code'])
            all_results.append(r)

    if total_pages > 1:
        log(f"         总页数:{total_pages}，继续抓取…")
    for page in range(2, min(total_pages + 1, 11)):
        time.sleep(0.6)
        results, _ = fetch_samr(keyword, page)
        if not results: break
        for r in results:
            if r['code'] not in seen:
                seen.add(r['code'])
                all_results.append(r)

    return all_results

# ============================================================
#  来源二：ttbz.org.cn 团标平台
# ============================================================
def fetch_ttbz(keyword):
    results = []
    try:
        resp = SESSION.post(
            "https://www.ttbz.org.cn/api/search/standard",
            json={"keyword": keyword, "pageIndex": 1, "pageSize": 30},
            headers={
                'Referer':      'https://www.ttbz.org.cn/',
                'Origin':       'https://www.ttbz.org.cn',
                'Content-Type': 'application/json',
            },
            timeout=20
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
                    if not is_sports(title): continue
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
    return results

# ============================================================
#  来源三：dbba.sacinfo.org.cn 地方标准
# ============================================================
def fetch_dbba(keyword):
    results = []
    try:
        resp = SESSION.get(
            'https://dbba.sacinfo.org.cn/api/standard/list',
            params={"searchText": keyword, "pageSize": 30, "pageNum": 1},
            headers={'Referer':'https://dbba.sacinfo.org.cn/'},
            timeout=20
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
                    if not is_sports(title): continue
                    results.append({
                        'code':          code,
                        'title':         title,
                        'type':          '地方标准',
                        'status':        norm_status(item.get('status') or ''),
                        'issueDate':     norm_date(item.get('publishDate')),
                        'implementDate': norm_date(item.get('implementDate')),
                        'issuedBy':      (item.get('publishDept') or '').strip(),
                        'isMandatory':   False,
                    })
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] dbba异常: {e}")
    return results

# ============================================================
#  AI摘要补全
# ============================================================
def ai_enrich_standard(std):
    provider = 'qwen' if QWEN_KEY else ('deepseek' if DEEPSEEK_KEY else None)
    if not provider: return None
    prompt = (f"你是中国标准化专家。用2-3句话描述该标准主要内容和适用范围，只返回描述。\n"
              f"编号：{std.get('code','')}  名称：{std.get('title','')}")
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
        if DEBUG_MODE: log(f"    AI失败: {e}")
    return None

def ai_enrich_batch(standards, force=False):
    provider = 'DeepSeek' if DEEPSEEK_KEY else ('通义千问/百炼' if QWEN_KEY else None)
    if not provider:
        log("  ⚠️  未配置AI Key，跳过摘要补全")
        return standards
    log(f"🤖 AI摘要补全（{provider}，{'强制全部' if force else '仅补缺'}）…")
    enriched = 0
    for i, std in enumerate(standards):
        # force=True 时重新生成所有摘要；否则只补空白的
        if not force and std.get('summary','').strip(): continue
        s = ai_enrich_standard(std)
        if s and len(s) > 10:
            standards[i]['summary'] = s; enriched += 1
            log(f"  ✅ [{std['code']}] {s[:40]}…")
        time.sleep(0.5)
    log(f"  完成：补全/更新 {enriched} 条摘要")
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
    db.update({'standards':standards,'updated':today,
               'version':today.replace('-','.'),'total':len(standards)})
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
    log(f"体育标准数据库 — 自动抓取更新 v8")
    log(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"AI摘要: {'DeepSeek' if DEEPSEEK_KEY else '通义千问/百炼' if QWEN_KEY else '未配置'}")
    log("="*60)

    db, standards = load_db()

    # ── 自动清理现有库中非体育标准 ──────────────────────────
    before = len(standards)
    standards = [s for s in standards if is_sports(clean_sacinfo(s.get('title','')))]
    removed = before - len(standards)
    if removed > 0:
        log(f"\n🗑️  自动清理非体育标准：移除 {removed} 条，剩余 {len(standards)} 条")

    # ── 清洗 sacinfo 标签 ────────────────────────────────────
    for i, std in enumerate(standards):
        if std.get('title') and '<sacinfo>' in std['title']:
            standards[i]['title'] = clean_sacinfo(std['title'])

    if check_only:
        log(f"\n🔍 核查现有 {len(standards)} 条标准状态…")
        changed = 0
        for i, std in enumerate(standards):
            upd = check_status_online(std)
            if upd:
                j = next((k for k,s in enumerate(standards) if s['code']==std['code']), None)
                if j is not None:
                    standards[j] = upd; changed += 1
                    log(f"  🔄 {std['code']}: {std.get('status')} → {upd['status']}")
            time.sleep(0.4)
        log(f"  状态变更: {changed} 条")
        save_db(db, standards, dry_run); return

    # ── 多源抓取 ─────────────────────────────────────────────
    log(f"\n🌐 开始抓取（{len(KEYWORDS)} 个关键词 × 3个来源）…")
    all_new = []
    total_kw = len(KEYWORDS)

    for i, kw in enumerate(KEYWORDS, 1):
        log(f"  [{i:02d}/{total_kw}] 「{kw}」")

        # samr（国标/行标，带分页）
        a = fetch_samr_all(kw)
        time.sleep(0.8)

        # 团标
        b = fetch_ttbz(kw)
        time.sleep(0.5)

        # 地方标准（每3个关键词查一次）
        c = fetch_dbba(kw) if i % 3 == 0 else []
        if c: time.sleep(0.5)

        got = len(a) + len(b) + len(c)
        if got:
            all_new.extend(a + b + c)
            log(f"         ✅ 国标/行标:{len(a)}  团标:{len(b)}  地标:{len(c)}")

    # ── 合并去重 ─────────────────────────────────────────────
    log(f"\n🔀 合并（原始 {len(all_new)} 条）…")
    before2 = len(standards)
    standards, added, updated_n = merge(standards, all_new)
    log(f"  新增 {added} | 更新 {updated_n} | 原有 {before2} | 最终 {len(standards)}")

    if added == 0 and before2 == 0:
        log("\n  ⚠️  未抓取到任何体育标准")
        log("  可能原因：API 返回的标题不包含预设词组，请运行 --debug 查看原始标题")

    # ── 自动补全：发布机构（全库扫描，补填推断值）────────────
    log("\n🔧 补全：发布机构…")
    filled_issued = 0
    for s in standards:
        if not s.get('issuedBy'):
            val = infer_issued_by(s.get('code',''), s.get('issueDate'))
            if val:
                s['issuedBy'] = val
                filled_issued += 1
    log(f"  补填发布机构 {filled_issued} 条")

    # ── 自动补全：版本替代关系（全库扫描，同编号不同年份）────
    log("\n🔧 补全：版本替代关系…")
    filled_replaces = auto_fill_replaces(standards)
    log(f"  发现版本替代关系 {filled_replaces} 条")

    # ── 自动补全：AI摘要 ──────────────────────────────────────
    # 有配置 AI Key 时自动补全（无论是否加 --ai 参数）
    # 用 --ai 参数可以强制重新生成所有摘要（包括已有摘要的条目）
    has_key = bool(QWEN_KEY or DEEPSEEK_KEY)
    if has_key or use_ai:
        if use_ai:
            log("\n🤖 AI摘要（--ai 模式：强制重新生成所有条目）…")
        else:
            # 只补全缺摘要的条目
            missing_summary = sum(1 for s in standards if not s.get('summary','').strip())
            log(f"\n🤖 AI摘要自动补全（缺摘要 {missing_summary} 条）…")
        standards = ai_enrich_batch(standards, force=use_ai)
    else:
        log("\n💡 提示：配置 QWEN_KEY 或 DEEPSEEK_KEY 后，摘要将在抓取时自动补全")
        log("   在 scripts/.env 中添加：QWEN_KEY=sk-xxxxxxxx")

    save_db(db, standards, dry_run)

    # ── 字段完整性报告 ────────────────────────────────────────
    miss_issued   = sum(1 for s in standards if not s.get('issuedBy'))
    miss_summary  = sum(1 for s in standards if not s.get('summary','').strip())
    miss_replaces = sum(1 for s in standards if
                        s.get('status') == '废止' and not s.get('replacedBy'))

    active = sum(1 for s in standards if s.get('status')=='现行')
    abol   = sum(1 for s in standards if s.get('status')=='废止')
    coming = sum(1 for s in standards if s.get('status')=='即将实施')
    log(f"\n📊 总 {len(standards)} | 现行 {active} | 废止 {abol} | 即将实施 {coming}")
    log(f"\n📋 字段完整性：")
    log(f"   缺发布机构: {miss_issued} 条  {'✅' if miss_issued==0 else '⚠️ 建议手动补全'}")
    log(f"   缺标准摘要: {miss_summary} 条  {'✅' if miss_summary==0 else ('⚠️ 配置AI Key可自动补全' if not has_key else '⚠️ 运行 --ai 可重试')}")
    log(f"   废止缺替代: {miss_replaces} 条  {'✅' if miss_replaces==0 else '⚠️ 建议手动补全'}")

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--dry',   action='store_true', help='预览，不写入')
    p.add_argument('--check', action='store_true', help='仅核查状态')
    p.add_argument('--ai',    action='store_true', help='强制重新生成所有AI摘要')
    p.add_argument('--debug', action='store_true', help='调试模式')
    args = p.parse_args()
    DEBUG_MODE = args.debug
    run(dry_run=args.dry, check_only=args.check, use_ai=args.ai)
