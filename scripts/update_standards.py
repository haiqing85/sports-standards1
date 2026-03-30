#!/usr/bin/env python3
"""
体育建设标准自动抓取 & 更新脚本 v9
======================================
v9 更新日志：
  1. 抓取页数上限：100 → 120 页
  2. 新增关键词：体育馆、人造草、木质地板
  3. 关键词过滤逻辑修复：
       - 关键词"体育"：抓取结果全部采纳（用 is_sports 宽泛过滤）
       - 其他关键词：抓取后过滤标准名称必须包含该关键词
  4. 修复行业标准缺失问题（篮球/足球/排球/手球试验方法等）：
       新增 samr 行业标准搜索接口 /hb/search/hbQueryPage
  5. 地方标准 dbba：每个关键词都抓取（原来每3个才抓），增加分页
  6. 团标 ttbz：增加分页抓取（原来只抓第1页）
  7. 新增平台：中国标准服务网 https://cssn.net.cn
  8. 新增平台：国家标准全文公开 https://openstd.samr.gov.cn
  9. 删除各 fetch 函数内的 is_sports 过滤，改在主循环统一执行 keyword_title_filter

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
#  自动补全规则二：版本替代关系自动发现
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
#  自动补全规则三：samr 详情页抓取替代关系
# ============================================================
def fetch_replaces_from_detail(code, row, session):
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
                    m = re.search(r'代替[^：:]*[：:]\s*([^\n<]{5,80})', html)
                    if m:
                        codes = re.findall(
                            r'(?:GB|GB/T|JGJ|JG/T|CJJ|T/)[^\s,，；;、<]{3,25}', m.group(1))
                        if codes: replaces = '；'.join(codes)
                    mb = re.search(r'被[^代替]{0,5}代替[^：:]*[：:]\s*([^\n<]{5,80})', html)
                    if mb:
                        codes = re.findall(
                            r'(?:GB|GB/T|JGJ|JG/T|CJJ|T/)[^\s,，；;、<]{3,25}', mb.group(1))
                        if codes: replaced_by = '；'.join(codes)
            except Exception:
                pass

    return replaces, replaced_by

# ============================================================
#  关键词列表（v9 新增：体育馆、人造草、木质地板）
# ============================================================
KEYWORDS = [
    # ── 合成材料面层/塑胶跑道 ──
    "合成材料面层", "塑胶跑道", "合成材料跑道", "聚氨酯跑道",
    "橡胶面层运动场", "中小学合成材料",
    # ── 人造草坪（v9新增"人造草"）──
    "人造草坪", "人造草皮", "运动场人造草", "人工草坪",
    "人造草",   # ★ v9 新增
    # ── 颗粒填充料 ──
    "颗粒填充料", "草坪填充橡胶",
    # ── 灯光照明 ──
    "体育场馆照明", "体育照明", "运动场照明", "体育建筑电气",
    # ── 木地板（v9新增"木质地板"）──
    "体育木地板", "运动木地板", "体育用木质地板",
    "木质地板",  # ★ v9 新增
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
    # ── 球类场地（细化）──
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
    "田径场地", "田径场",
    # ── 综合场地/设计 ──
    "体育场地", "运动场地", "体育场馆",
    "体育建筑", "体育公园", "全民健身",
    "学校操场", "体育设施",
    # ── 体育馆（v9新增）──
    "体育馆",   # ★ v9 新增
    # ── 宽泛体育（is_sports严格过滤）──
    "体育",
]

# ============================================================
#  体育标准精确过滤词组（SPORTS_TERMS）
#  v9新增：木质地板、人造草
# ============================================================
SPORTS_TERMS = [
    # 合成材料/塑胶
    "合成材料面层","合成材料跑道","塑胶跑道","聚氨酯跑道","橡胶面层",
    # 人造草坪（v9新增"人造草"）
    "人造草坪","人造草皮","人工草坪","运动场人造草","人造草",
    # 颗粒填充料
    "颗粒填充料","草坪填充",
    # 照明
    "体育场馆照明","体育照明","运动场照明","体育场地照明","体育建筑电气",
    # 木地板（v9新增"木质地板"）
    "体育木地板","运动木地板","体育用木质地板","体育馆木地板","体育馆用木","木质地板",
    # 地板/地胶
    "运动地胶","PVC运动地板","体育地板","运动地板","弹性运动地板",
    "卷材运动地板","聚氯乙烯运动",
    # 围网
    "体育围网","运动场围网","球场围网","体育场围网","围网",
    # 健身
    "室外健身器材","健身路径","公共健身器材","户外健身器材","健身步道",
    "健身器材","健身设施",
    # 体育器材/用品/场地（通用）
    "体育器材","学校体育器材","篮球架","足球门","排球架","乒乓球台",
    "体育用品","运动器材","体育装备",
    # 体育场地/场馆（通用）
    "体育场地","运动场地","体育场馆","体育建筑","体育场所",
    "运动场所","体育馆",
    # 球类场地（细化）
    "足球场地","足球场","足球","篮球场地","篮球场","篮球",
    "网球场地","网球场","网球","排球场地","排球",
    "羽毛球场地","羽毛球","乒乓球场地","乒乓球",
    "手球场","手球","棒球场","棒球","冰球场","冰球",
    "曲棍球","保龄球","壁球","高尔夫球",
    # 田径
    "田径场地","田径场","田径",
    # 游泳
    "游泳场地","游泳馆","游泳池",
    # 综合/公共/学校体育
    "学校操场","体育公园","全民健身","体育设施","体育活动",
    "运动健身","健身房","健身中心","健身俱乐部",
    # 体育建设/规划/管理
    "体育建筑设计","体育场馆设计","体育用地","体育竞技",
    "体育赛事","运动竞赛","竞技场","比赛场地",
    # 运动员/裁判相关
    "运动员","裁判员","体育训练","运动训练",
    # 冰雪/水上/特殊运动
    "滑冰场","冰场","溜冰场","冰雪运动",
    "赛车场","卡丁车","攀岩",
    # 体育行业通用（兜底）
    "体育",
]

def is_sports(title):
    if not title: return False
    return any(term in title for term in SPORTS_TERMS)

# ============================================================
#  v9 新增：关键词标题过滤器
#  ★ 修复"篮球/足球/排球/手球试验方法、体育馆、人造草等抓不到"的根本原因：
#    原来所有 fetch 函数都用 is_sports() 过滤——这本身没错。
#    但 "体育" 之外的关键词抓到的结果里，可能有标题只含行业词而不含关键词，
#    导致看似"没有结果"。v9 统一改为：按关键词本身过滤标题。
# ============================================================
def keyword_title_filter(results, keyword):
    """
    按关键词过滤标准名称：
    - 关键词为"体育"：用 is_sports() 宽泛过滤，接收所有体育相关标准
    - 其他关键词：标准名称必须包含该关键词（精确过滤）
    返回过滤后的列表。
    """
    if keyword == "体育":
        return [r for r in results if is_sports(r.get('title', ''))]
    else:
        return [r for r in results if keyword in r.get('title', '')]

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
        "木地板":"木地板","木质地板":"木地板",
        "地胶":"PVC运动地胶","弹性地板":"PVC运动地胶","运动地板":"PVC运动地胶",
        "围网":"围网",
        "健身器材":"健身路径","健身路径":"健身路径",
        "体育器材":"体育器材",
        "颗粒填充":"颗粒填充料",
        "游泳":"游泳场地",
        "体育建筑":"场地设计","体育公园":"场地设计",
        "体育馆":"体育场馆",
    }
    for kw, cat in cm.items():
        if kw in text: return cat
    return "综合"

def guess_tags(text):
    return [t for t in ["体育","运动","塑胶","合成材料","人造草","照明",
                         "木地板","围网","健身","颗粒","游泳","篮球","足球",
                         "网球","田径","排球","羽毛球","跑道","场地","学校",
                         "体育馆","手球","木质地板"] if t in text][:6]

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
#  来源一-A：std.samr.gov.cn —— 国家标准搜索
# ============================================================
def fetch_samr(keyword, page=1):
    """
    v9：移除内部 is_sports 过滤，由主循环的 keyword_title_filter 统一处理。
    这样搜索"篮球试验方法"等时不会被误过滤。
    """
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
                "pageSize":   50,
                "pageIndex":  page,
            },
            headers={
                'Referer':          'https://std.samr.gov.cn/',
                'Origin':           'https://std.samr.gov.cn',
                'Content-Type':     'application/json',
                'Accept':           'application/json, text/plain, */*',
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
                        log(f"    [DEBUG] samr-GB p{page}: rows={len(rows)} total={total}")
                    for row in rows:
                        code  = clean_samr_code(
                            row.get('C_STD_CODE') or row.get('STD_CODE') or row.get('stdCode') or ''
                        ).strip()
                        title = clean_sacinfo(
                            row.get('C_C_NAME') or row.get('STD_NAME') or row.get('stdName') or ''
                        ).strip()
                        if not code or not title: continue
                        # ★ v9：不在此过滤，交由主循环 keyword_title_filter 处理

                        dept1 = (row.get('ISSUE_DEPT') or row.get('C_ISSUE_DEPT') or '').strip()
                        dept2 = (row.get('ISSUE_UNIT') or row.get('C_ISSUE_UNIT') or
                                 row.get('AUTHOR_UNIT') or '').strip()
                        if dept1 and dept2 and dept2 != dept1:
                            issued_by = dept1 + '、' + dept2
                        else:
                            issued_by = dept1 or dept2
                        issue_date = norm_date(row.get('ISSUE_DATE') or row.get('issueDate'))
                        if not issued_by:
                            issued_by = infer_issued_by(code, issue_date)

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
                            'type':          '国家标准',
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
                    if DEBUG_MODE: log(f"    [DEBUG] samr-GB JSON解析异常: {e}")
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] samr-GB请求异常: {e}")

    return results, total_pages


def fetch_samr_all(keyword):
    """抓取关键词的全部分页（v9：最多120页）"""
    all_results = []
    seen = set()

    results, total_pages = fetch_samr(keyword, 1)
    for r in results:
        if r['code'] not in seen:
            seen.add(r['code'])
            all_results.append(r)

    if total_pages > 1:
        log(f"         samr-GB 总页数:{total_pages}，继续抓取…")
    # ★ v9：页数上限 100→120
    for page in range(2, min(total_pages + 1, 121)):
        time.sleep(0.6)
        results, _ = fetch_samr(keyword, page)
        if not results: break
        for r in results:
            if r['code'] not in seen:
                seen.add(r['code'])
                all_results.append(r)

    return all_results


# ============================================================
#  来源一-B：std.samr.gov.cn —— 行业标准搜索（v9 新增）
#  ★ 修复：篮球/足球/排球/手球"试验方法"等行业标准搜不到的问题
#    原代码只搜 /gb/（国标），行业标准在 /hb/ 接口
# ============================================================
def fetch_samr_industry(keyword, page=1):
    """
    搜索 samr 行业标准 /hb/search/hbQueryPage。
    大量体育器材试验方法标准（如篮球、足球、手球等）属行业标准，
    原来只搜国标导致这些标准全部缺失。
    """
    results = []
    total_pages = 1

    try:
        resp = SESSION.post(
            "https://std.samr.gov.cn/hb/search/hbQueryPage",
            json={
                "searchText": keyword,
                "status":     "",
                "sortField":  "ISSUE_DATE",
                "sortType":   "desc",
                "pageSize":   50,
                "pageIndex":  page,
            },
            headers={
                'Referer':          'https://std.samr.gov.cn/',
                'Origin':           'https://std.samr.gov.cn',
                'Content-Type':     'application/json',
                'Accept':           'application/json, text/plain, */*',
                'X-Requested-With': 'XMLHttpRequest',
            },
            timeout=25
        )
        if resp.ok:
            ct = resp.headers.get('content-type','')
            if 'html' not in ct.lower():
                try:
                    data = resp.json()
                    rows = data.get('rows') or []
                    total = int(data.get('total') or 0)
                    if total > 0:
                        total_pages = max(1, -(-total // 50))
                    if DEBUG_MODE:
                        log(f"    [DEBUG] samr-HB p{page}: rows={len(rows)} total={total}")
                    for row in rows:
                        code  = clean_samr_code(
                            row.get('C_STD_CODE') or row.get('STD_CODE') or row.get('stdCode') or ''
                        ).strip()
                        title = clean_sacinfo(
                            row.get('C_C_NAME') or row.get('STD_NAME') or row.get('stdName') or ''
                        ).strip()
                        if not code or not title: continue

                        dept1 = (row.get('ISSUE_DEPT') or row.get('C_ISSUE_DEPT') or '').strip()
                        dept2 = (row.get('ISSUE_UNIT') or row.get('C_ISSUE_UNIT') or '').strip()
                        if dept1 and dept2 and dept2 != dept1:
                            issued_by = dept1 + '、' + dept2
                        else:
                            issued_by = dept1 or dept2
                        issue_date = norm_date(row.get('ISSUE_DATE') or row.get('issueDate'))
                        if not issued_by:
                            issued_by = infer_issued_by(code, issue_date)

                        results.append({
                            'code':          code,
                            'title':         title,
                            'type':          '行业标准',
                            'status':        norm_status(row.get('STATE') or row.get('STD_STATUS') or ''),
                            'issueDate':     issue_date,
                            'implementDate': norm_date(row.get('ACT_DATE') or row.get('IMPL_DATE')),
                            'abolishDate':   norm_date(row.get('ABOL_DATE')),
                            'issuedBy':      issued_by,
                            'replaces':      None,
                            'replacedBy':    None,
                            'isMandatory':   is_mandatory(code) or '强制' in (row.get('STD_NATURE') or ''),
                        })
                except Exception as e:
                    if DEBUG_MODE: log(f"    [DEBUG] samr-HB JSON解析异常: {e}")
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] samr-HB请求异常: {e}")

    return results, total_pages


def fetch_samr_industry_all(keyword):
    """抓取行业标准全部分页（最多120页）"""
    all_results = []
    seen = set()

    results, total_pages = fetch_samr_industry(keyword, 1)
    for r in results:
        if r['code'] not in seen:
            seen.add(r['code'])
            all_results.append(r)

    if total_pages > 1:
        log(f"         samr-HB 总页数:{total_pages}，继续抓取…")
    for page in range(2, min(total_pages + 1, 121)):
        time.sleep(0.6)
        results, _ = fetch_samr_industry(keyword, page)
        if not results: break
        for r in results:
            if r['code'] not in seen:
                seen.add(r['code'])
                all_results.append(r)

    return all_results


# ============================================================
#  来源二：ttbz.org.cn 全国团体标准信息平台（v9：增加分页）
# ============================================================
def fetch_ttbz(keyword, max_pages=5):
    """
    v9：增加分页抓取（原来只抓第1页）。
    移除内部 is_sports 过滤，由主循环统一处理。
    """
    results = []
    seen = set()
    for page in range(1, max_pages + 1):
        try:
            resp = SESSION.post(
                "https://www.ttbz.org.cn/api/search/standard",
                json={"keyword": keyword, "pageIndex": page, "pageSize": 30},
                headers={
                    'Referer':      'https://www.ttbz.org.cn/',
                    'Origin':       'https://www.ttbz.org.cn',
                    'Content-Type': 'application/json',
                },
                timeout=20
            )
            if resp.ok and 'json' in resp.headers.get('content-type',''):
                data = resp.json()
                rows = data.get('Data') or data.get('data') or []
                if not rows:
                    break
                for row in rows:
                    code  = (row.get('StdCode') or row.get('stdCode') or '').strip()
                    title = (row.get('StdName') or row.get('stdName') or '').strip()
                    if not code or not title or code in seen: continue
                    seen.add(code)
                    # ★ v9：不在此过滤
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
                if len(rows) < 30:
                    break  # 最后一页
            else:
                break
        except Exception as e:
            if DEBUG_MODE: log(f"    [DEBUG] ttbz异常(p{page}): {e}")
            break
        time.sleep(0.4)
    return results


# ============================================================
#  来源三：dbba.sacinfo.org.cn 地方标准信息服务平台
#  v9：每个关键词都抓取（原来每3个才抓），增加分页
# ============================================================
def fetch_dbba(keyword, max_pages=5):
    """
    v9：增加分页，每个关键词都调用（不再跳过）。
    移除内部 is_sports 过滤，由主循环统一处理。
    """
    results = []
    seen = set()
    for page in range(1, max_pages + 1):
        try:
            resp = SESSION.get(
                'https://dbba.sacinfo.org.cn/api/standard/list',
                params={"searchText": keyword, "pageSize": 30, "pageNum": page},
                headers={'Referer': 'https://dbba.sacinfo.org.cn/'},
                timeout=20
            )
            if resp.ok and 'json' in resp.headers.get('content-type',''):
                data = resp.json()
                items = (data.get('data') or {}).get('list') or []
                if not items:
                    break
                for item in items:
                    code  = (item.get('stdCode') or '').strip()
                    title = (item.get('stdName') or '').strip()
                    if not code or not title or code in seen: continue
                    seen.add(code)
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
                if len(items) < 30:
                    break
            else:
                break
        except Exception as e:
            if DEBUG_MODE: log(f"    [DEBUG] dbba异常(p{page}): {e}")
            break
        time.sleep(0.4)
    return results


# ============================================================
#  来源四：openstd.samr.gov.cn 国家标准全文公开（v9 新增）
# ============================================================
def fetch_openstd(keyword, page=1):
    """
    国家标准全文公开系统。
    尝试两个已知端点，获取可供全文下载的国家标准。
    """
    results = []
    total_pages = 1

    endpoints = [
        # 端点A：与 std.samr.gov.cn 共用后端（常见架构）
        ("POST", "https://openstd.samr.gov.cn/bzgk/gb/search/gbQueryPage", {
            "searchText": keyword,
            "status":     "",
            "pageSize":   50,
            "pageIndex":  page,
        }),
        # 端点B：部分版本使用 GET + 查询参数
        ("GET", "https://openstd.samr.gov.cn/bzgk/gb/gbQuery", {
            "searchText": keyword,
            "pageSize":   30,
            "pageIndex":  page,
        }),
    ]

    for method, url, payload in endpoints:
        try:
            if method == "POST":
                resp = SESSION.post(url, json=payload,
                    headers={
                        'Referer': 'https://openstd.samr.gov.cn/bzgk/gb/',
                        'Content-Type': 'application/json',
                        'Accept': 'application/json, */*',
                    }, timeout=25)
            else:
                resp = SESSION.get(url, params=payload,
                    headers={'Referer': 'https://openstd.samr.gov.cn/bzgk/gb/'},
                    timeout=25)

            if not resp.ok:
                continue
            ct = resp.headers.get('content-type', '')
            if 'html' in ct.lower():
                if DEBUG_MODE: log(f"    [DEBUG] openstd 返回HTML，尝试下一端点")
                continue

            data = resp.json()
            rows = data.get('rows') or data.get('data') or data.get('list') or []
            total = int(data.get('total') or data.get('count') or 0)
            if total > 0:
                total_pages = max(1, -(-total // 50))
            if DEBUG_MODE:
                log(f"    [DEBUG] openstd p{page}: rows={len(rows)} total={total}")

            for row in rows:
                code  = clean_samr_code(
                    row.get('C_STD_CODE') or row.get('STD_CODE') or row.get('stdCode') or ''
                ).strip()
                title = clean_sacinfo(
                    row.get('C_C_NAME') or row.get('STD_NAME') or row.get('stdName') or
                    row.get('name') or ''
                ).strip()
                if not code or not title: continue

                issue_date = norm_date(row.get('ISSUE_DATE') or row.get('issueDate'))
                issued_by  = (row.get('ISSUE_DEPT') or row.get('issueDept') or '').strip()
                if not issued_by:
                    issued_by = infer_issued_by(code, issue_date)

                results.append({
                    'code':          code,
                    'title':         title,
                    'type':          '国家标准',
                    'status':        norm_status(row.get('STATE') or row.get('status') or ''),
                    'issueDate':     issue_date,
                    'implementDate': norm_date(row.get('ACT_DATE') or row.get('IMPL_DATE')),
                    'abolishDate':   norm_date(row.get('ABOL_DATE')),
                    'issuedBy':      issued_by,
                    'replaces':      None,
                    'replacedBy':    None,
                    'isMandatory':   is_mandatory(code),
                })
            break  # 成功则不尝试备用端点
        except Exception as e:
            if DEBUG_MODE: log(f"    [DEBUG] openstd端点异常({url}): {e}")
            continue

    return results, total_pages


def fetch_openstd_all(keyword):
    """抓取国家标准全文公开全部分页（最多120页）"""
    all_results = []
    seen = set()

    results, total_pages = fetch_openstd(keyword, 1)
    for r in results:
        if r['code'] not in seen:
            seen.add(r['code'])
            all_results.append(r)

    if total_pages > 1:
        log(f"         openstd 总页数:{total_pages}，继续抓取…")
    for page in range(2, min(total_pages + 1, 121)):
        time.sleep(0.6)
        results, _ = fetch_openstd(keyword, page)
        if not results: break
        for r in results:
            if r['code'] not in seen:
                seen.add(r['code'])
                all_results.append(r)

    return all_results


# ============================================================
#  来源五：cssn.net.cn 中国标准服务网（v9 新增）
# ============================================================
def fetch_cssn(keyword):
    """
    中国标准服务网 https://cssn.net.cn
    该平台以国标/行标/地标为主，部分内容免费检索。
    尝试多个已知搜索端点。
    """
    results = []
    seen = set()

    # 端点列表（按可能性排序）
    attempts = [
        # A. 前台搜索接口（GET）
        {
            "method": "GET",
            "url": "https://cssn.net.cn/cssn/searchStandard",
            "params": {
                "keyword": keyword,
                "pageNo": 1,
                "pageSize": 20,
            },
            "headers": {"Referer": "https://cssn.net.cn/cssn/index"},
        },
        # B. 另一个常见端点
        {
            "method": "GET",
            "url": "https://cssn.net.cn/cssn/stdSearch",
            "params": {
                "searchContent": keyword,
                "pageIndex": 1,
                "pageSize": 20,
            },
            "headers": {"Referer": "https://cssn.net.cn/"},
        },
        # C. POST 方式
        {
            "method": "POST",
            "url": "https://cssn.net.cn/cssn/frontPageSearch",
            "data": {
                "searchContent": keyword,
                "pageIndex": "1",
                "pageSize": "20",
                "searchType": "std",
            },
            "headers": {
                "Referer": "https://cssn.net.cn/cssn/index",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        },
    ]

    for attempt in attempts:
        try:
            method = attempt["method"]
            url    = attempt["url"]
            hdrs   = attempt.get("headers", {})
            if method == "GET":
                resp = SESSION.get(url, params=attempt.get("params", {}),
                                   headers=hdrs, timeout=20)
            else:
                resp = SESSION.post(url, data=attempt.get("data", {}),
                                    headers=hdrs, timeout=20)

            if not resp.ok:
                if DEBUG_MODE: log(f"    [DEBUG] cssn {url} → HTTP {resp.status_code}")
                continue

            ct = resp.headers.get('content-type', '')
            if 'json' not in ct.lower():
                if DEBUG_MODE: log(f"    [DEBUG] cssn {url} 非JSON响应，跳过")
                continue

            data = resp.json()
            # 尝试多种数据结构
            rows = (data.get('data') or data.get('result') or
                    data.get('list') or data.get('rows') or [])
            if isinstance(data, list):
                rows = data

            if DEBUG_MODE:
                log(f"    [DEBUG] cssn {url}: rows={len(rows)}")

            for row in rows:
                code  = (row.get('stdCode') or row.get('code') or
                         row.get('StdCode') or '').strip()
                title = (row.get('stdName') or row.get('name') or
                         row.get('title') or row.get('StdName') or '').strip()
                if not code or not title or code in seen:
                    continue
                seen.add(code)

                issue_date = norm_date(
                    row.get('issueDate') or row.get('publishDate') or row.get('IssueDate'))
                results.append({
                    'code':          code,
                    'title':         title,
                    'type':          guess_type(code),
                    'status':        norm_status(row.get('status') or row.get('Status') or ''),
                    'issueDate':     issue_date,
                    'implementDate': norm_date(row.get('implementDate') or row.get('ImplementDate')),
                    'abolishDate':   None,
                    'issuedBy':      (row.get('issuedBy') or row.get('orgName') or
                                     row.get('publishDept') or
                                     infer_issued_by(code, issue_date)),
                    'isMandatory':   is_mandatory(code),
                })
            if results:
                break  # 成功获取数据，不尝试备用端点
        except Exception as e:
            if DEBUG_MODE: log(f"    [DEBUG] cssn异常({url}): {e}")
            continue

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
            for f in ('status','abolishDate','implementDate','issueDate'):
                nv = item.get(f)
                if nv and nv != orig.get(f):
                    orig[f] = nv; changed = True
            nv_issued = item.get('issuedBy','').strip()
            if nv_issued and len(nv_issued) > len(orig.get('issuedBy','') or ''):
                orig['issuedBy'] = nv_issued; changed = True
            for f in ('replaces', 'replacedBy'):
                nv = item.get(f)
                if nv and not orig.get(f):
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
    log(f"体育标准数据库 — 自动抓取更新 v9")
    log(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"AI摘要: {'DeepSeek' if DEEPSEEK_KEY else '通义千问/百炼' if QWEN_KEY else '未配置'}")
    log(f"抓取平台: samr国标 / samr行标 / ttbz团标 / dbba地标 / openstd全文公开 / cssn标准服务网")
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
    log(f"\n🌐 开始抓取（{len(KEYWORDS)} 个关键词 × 6个来源）…")
    log(f"   ┌─ 来源1: samr 国家标准     std.samr.gov.cn/gb")
    log(f"   ├─ 来源2: samr 行业标准(★新) std.samr.gov.cn/hb")
    log(f"   ├─ 来源3: ttbz 全国团标     www.ttbz.org.cn")
    log(f"   ├─ 来源4: dbba 地方标准     dbba.sacinfo.org.cn")
    log(f"   ├─ 来源5: openstd 国标全文(★新) openstd.samr.gov.cn")
    log(f"   └─ 来源6: cssn 中国标准服务网(★新) cssn.net.cn")

    all_new = []
    total_kw = len(KEYWORDS)

    for i, kw in enumerate(KEYWORDS, 1):
        log(f"\n  [{i:02d}/{total_kw}] 「{kw}」")

        # ── 来源1：samr 国家标准（含分页，最多120页）──
        a = fetch_samr_all(kw)
        a = keyword_title_filter(a, kw)
        time.sleep(0.8)

        # ── 来源2：samr 行业标准（★v9新增，修复试验方法等行标缺失）──
        b = fetch_samr_industry_all(kw)
        b = keyword_title_filter(b, kw)
        time.sleep(0.8)

        # ── 来源3：ttbz 团标（含分页，最多5页）──
        c = fetch_ttbz(kw)
        c = keyword_title_filter(c, kw)
        time.sleep(0.5)

        # ── 来源4：dbba 地方标准（★v9改为每个关键词都抓，含分页）──
        d = fetch_dbba(kw)
        d = keyword_title_filter(d, kw)
        time.sleep(0.5)

        # ── 来源5：openstd 国家标准全文公开（★v9新增）──
        e = fetch_openstd_all(kw)
        e = keyword_title_filter(e, kw)
        time.sleep(0.5)

        # ── 来源6：cssn 中国标准服务网（★v9新增）──
        f_results = fetch_cssn(kw)
        f_results = keyword_title_filter(f_results, kw)
        time.sleep(0.5)

        got = len(a) + len(b) + len(c) + len(d) + len(e) + len(f_results)
        if got:
            all_new.extend(a + b + c + d + e + f_results)
            log(f"         ✅ 国标:{len(a)}  行标:{len(b)}  团标:{len(c)}  "
                f"地标:{len(d)}  全文公开:{len(e)}  cssn:{len(f_results)}")
        else:
            if DEBUG_MODE:
                log(f"         ⚠️  关键词「{kw}」所有来源均未找到结果")

    # ── 合并去重 ─────────────────────────────────────────────
    log(f"\n🔀 合并（原始 {len(all_new)} 条）…")
    before2 = len(standards)
    standards, added, updated_n = merge(standards, all_new)
    log(f"  新增 {added} | 更新 {updated_n} | 原有 {before2} | 最终 {len(standards)}")

    if added == 0 and before2 == 0:
        log("\n  ⚠️  未抓取到任何体育标准")
        log("  可能原因：")
        log("    1. 网络被限流（查看 --debug 原始响应）")
        log("    2. API 端点变更（samr/ttbz/dbba 均可能改版）")
        log("    3. 关键词匹配度低（尝试更宽泛词）")

    # ── 自动补全：发布机构 ────────────────────────────────────
    log("\n🔧 补全：发布机构…")
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
    log(f"  补填发布机构 {filled_issued} 条")

    # ── 自动补全：版本替代关系 ────────────────────────────────
    log("\n🔧 补全：版本替代关系…")
    filled_replaces = auto_fill_replaces(standards)
    log(f"  发现版本替代关系 {filled_replaces} 条")

    # ── 摘要（AI Key 配置后才生成）────────────────────────────
    has_key = bool(QWEN_KEY or DEEPSEEK_KEY)
    if use_ai and not has_key:
        log("\n⚠️  --ai 参数需要先在 scripts/.env 配置 QWEN_KEY 或 DEEPSEEK_KEY")
    elif has_key or use_ai:
        if use_ai:
            log("\n🤖 AI摘要（--ai 模式：强制重新生成所有条目）…")
        else:
            missing_summary = sum(1 for s in standards if not s.get('summary','').strip())
            log(f"\n🤖 AI摘要自动补全（缺摘要 {missing_summary} 条）…")
        standards = ai_enrich_batch(standards, force=use_ai)
    else:
        missing_summary = sum(1 for s in standards if not s.get('summary','').strip())
        if missing_summary > 0:
            log(f"\n💡 {missing_summary} 条标准缺少摘要（不自动生成，避免内容不准确）")
            log("   配置 QWEN_KEY 或 DEEPSEEK_KEY 后运行 --ai 可自动补全")

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
