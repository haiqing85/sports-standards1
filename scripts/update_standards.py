#!/usr/bin/env python3
"""
体育建设标准自动抓取 & 更新脚本 v8.2
======================================
v8.2 调整：
  1. 抓取页数上限调整为120页
  2. 新增抓取关键词：体育馆、人造草、木质地板
  3. 关键词"体育"搜索结果全部采纳，不做二次过滤
  4. 全量抓取5大平台：国标平台、团标平台、地标平台、国家标准全文公开平台、中国标准服务网
  5. 仅抓取标准元数据，不抓取标准正文/全文内容，其余元数据字段完整保留
  6. 抓取时间范围扩大至1950年以来发布的全部标准
  7. 恢复发布机构自动推断、版本替代关系自动补全、AI摘要补全功能
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
    """
    根据编号前缀+发布年份推断发布机构，API返回为空时使用。
    2018年后的国家标准通常由两个机构联合发布：
      国家市场监督管理总局、国家标准化管理委员会
    """
    if not code: return ''
    year = 0
    if issue_date:
        try: year = int(str(issue_date)[:4])
        except: pass
    cu = re.sub(r'\s+', '', code).upper()
    # 国家标准 GB / GB/T / GB/Z
    if re.match(r'^GB', cu):
        if year >= 2018:
            return '国家市场监督管理总局、国家标准化管理委员会'
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
    if cu.startswith('T/'):      return ''
    # 地方标准
    if cu.startswith('DB'): return ''
    return ''
# ============================================================
#  自动补全规则二：版本替代关系自动发现
# ============================================================
def auto_fill_replaces(standards):
    """
    扫描全库，自动发现同编号不同年份的版本关系，填写 replaces/replacedBy。
    只填写目前为空的字段，不覆盖已有数据。
    返回更新条数。
    """
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
#  自动补全规则三：替代关系API字段提取（不抓取详情页正文）
# ============================================================
def fetch_replaces_from_api(row):
    """
    仅从API返回字段提取替代关系，不访问详情页、不抓取标准正文内容
    """
    replaces    = None
    replaced_by = None
    # 仅从API返回字段读取替代关系，移除详情页抓取逻辑
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
#  关键词列表（新增体育馆、人造草、木质地板）
# ============================================================
KEYWORDS = [
    # ── 合成材料面层/塑胶跑道 ──
    "合成材料面层", "塑胶跑道", "合成材料跑道", "聚氨酯跑道",
    "橡胶面层运动场", "中小学合成材料",
    # ── 人造草坪 ──
    "人造草坪", "人造草皮", "运动场人造草", "人工草坪", "人造草",
    # ── 颗粒填充料 ──
    "颗粒填充料", "草坪填充橡胶",
    # ── 灯光照明 ──
    "体育场馆照明", "体育照明", "运动场照明", "体育建筑电气",
    # ── 木地板 ──
    "体育木地板", "运动木地板", "体育用木质地板", "木质地板",
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
    # ── 球类场地（细化） ──
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
    "体育场地", "运动场地", "体育场馆", "体育馆",
    "体育建筑", "体育公园", "全民健身",
    "学校操场", "体育设施",
    # ── 宽泛体育兜底 ──
    "体育",
]
# ============================================================
#  体育标准过滤词组（非"体育"关键词生效）
# ============================================================
SPORTS_TERMS = [
    "合成材料面层","合成材料跑道","塑胶跑道","聚氨酯跑道","橡胶面层",
    "人造草坪","人造草皮","人工草坪","运动场人造草","人造草",
    "颗粒填充料","草坪填充",
    "体育场馆照明","体育照明","运动场照明","体育场地照明","体育建筑电气",
    "体育木地板","运动木地板","体育用木质地板","体育馆木地板","体育馆用木","木质地板",
    "运动地胶","PVC运动地板","体育地板","运动地板","弹性运动地板",
    "卷材运动地板","聚氯乙烯运动",
    "体育围网","运动场围网","球场围网","体育场围网","围网",
    "室外健身器材","健身路径","公共健身器材","户外健身器材","健身步道",
    "健身器材","健身设施",
    "体育器材","学校体育器材","篮球架","足球门","排球架","乒乓球台",
    "体育用品","运动器材","体育装备",
    "体育场地","运动场地","体育场馆","体育建筑","体育场所","体育馆",
    "运动场所","体育馆",
    "足球场地","足球场","足球","篮球场地","篮球场","篮球",
    "网球场地","网球场","网球","排球场地","排球",
    "羽毛球场地","羽毛球","乒乓球场地","乒乓球",
    "手球场","手球","棒球场","棒球","冰球场","冰球",
    "曲棍球","保龄球","壁球","高尔夫球",
    "田径场地","田径场","田径",
    "游泳场地","游泳馆","游泳池",
    "学校操场","体育公园","全民健身","体育设施","体育活动",
    "运动健身","健身房","健身中心","健身俱乐部",
    "体育建筑设计","体育场馆设计","体育用地","体育竞技",
    "体育赛事","运动竞赛","竞技场","比赛场地",
    "运动员","裁判员","体育训练","运动训练",
    "滑冰场","冰场","溜冰场","冰雪运动",
    "赛车场","卡丁车","攀岩",
    "体育",
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
def clean_code(c):
    return re.sub(r'\s+', ' ', c).strip()
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
# ============================================================
#  标准条目构建（完整元数据，无正文内容）
# ============================================================
def build_entry(item):
    code  = item.get('code','')
    title = clean_sacinfo(item.get('title',''))
    # 发布机构优先用API返回值，为空时按规则推断
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
#  来源一：std.samr.gov.cn 国家标准平台（120页上限）
# ============================================================
def fetch_samr(keyword, page=1):
    """
    仅抓取列表页元数据，不访问详情页、不抓取标准正文
    关键词为"体育"时，结果全部采纳，不做过滤
    无时间范围限制，覆盖1950年以来全部标准
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
                        # 关键词"体育"全部采纳，其他关键词需过滤
                        if keyword != "体育" and not is_sports(title):
                            continue
                        # 发布机构处理
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
                        # 替代关系仅从API字段提取，不访问详情页
                        replaces_val, replaced_by_val = fetch_replaces_from_api(row)
                        # 仅保留元数据，无正文内容
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
    """抓取关键词全部分页，上限120页"""
    all_results = []
    seen = set()
    results, total_pages = fetch_samr(keyword, 1)
    for r in results:
        if r['code'] not in seen:
            seen.add(r['code'])
            all_results.append(r)
    if total_pages > 1:
        log(f"         总页数:{total_pages}，继续抓取…")
    # 120页上限，覆盖6000条数据
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
#  来源二：www.ttbz.org.cn 全国团体标准信息平台
# ============================================================
def fetch_ttbz(keyword):
    """仅抓取列表页元数据，不抓取正文内容"""
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
                    # 关键词"体育"全部采纳
                    if keyword != "体育" and not is_sports(title):
                        continue
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
#  来源三：dbba.sacinfo.org.cn 地方标准信息服务平台
# ============================================================
def fetch_dbba(keyword):
    """仅抓取列表页元数据，不抓取正文内容"""
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
                    # 关键词"体育"全部采纳
                    if keyword != "体育" and not is_sports(title):
                        continue
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
#  来源四：openstd.samr.gov.cn 国家标准全文公开平台
# ============================================================
def fetch_openstd(keyword):
    """仅抓取列表页元数据，不访问详情页、不抓取标准正文全文"""
    results = []
    try:
        resp = SESSION.get(
            'https://openstd.samr.gov.cn/api/jpublic/searchAllStandard',
            params={
                'p': 1,
                'pageSize': 50,
                'kw': keyword,
                'fy': 'all',
                'lx': 'all',
            },
            headers={'Referer':'https://openstd.samr.gov.cn/'},
            timeout=20
        )
        if resp.ok:
            data = resp.json()
            rows = data.get('data') or []
            for row in rows:
                code  = (row.get('code') or '').strip()
                title = (row.get('name') or '').strip()
                if not code or not title: continue
                # 关键词"体育"全部采纳
                if keyword != "体育" and not is_sports(title):
                    continue
                results.append({
                    'code':          code,
                    'title':         title,
                    'type':          guess_type(code),
                    'status':        norm_status(row.get('status') or ''),
                    'issueDate':     norm_date(row.get('issueDate')),
                    'implementDate': norm_date(row.get('implementDate')),
                    'abolishDate':   norm_date(row.get('abolishDate')),
                    'issuedBy':      (row.get('issueDept') or '').strip(),
                    'isMandatory':   is_mandatory(code),
                })
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] openstd异常: {e}")
    return results
# ============================================================
#  来源五：cssn.net.cn 中国标准服务网
# ============================================================
def fetch_cssn(keyword):
    """仅抓取列表页元数据，不访问详情页、不抓取标准正文"""
    results = []
    try:
        # 初始化会话获取cookie
        SESSION.get('https://cssn.net.cn/cssn/index', timeout=15)
        # 搜索请求
        resp = SESSION.post(
            'https://cssn.net.cn/cssn/search/standardSearch',
            data={
                'searchText': keyword,
                'pageNo': 1,
                'pageSize': 50,
                'sortField': 'pubDate',
                'sortOrder': 'desc',
            },
            headers={
                'Referer': 'https://cssn.net.cn/cssn/index',
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            timeout=20
        )
        if resp.ok:
            html = resp.text
            # 提取列表页元数据，不进入详情页
            item_pattern = re.compile(r'<div class="standard-item">.*?</div>', re.S)
            code_pattern = re.compile(r'<a[^>]+class="std-code"[^>]*>([^<]+)</a>')
            name_pattern = re.compile(r'<a[^>]+class="std-name"[^>]*>([^<]+)</a>')
            status_pattern = re.compile(r'<span class="status-tag">([^<]+)</span>')
            date_pattern = re.compile(r'发布日期：\s*(\d{4}-\d{2}-\d{2})')
            dept_pattern = re.compile(r'发布机构：\s*([^<\n]+)')
            
            items = item_pattern.findall(html)
            for item in items:
                code_match = code_pattern.search(item)
                name_match = name_pattern.search(item)
                if not code_match or not name_match:
                    continue
                code = code_match.group(1).strip()
                title = name_match.group(1).strip()
                if not code or not title: continue
                # 关键词"体育"全部采纳
                if keyword != "体育" and not is_sports(title):
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
        if DEBUG_MODE: log(f"    [DEBUG] cssn异常: {e}")
    return results
# ============================================================
#  AI摘要补全（基于编号和名称生成，不抓取正文内容）
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
#  标准状态在线核查
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
# ============================================================
#  数据合并去重
# ============================================================
def merge(existing, new_items):
    idx = {norm_code(s['code']): i for i, s in enumerate(existing)}
    added = updated_n = 0
    for item in new_items:
        cn = norm_code(item.get('code',''))
        if not cn: continue
        if cn in idx:
            orig, changed = existing[idx[cn]], False
            # 核心字段更新，新值更完整时覆盖
            for f in ('status','abolishDate','implementDate','issueDate'):
                nv = item.get(f)
                if nv and nv != orig.get(f):
                    orig[f] = nv
                    changed = True
            # 发布机构：新值更完整时更新
            nv_issued = item.get('issuedBy','').strip()
            if nv_issued and len(nv_issued) > len(orig.get('issuedBy','') or ''):
                orig['issuedBy'] = nv_issued
                changed = True
            # 替代关系：原值为空时填充
            for f in ('replaces', 'replacedBy'):
                nv = item.get(f)
                if nv and not orig.get(f):
                    orig[f] = nv
                    changed = True
            # 不覆盖手动录入的摘要、本地文件路径
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
    log("="*60)
    log(f"体育建设标准数据库 — 自动抓取更新 v8.2")
    log(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"核心配置: 120页抓取上限 | 5大平台 | 1950年至今 | 仅元数据抓取，不抓取标准正文")
    log(f"AI能力: {'DeepSeek' if DEEPSEEK_KEY else '通义千问/百炼' if QWEN_KEY else '未配置'}")
    log("="*60)

    # 加载现有数据库
    db, standards = load_db()

    # 自动清理非体育标准
    before_clean = len(standards)
    standards = [s for s in standards if is_sports(clean_sacinfo(s.get('title','')))]
    removed = before_clean - len(standards)
    if removed > 0:
        log(f"\n🗑️  自动清理非体育标准：移除 {removed} 条，剩余 {len(standards)} 条")

    # 清洗标题中的标签
    for i, std in enumerate(standards):
        if std.get('title') and '<sacinfo>' in std['title']:
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

    # 多平台全量抓取
    log(f"\n🌐 开始多平台抓取（{len(KEYWORDS)} 个关键词 × 5个平台）…")
    all_new = []
    total_kw = len(KEYWORDS)

    for i, kw in enumerate(KEYWORDS, 1):
        log(f"  [{i:02d}/{total_kw}] 关键词：「{kw}」")
        # 1. 国标/行标平台（带分页）
        samr_data = fetch_samr_all(kw)
        time.sleep(0.8)
        # 2. 全国团体标准平台
        ttbz_data = fetch_ttbz(kw)
        time.sleep(0.5)
        # 3. 地方标准平台
        dbba_data = fetch_dbba(kw)
        time.sleep(0.5)
        # 4. 国家标准全文公开平台
        openstd_data = fetch_openstd(kw)
        time.sleep(0.5)
        # 5. 中国标准服务网
        cssn_data = fetch_cssn(kw)
        time.sleep(0.5)

        # 统计本次抓取结果
        got_total = len(samr_data) + len(ttbz_data) + len(dbba_data) + len(openstd_data) + len(cssn_data)
        if got_total:
            all_new.extend(samr_data + ttbz_data + dbba_data + openstd_data + cssn_data)
            log(f"         ✅ 国标平台:{len(samr_data)}  团标平台:{len(ttbz_data)}  地标平台:{len(dbba_data)}  公开平台:{len(openstd_data)}  服务网:{len(cssn_data)}")

    # 合并去重
    log(f"\n🔀 开始合并去重（原始抓取 {len(all_new)} 条）…")
    before_merge = len(standards)
    standards, added, updated_n = merge(standards, all_new)
    log(f"  新增 {added} 条 | 更新 {updated_n} 条 | 原有 {before_merge} 条 | 最终 {len(standards)} 条")

    if added == 0 and before_merge == 0:
        log("\n  ⚠️  未抓取到任何有效体育标准，可开启--debug模式排查抓取异常")

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

    # 保存最终数据
    save_db(db, standards, dry_run)

    # 生成数据统计报告
    miss_issued   = sum(1 for s in standards if not s.get('issuedBy'))
    miss_summary  = sum(1 for s in standards if not s.get('summary','').strip())
    miss_replaces = sum(1 for s in standards if s.get('status') == '废止' and not s.get('replacedBy'))
    active = sum(1 for s in standards if s.get('status')=='现行')
    abol   = sum(1 for s in standards if s.get('status')=='废止')
    coming = sum(1 for s in standards if s.get('status')=='即将实施')

    log(f"\n📊 最终数据统计")
    log(f"  总标准数：{len(standards)} 条")
    log(f"  现行标准：{active} 条 | 废止标准：{abol} 条 | 即将实施：{coming} 条")
    log(f"\n📋 字段完整性报告")
    log(f"  缺发布机构：{miss_issued} 条  {'✅ 全部补全' if miss_issued==0 else '⚠️ 可手动补全'}")
    log(f"  缺标准摘要：{miss_summary} 条  {'✅ 全部补全' if miss_summary==0 else '⚠️ 配置AI Key可自动补全'}")
    log(f"  废止标准缺替代关系：{miss_replaces} 条  {'✅ 全部补全' if miss_replaces==0 else '⚠️ 可手动补全'}")
    log("="*60)

# ============================================================
#  命令行参数入口
# ============================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='体育建设标准自动抓取更新脚本')
    parser.add_argument('--dry',   action='store_true', help='预览模式，抓取结果不写入文件')
    parser.add_argument('--check', action='store_true', help='仅核查现有标准状态，不执行新抓取')
    parser.add_argument('--ai',    action='store_true', help='强制重新生成所有标准的AI摘要')
    parser.add_argument('--debug', action='store_true', help='调试模式，输出详细异常日志')
    args = parser.parse_args()
    DEBUG_MODE = args.debug
    run(dry_run=args.dry, check_only=args.check, use_ai=args.ai)