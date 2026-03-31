#!/usr/bin/env python3
"""
体育建设标准自动抓取 & 更新脚本 v10
======================================
v10 重构核心：
  ★ 根本原则：是否收录 = 标准名称(title)中是否含关键词，与标准正文内容无关
  ★ 彻底去掉 is_sports() 对收录的影响（只保留初始库清理用）
  ★ 所有过滤统一由 title_has_keyword(title, kw) 决定

v10 主要修复：
  1. 早停机制：samr 连续 EARLY_STOP_PAGES 页无标题匹配则停止
     （samr 是全文搜索，"木质地板"返回1539页但大多数标题不含该词）
  2. 修正 ttbz / dbba API 接口，增加多路径 fallback
  3. 过滤逻辑彻底简化：keyword in title（纯字面包含）
  4. 大幅降低 sleep，配合早停避免超时
  5. 保留 openstd / cssn 平台（v9新增，共5个平台）
  6. 保留新关键词：体育馆、人造草、木质地板、各球类试验方法

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

# ─── 可调参数 ────────────────────────────────────────────────────
PAGE_SIZE        = 50   # samr 每页条数
MAX_PAGES        = 200  # samr 每关键词最大页数上限（配合早停，实际远不到200页）
EARLY_STOP_PAGES = 8    # 连续几页标题无匹配则停止（早停阈值）
TTBZ_PAGE_SIZE   = 50   # 团标平台每页条数，最多 5 页
DBBA_PAGE_SIZE   = 50   # 地标平台每页条数，最多 5 页
SLEEP_PAGE       = 0.35 # 翻页间隔（秒）
SLEEP_KW         = 0.5  # 关键词间隔（秒）
# ─────────────────────────────────────────────────────────────────

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
#  工具函数
# ============================================================
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
      'AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/124.0.0.0 Safari/537.36')

def make_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.2, status_forcelist=[429,500,502,503,504])
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
        slash_map = {'GBT': 'GB/T', 'GBZ': 'GB/Z', 'JGT': 'JG/T', 'GAT': 'GA/T'}
        prefix = slash_map.get(prefix, prefix)
        return f"{prefix} {number}".strip() if prefix else number
    return re.sub(r'<[^>]+>', '', raw).strip()

def norm_status(raw):
    raw = str(raw or '').strip()
    if any(x in raw for x in ['现行', '有效', '执行', '施行']): return '现行'
    if any(x in raw for x in ['废止', '作废', '撤销', '废弃']): return '废止'
    if any(x in raw for x in ['即将', '待实施', '未实施']):     return '即将实施'
    return '现行'

def norm_date(raw):
    if not raw: return None
    raw = str(raw).strip()
    if raw.isdigit() and len(raw) >= 10:
        try:
            ts = int(raw)
            if ts > 1e11: ts //= 1000
            return datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
        except Exception:
            pass
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
    for prefix, t in [('GB/T', '国家标准'), ('GB', '国家标准'),
                       ('JGJ', '行业标准'), ('JG/T', '行业标准'),
                       ('CJJ', '行业标准'), ('T/', '团标'), ('DB', '地方标准')]:
        if cu.startswith(norm_code(prefix)): return t
    return '国家标准'

def guess_category(text):
    pairs = [
        ('合成材料', '合成材料面层'), ('塑胶跑道', '合成材料面层'),
        ('人造草',  '人造草坪'),  ('草坪', '人造草坪'),
        ('照明',   '灯光照明'),  ('灯光', '灯光照明'),
        ('木地板',  '木地板'),   ('木质地板', '木地板'),
        ('地胶',   'PVC运动地胶'), ('弹性地板', 'PVC运动地胶'),
        ('运动地板', 'PVC运动地胶'),
        ('围网',   '围网'),
        ('健身器材', '健身路径'), ('健身路径', '健身路径'),
        ('体育器材', '体育器材'),
        ('颗粒填充', '颗粒填充料'),
        ('游泳',   '游泳场地'),
        ('体育建筑', '场地设计'), ('体育公园', '场地设计'),
        ('体育馆',  '体育馆'),
    ]
    for kw, cat in pairs:
        if kw in text: return cat
    return '综合'

def guess_tags(text):
    candidates = [
        '体育', '运动', '塑胶', '合成材料', '人造草', '照明',
        '木地板', '木质地板', '围网', '健身', '颗粒', '游泳',
        '篮球', '足球', '网球', '田径', '排球', '羽毛球',
        '乒乓球', '手球', '跑道', '场地', '学校', '体育馆',
    ]
    return [t for t in candidates if t in text][:6]

# ============================================================
#  发布机构推断
# ============================================================
def infer_issued_by(code, issue_date):
    if not code: return ''
    year = 0
    if issue_date:
        try: year = int(str(issue_date)[:4])
        except: pass
    cu = re.sub(r'\s+', '', code).upper()
    if re.match(r'^GB', cu):
        if year >= 2018: return '国家市场监督管理总局、国家标准化管理委员会'
        if year >= 2001: return '国家质量监督检验检疫总局'
        if year >= 1993: return '国家技术监督局'
        return '国家标准化管理委员会'
    if re.match(r'^(JGJ|JGJT|JGT|CJJ|CJJT)', cu):
        return '住房和城乡建设部' if year >= 2008 else '建设部'
    if cu.startswith('T/SGTAS'): return '中国运动场地联合会'
    if cu.startswith('T/CECS'):  return '中国工程建设标准化协会'
    if cu.startswith('T/CSUS'):  return '中国城市科学研究会'
    if cu.startswith('T/CAECS'): return '中国建设教育协会'
    if cu.startswith('T/CSTM'):  return '中关村材料试验技术联盟'
    if cu.startswith('T/'):      return ''
    if cu.startswith('DB'):      return ''
    return ''

# ============================================================
#  版本替代关系自动发现
# ============================================================
def auto_fill_replaces(standards):
    groups = {}
    for s in standards:
        code = s.get('code', '')
        m = re.match(r'^(.+?)\s*[－\-–]\s*(\d{4})$', code.strip())
        if m:
            base = re.sub(r'\s+', '', m.group(1)).upper()
            year = int(m.group(2))
            groups.setdefault(base, []).append({'std': s, 'year': year, 'code': code})
    updated = 0
    for base, versions in groups.items():
        if len(versions) < 2: continue
        versions.sort(key=lambda x: x['year'])
        for i, ver in enumerate(versions):
            s = ver['std']
            if i > 0 and not s.get('replaces'):
                s['replaces'] = versions[i - 1]['code']; updated += 1
            if i < len(versions) - 1 and not s.get('replacedBy'):
                s['replacedBy'] = versions[i + 1]['code']; updated += 1
            if (i < len(versions) - 1
                    and s.get('status') == '现行'
                    and versions[i + 1]['std'].get('status') == '现行'
                    and not s.get('abolishDate')):
                s['status'] = '废止'; updated += 1
    return updated

# ============================================================
#  ★ 核心过滤函数（v10 简化）
#
#  规则：标准名称(title)中是否含关键词(keyword) → 字面子串匹配
#  不再使用 is_sports()，与标准正文内容完全无关
# ============================================================
def title_has_keyword(title: str, keyword: str) -> bool:
    """标准名称中含有关键词（字面子串）则返回 True。"""
    return bool(title) and keyword in title

def filter_by_title(results: list, keyword: str) -> list:
    """
    对抓取结果按"标题含关键词"过滤，返回保留列表。
    同时打印 debug 信息。
    """
    kept    = [r for r in results if title_has_keyword(r.get('title', ''), keyword)]
    dropped = len(results) - len(kept)
    if DEBUG_MODE and (dropped or kept):
        log(f"    [filter]「{keyword}」: 共{len(results)} → 保留{len(kept)} 过滤{dropped}")
    return kept

# ============================================================
#  is_sports：仅用于初始库清理（不影响抓取收录逻辑）
# ============================================================
_SPORTS_TERMS_FOR_CLEANUP = [
    "合成材料面层", "合成材料跑道", "塑胶跑道", "聚氨酯跑道", "橡胶面层",
    "人造草坪", "人造草皮", "人工草坪", "运动场人造草", "人造草",
    "颗粒填充料", "草坪填充",
    "体育场馆照明", "体育照明", "运动场照明", "体育场地照明", "体育建筑电气",
    "体育木地板", "运动木地板", "体育用木质地板", "体育馆木地板", "木质地板",
    "运动地胶", "PVC运动地板", "体育地板", "运动地板", "弹性运动地板",
    "卷材运动地板", "聚氯乙烯运动",
    "体育围网", "运动场围网", "球场围网", "围网",
    "室外健身器材", "健身路径", "公共健身器材", "户外健身器材", "健身步道",
    "健身器材", "健身设施",
    "体育器材", "学校体育器材", "篮球架", "足球门", "排球架", "乒乓球台",
    "体育用品", "运动器材",
    "体育场地", "运动场地", "体育场馆", "体育建筑", "体育馆",
    "足球场地", "足球场", "足球", "篮球场地", "篮球场", "篮球",
    "网球场地", "网球场", "网球", "排球场地", "排球",
    "羽毛球场地", "羽毛球", "乒乓球场地", "乒乓球",
    "手球场", "手球", "棒球场", "棒球", "冰球场", "冰球",
    "曲棍球", "保龄球", "壁球", "高尔夫球",
    "田径场地", "田径场", "田径",
    "游泳场地", "游泳馆", "游泳池",
    "学校操场", "体育公园", "全民健身", "体育设施", "体育活动",
    "运动健身", "健身房", "健身中心",
    "体育建筑设计", "体育场馆设计", "体育用地", "体育竞技",
    "体育赛事", "运动竞赛", "比赛场地",
    "运动员", "体育训练", "运动训练",
    "滑冰场", "冰场", "冰雪运动", "赛车场", "攀岩",
    "体育",  # 兜底
]

def is_sports_for_cleanup(title):
    """仅用于清理旧库中明显的非体育标准，不影响新标准收录。"""
    if not title: return False
    return any(t in title for t in _SPORTS_TERMS_FOR_CLEANUP)

# ============================================================
#  关键词列表
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
    # ── 球类场地/器材 ──
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
    # ── 球类试验/检验方法（独立关键词，修复samr全文搜索漏标题） ──
    "篮球试验方法", "篮球检验",
    "足球试验方法", "足球检验",
    "排球试验方法",
    "手球试验方法",
    # ── 田径 ──
    "田径场地", "田径场",
    # ── 综合场地/设计 ──
    "体育场地", "运动场地", "体育场馆",
    "体育建筑", "体育公园", "全民健身",
    "学校操场", "体育设施",
    # ── 体育馆 ──
    "体育馆",
    # ── 宽泛体育（标题含"体育"即收录） ──
    "体育",
]

# ============================================================
#  build_entry
# ============================================================
def build_entry(item):
    code  = item.get('code', '')
    title = clean_sacinfo(item.get('title', ''))
    issued_by = item.get('issuedBy', '').strip()
    if not issued_by:
        issued_by = infer_issued_by(code, item.get('issueDate'))
    return {
        'id':            make_id(code),
        'code':          code,
        'title':         title,
        'english':       '',
        'type':          item.get('type') or guess_type(code),
        'status':        item.get('status', '现行'),
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
        'source':        item.get('source') or '',
    }

# ============================================================
#  来源一：std.samr.gov.cn 国标/行标
#
#  ★ v10 早停机制（关键）：
#    samr API 是全文搜索（含标准正文），搜"木质地板"会返回1539页，
#    但其中绝大多数标题不含该词。
#    策略：逐页抓取，对每页结果按 title_has_keyword 过滤；
#          若连续 EARLY_STOP_PAGES 页过滤后均为0条，则停止。
# ============================================================
def fetch_samr_one_page(keyword, page):
    """请求 samr 单页，返回 (raw_rows_list, total_pages)。"""
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
            },
            headers={
                'Referer':          'https://std.samr.gov.cn/',
                'Origin':           'https://std.samr.gov.cn',
                'Content-Type':     'application/json',
                'Accept':           'application/json, text/plain, */*',
                'X-Requested-With': 'XMLHttpRequest',
            },
            timeout=30
        )
        if not resp.ok:
            if DEBUG_MODE: log(f"    [DEBUG] samr HTTP{resp.status_code} p{page}")
            return [], 1
        ct = resp.headers.get('content-type', '')
        if 'html' in ct.lower():
            if DEBUG_MODE: log(f"    [DEBUG] samr返回HTML p{page}")
            return [], 1
        data       = resp.json()
        total      = int(data.get('total') or 0)
        total_pg   = max(1, -(-total // PAGE_SIZE)) if total else 1
        raw_rows   = data.get('rows') or []
        if DEBUG_MODE:
            log(f"    [DEBUG] samr p{page}/{total_pg}: raw={len(raw_rows)} total={total}")
        return raw_rows, total_pg
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] samr p{page}异常: {e}")
        return [], 1

def _parse_samr_row(row):
    """将 samr 原始 row 转为标准 dict，不做过滤。"""
    code = clean_samr_code(
        row.get('C_STD_CODE') or row.get('STD_CODE') or row.get('stdCode') or ''
    ).strip()
    title = clean_sacinfo(
        row.get('C_C_NAME') or row.get('STD_NAME') or row.get('stdName') or ''
    ).strip()
    if not code or not title:
        return None
    dept1 = (row.get('ISSUE_DEPT') or row.get('C_ISSUE_DEPT') or '').strip()
    dept2 = (row.get('ISSUE_UNIT') or row.get('C_ISSUE_UNIT') or
              row.get('AUTHOR_UNIT') or '').strip()
    issued_by = (dept1 + '、' + dept2) if (dept1 and dept2 and dept2 != dept1) else (dept1 or dept2)
    issue_date = norm_date(row.get('ISSUE_DATE') or row.get('issueDate'))
    if not issued_by:
        issued_by = infer_issued_by(code, issue_date)
    return {
        'code':          code,
        'title':         title,
        'status':        norm_status(row.get('STATE') or row.get('STD_STATUS') or ''),
        'issueDate':     issue_date,
        'implementDate': norm_date(row.get('ACT_DATE') or row.get('IMPL_DATE')),
        'abolishDate':   norm_date(row.get('ABOL_DATE')),
        'issuedBy':      issued_by,
        'replaces':      clean_sacinfo(row.get('C_SUPERSEDE_CODE') or row.get('SUPERSEDE_CODE') or '').strip() or None,
        'replacedBy':    clean_sacinfo(row.get('C_REPLACED_CODE') or row.get('REPLACED_CODE') or '').strip() or None,
        'isMandatory':   is_mandatory(code) or '强制' in (row.get('STD_NATURE') or ''),
        'source':        'samr',
    }

def fetch_samr_all(keyword):
    """
    抓取 samr 某关键词的全部相关标准（带早停）。
    每页抓完后立刻按 title_has_keyword 过滤；
    连续 EARLY_STOP_PAGES 页无匹配 → 停止翻页。
    """
    all_results  = []
    seen_codes   = set()
    empty_streak = 0   # 连续无标题匹配的页数

    raw_rows, total_pages = fetch_samr_one_page(keyword, 1)
    parsed = [_parse_samr_row(r) for r in raw_rows]
    matched_p1 = [p for p in parsed if p and title_has_keyword(p['title'], keyword)]
    for p in matched_p1:
        if p['code'] not in seen_codes:
            seen_codes.add(p['code']); all_results.append(p)
    empty_streak = 0 if matched_p1 else 1

    if total_pages > 1:
        log(f"         samr总页:{total_pages}，早停阈值:{EARLY_STOP_PAGES}页…")

    for page in range(2, min(total_pages + 1, MAX_PAGES + 1)):
        if empty_streak >= EARLY_STOP_PAGES:
            if DEBUG_MODE:
                log(f"    [DEBUG] 早停: 连续{empty_streak}页无匹配，停止 (已搜{page-1}页)")
            break
        time.sleep(SLEEP_PAGE)
        raw_rows, _ = fetch_samr_one_page(keyword, page)
        if not raw_rows:
            empty_streak += 1
            continue
        parsed = [_parse_samr_row(r) for r in raw_rows]
        matched = [p for p in parsed if p and title_has_keyword(p['title'], keyword)]
        if matched:
            empty_streak = 0
            for p in matched:
                if p['code'] not in seen_codes:
                    seen_codes.add(p['code']); all_results.append(p)
        else:
            empty_streak += 1

    return all_results

# ============================================================
#  来源二：ttbz.org.cn 全国团体标准信息平台
#  v10：多路径 fallback + 分页
# ============================================================
def fetch_ttbz(keyword):
    """
    尝试多个已知接口路径，取第一个有效响应。
    """
    all_results, seen = [], set()

    # 接口候选列表（按可用性优先排序）
    _TTBZ_APIS = [
        # 路径A：JSON POST（官网搜索用）
        dict(method='POST',
             url='https://www.ttbz.org.cn/StandardManage/Search',
             json_body=lambda kw, pg: {"keyword": kw, "pageIndex": pg, "pageSize": TTBZ_PAGE_SIZE},
             rows_key=lambda d: d.get('Data') or d.get('data') or [],
             code_keys=('StandardCode', 'StdCode', 'stdCode'),
             name_keys=('StandardName', 'StdName', 'stdName'),
             org_keys=('OrgName', 'orgName')),
        # 路径B：JSON POST（API子路径）
        dict(method='POST',
             url='https://www.ttbz.org.cn/api/search/standard',
             json_body=lambda kw, pg: {"keyword": kw, "pageIndex": pg, "pageSize": TTBZ_PAGE_SIZE},
             rows_key=lambda d: d.get('Data') or d.get('data') or [],
             code_keys=('StdCode', 'stdCode', 'StandardCode'),
             name_keys=('StdName', 'stdName', 'StandardName'),
             org_keys=('OrgName', 'orgName')),
        # 路径C：GET（部分版本支持）
        dict(method='GET',
             url='https://www.ttbz.org.cn/Home/Standard',
             params_func=lambda kw, pg: {'key': kw, 'page': pg, 'size': TTBZ_PAGE_SIZE},
             rows_key=lambda d: d.get('data') or d.get('list') or d.get('rows') or [],
             code_keys=('stdCode', 'StdCode', 'code'),
             name_keys=('stdName', 'StdName', 'name'),
             org_keys=('orgName', 'OrgName')),
    ]

    headers_post = {
        'Referer':      'https://www.ttbz.org.cn/',
        'Origin':       'https://www.ttbz.org.cn',
        'Content-Type': 'application/json',
        'Accept':       'application/json, */*',
    }
    headers_get = {'Referer': 'https://www.ttbz.org.cn/'}

    def _try_api(api_cfg, page):
        try:
            if api_cfg['method'] == 'POST':
                resp = SESSION.post(
                    api_cfg['url'],
                    json=api_cfg['json_body'](keyword, page),
                    headers=headers_post, timeout=20)
            else:
                resp = SESSION.get(
                    api_cfg['url'],
                    params=api_cfg['params_func'](keyword, page),
                    headers=headers_get, timeout=20)
            if not resp.ok: return None
            if 'json' not in resp.headers.get('content-type', ''): return None
            data = resp.json()
            rows = api_cfg['rows_key'](data)
            return rows if isinstance(rows, list) else None
        except Exception as e:
            if DEBUG_MODE: log(f"    [DEBUG] ttbz({api_cfg['url']}) p{page}异常: {e}")
            return None

    working_api = None
    for api in _TTBZ_APIS:
        rows = _try_api(api, 1)
        if rows is not None:   # None=失败，[]=成功但无结果
            working_api = api
            _process_ttbz_rows(rows, api, seen, all_results)
            if len(rows) < TTBZ_PAGE_SIZE: break
            # 继续翻页
            for page in range(2, 6):
                time.sleep(SLEEP_PAGE)
                rows = _try_api(api, page)
                if not rows: break
                _process_ttbz_rows(rows, api, seen, all_results)
                if len(rows) < TTBZ_PAGE_SIZE: break
            break  # 已有可用接口，不继续尝试下一个
        time.sleep(0.3)

    if DEBUG_MODE:
        api_url = working_api['url'] if working_api else '无可用接口'
        log(f"    [DEBUG] ttbz「{keyword}」: {len(all_results)}条 via {api_url}")
    return all_results

def _process_ttbz_rows(rows, api, seen, results):
    for row in rows:
        code = next((row.get(k,'') for k in api['code_keys'] if row.get(k)), '').strip()
        name = next((row.get(k,'') for k in api['name_keys'] if row.get(k)), '').strip()
        if not code or not name or code in seen: continue
        seen.add(code)
        org  = next((row.get(k,'') for k in api['org_keys'] if row.get(k)), '').strip()
        results.append({
            'code':          code,
            'title':         name,
            'type':          '团标',
            'status':        norm_status(row.get('Status') or row.get('status') or '现行'),
            'issueDate':     norm_date(row.get('IssueDate') or row.get('issueDate')),
            'implementDate': norm_date(row.get('ImplementDate') or row.get('implementDate')),
            'issuedBy':      org,
            'isMandatory':   False,
            'source':        'ttbz',
        })

# ============================================================
#  来源三：dbba.sacinfo.org.cn 地方标准信息服务平台
#  v10：多路径 fallback + 分页
# ============================================================
def fetch_dbba(keyword):
    """
    地方标准平台，多接口路径 fallback。
    """
    all_results, seen = [], set()

    _DBBA_APIS = [
        # 路径A：GET list（原有）
        dict(method='GET',
             url='https://dbba.sacinfo.org.cn/api/standard/list',
             params=lambda kw, pg: {"searchText": kw, "pageSize": DBBA_PAGE_SIZE, "pageNum": pg},
             extract=lambda d: (d.get('data') or {}).get('list') or []),
        # 路径B：POST JSON
        dict(method='POST',
             url='https://dbba.sacinfo.org.cn/api/standard/query',
             params=lambda kw, pg: {"keyword": kw, "pageSize": DBBA_PAGE_SIZE, "pageNum": pg},
             extract=lambda d: (d.get('data') or {}).get('list') or d.get('list') or []),
        # 路径C：sacinfo 聚合查询（通用接口）
        dict(method='GET',
             url='https://std.sacinfo.org.cn/gnStd/getGnStdList',
             params=lambda kw, pg: {"keyword": kw, "pageNo": pg, "pageSize": DBBA_PAGE_SIZE,
                                     "stdType": "DB"},
             extract=lambda d: d.get('rows') or d.get('data') or []),
    ]

    headers = {'Referer': 'https://dbba.sacinfo.org.cn/'}

    def _try_dbba(api_cfg, page):
        try:
            p = api_cfg['params'](keyword, page)
            if api_cfg['method'] == 'GET':
                resp = SESSION.get(api_cfg['url'], params=p, headers=headers, timeout=20)
            else:
                resp = SESSION.post(api_cfg['url'], json=p, headers={
                    **headers, 'Content-Type': 'application/json'}, timeout=20)
            if not resp.ok: return None
            if 'json' not in resp.headers.get('content-type', ''): return None
            data  = resp.json()
            items = api_cfg['extract'](data)
            return items if isinstance(items, list) else None
        except Exception as e:
            if DEBUG_MODE: log(f"    [DEBUG] dbba({api_cfg['url']}) p{page}异常: {e}")
            return None

    working_api = None
    for api in _DBBA_APIS:
        items = _try_dbba(api, 1)
        if items is not None:
            working_api = api
            _process_dbba_items(items, seen, all_results)
            if len(items) < DBBA_PAGE_SIZE: break
            for page in range(2, 6):
                time.sleep(SLEEP_PAGE)
                items = _try_dbba(api, page)
                if not items: break
                _process_dbba_items(items, seen, all_results)
                if len(items) < DBBA_PAGE_SIZE: break
            break
        time.sleep(0.3)

    if DEBUG_MODE:
        api_url = working_api['url'] if working_api else '无可用接口'
        log(f"    [DEBUG] dbba「{keyword}」: {len(all_results)}条 via {api_url}")
    return all_results

def _process_dbba_items(items, seen, results):
    for item in items:
        code  = (item.get('stdCode') or item.get('STD_CODE') or item.get('code') or '').strip()
        title = (item.get('stdName') or item.get('STD_NAME') or item.get('name') or '').strip()
        if not code or not title or code in seen: continue
        seen.add(code)
        results.append({
            'code':          code,
            'title':         title,
            'type':          '地方标准',
            'status':        norm_status(item.get('status') or item.get('STATE') or ''),
            'issueDate':     norm_date(item.get('publishDate') or item.get('ISSUE_DATE')),
            'implementDate': norm_date(item.get('implementDate') or item.get('ACT_DATE')),
            'issuedBy':      (item.get('publishDept') or item.get('ISSUE_DEPT') or '').strip(),
            'isMandatory':   False,
            'source':        'dbba',
        })

# ============================================================
#  来源四：openstd.samr.gov.cn 国家标准全文公开系统
# ============================================================
def fetch_openstd(keyword):
    all_results, seen = [], set()

    for page in range(1, 6):
        try:
            resp = SESSION.post(
                "https://openstd.samr.gov.cn/bzgk/gb/searchGB",
                json={
                    "searchText": keyword,
                    "status":     "",
                    "sortField":  "ISSUE_DATE",
                    "sortType":   "desc",
                    "pageSize":   50,
                    "pageIndex":  page,
                },
                headers={
                    'Referer':      'https://openstd.samr.gov.cn/bzgk/gb/',
                    'Origin':       'https://openstd.samr.gov.cn',
                    'Content-Type': 'application/json',
                    'Accept':       'application/json, text/plain, */*',
                },
                timeout=25
            )
            if not resp.ok: break
            if 'json' not in resp.headers.get('content-type', ''): break
            data = resp.json()
            rows = data.get('rows') or data.get('data') or data.get('list') or []
            if not rows: break
            for row in rows:
                code  = clean_samr_code(
                    row.get('C_STD_CODE') or row.get('STD_CODE') or row.get('stdCode') or ''
                ).strip()
                title = clean_sacinfo(
                    row.get('C_C_NAME') or row.get('STD_NAME') or row.get('stdName') or ''
                ).strip()
                if not code or not title or code in seen: continue
                # 此处不过滤，由外层 filter_by_title 处理
                seen.add(code)
                issue_date = norm_date(row.get('ISSUE_DATE') or row.get('issueDate'))
                issued_by  = (row.get('ISSUE_DEPT') or row.get('C_ISSUE_DEPT') or '').strip()
                if not issued_by:
                    issued_by = infer_issued_by(code, issue_date)
                all_results.append({
                    'code':          code,
                    'title':         title,
                    'status':        norm_status(row.get('STATE') or row.get('STD_STATUS') or ''),
                    'issueDate':     issue_date,
                    'implementDate': norm_date(row.get('ACT_DATE') or row.get('IMPL_DATE')),
                    'abolishDate':   norm_date(row.get('ABOL_DATE')),
                    'issuedBy':      issued_by,
                    'isMandatory':   is_mandatory(code),
                    'source':        'openstd',
                })
            if len(rows) < 50: break
        except Exception as e:
            if DEBUG_MODE: log(f"    [DEBUG] openstd p{page}异常: {e}")
            break
        time.sleep(SLEEP_PAGE)

    return all_results

# ============================================================
#  来源五：cssn.net.cn 中国标准服务网
# ============================================================
def fetch_cssn(keyword):
    all_results, seen = [], set()

    endpoints = [
        ('POST', 'https://cssn.net.cn/cssn/searchStd',
         {'keyword': keyword, 'pageIndex': 1, 'pageSize': 50}),
        ('POST', 'https://cssn.net.cn/cssn/standard/search',
         {'keyword': keyword, 'page': 1, 'size': 50}),
        ('GET',  'https://cssn.net.cn/cssn/standard/list',
         {'keyword': keyword, 'pageNo': 1, 'pageSize': 50}),
        ('GET',  'https://cssn.net.cn/cssn/search',
         {'q': keyword, 'pageSize': 50, 'pageNum': 1}),
    ]
    headers_post = {
        'Referer':      'https://cssn.net.cn/cssn/index',
        'Origin':       'https://cssn.net.cn',
        'Content-Type': 'application/json',
        'Accept':       'application/json, */*',
    }
    headers_get = {'Referer': 'https://cssn.net.cn/cssn/index'}

    for method, url, params in endpoints:
        try:
            if method == 'POST':
                resp = SESSION.post(url, json=params, headers=headers_post, timeout=20)
            else:
                resp = SESSION.get(url, params=params, headers=headers_get, timeout=20)
            if not resp.ok: continue
            if 'json' not in resp.headers.get('content-type', ''): continue
            data = resp.json()
            rows = data.get('data') or data.get('rows') or data.get('list') or data.get('result') or []
            if isinstance(rows, dict):
                rows = rows.get('list') or rows.get('rows') or []
            if not rows: continue
            for row in rows:
                code  = (row.get('stdCode') or row.get('STD_CODE') or row.get('code') or '').strip()
                title = (row.get('stdName') or row.get('STD_NAME') or row.get('name') or '').strip()
                if not code or not title or code in seen: continue
                seen.add(code)
                issue_date = norm_date(row.get('issueDate') or row.get('ISSUE_DATE') or row.get('publishDate'))
                issued_by  = (row.get('issuedBy') or row.get('ISSUE_DEPT') or row.get('publishDept') or '').strip()
                if not issued_by:
                    issued_by = infer_issued_by(code, issue_date)
                all_results.append({
                    'code':          code,
                    'title':         title,
                    'type':          guess_type(code),
                    'status':        norm_status(row.get('status') or row.get('STATE') or ''),
                    'issueDate':     issue_date,
                    'implementDate': norm_date(row.get('implementDate') or row.get('ACT_DATE')),
                    'issuedBy':      issued_by,
                    'isMandatory':   is_mandatory(code),
                    'source':        'cssn',
                })
            if all_results: break
        except Exception as e:
            if DEBUG_MODE: log(f"    [DEBUG] cssn({url})异常: {e}")
        time.sleep(0.3)

    if DEBUG_MODE:
        log(f"    [DEBUG] cssn「{keyword}」: {len(all_results)}条")
    return all_results

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
                json={"model": "deepseek-chat",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 200, "temperature": 0.3},
                headers={'Authorization': f'Bearer {DEEPSEEK_KEY}',
                         'Content-Type': 'application/json'},
                timeout=30)
            if resp.ok:
                return resp.json().get('choices', [{}])[0].get('message', {}).get('content', '').strip()
        else:
            resp = SESSION.post(
                'https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation',
                json={"model": "qwen-turbo",
                      "input": {"messages": [{"role": "user", "content": prompt}]},
                      "parameters": {"max_tokens": 200}},
                headers={'Authorization': f'Bearer {QWEN_KEY}',
                         'Content-Type': 'application/json'},
                timeout=30)
            if resp.ok:
                return resp.json().get('output', {}).get('text', '').strip()
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
        if not force and std.get('summary', '').strip(): continue
        s = ai_enrich_standard(std)
        if s and len(s) > 10:
            standards[i]['summary'] = s
            enriched += 1
            log(f"  ✅ [{std['code']}] {s[:40]}…")
        time.sleep(0.5)
    log(f"  完成：补全/更新 {enriched} 条摘要")
    return standards

# ============================================================
#  核查状态
# ============================================================
def check_status_online(std):
    code = std.get('code', '')
    if not code: return None
    try:
        raw_rows, _ = fetch_samr_one_page(code, 1)
        for row in raw_rows:
            r = _parse_samr_row(row)
            if r and norm_code(r['code']) == norm_code(code):
                ns = r['status']
                if ns and ns != std.get('status'):
                    upd = dict(std)
                    upd['status'] = ns
                    if ns == '废止':
                        upd['abolishDate'] = r.get('abolishDate') or datetime.now().strftime('%Y-%m-%d')
                    return upd
    except Exception:
        pass
    return None

# ============================================================
#  合并
# ============================================================
def merge(existing, new_items):
    idx = {norm_code(s['code']): i for i, s in enumerate(existing)}
    added = updated_n = 0
    for item in new_items:
        cn = norm_code(item.get('code', ''))
        if not cn: continue
        if cn in idx:
            orig, changed = existing[idx[cn]], False
            for f in ('status', 'abolishDate', 'implementDate', 'issueDate'):
                nv = item.get(f)
                if nv and nv != orig.get(f):
                    orig[f] = nv; changed = True
            nv_issued = item.get('issuedBy', '').strip()
            if nv_issued and len(nv_issued) > len(orig.get('issuedBy', '') or ''):
                orig['issuedBy'] = nv_issued; changed = True
            for f in ('replaces', 'replacedBy'):
                nv = item.get(f)
                if nv and not orig.get(f):
                    orig[f] = nv; changed = True
            if changed: updated_n += 1
        else:
            existing.append(build_entry(item))
            idx[cn] = len(existing) - 1
            added += 1
    return existing, added, updated_n

# ============================================================
#  DB 读写
# ============================================================
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
               'version': today.replace('-', '.'), 'total': len(standards)})
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
    log("=" * 65)
    log("体育标准数据库 — 自动抓取更新 v10")
    log(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"平台: samr / ttbz / dbba / openstd / cssn  (共5个)")
    log(f"收录规则: 标准名称含关键词即收录（纯标题匹配，不看正文）")
    log(f"早停阈值: 连续{EARLY_STOP_PAGES}页无标题匹配则停止翻页")
    log(f"AI摘要: {'DeepSeek' if DEEPSEEK_KEY else '通义千问' if QWEN_KEY else '未配置'}")
    log("=" * 65)

    db, standards = load_db()

    # ── 仅对旧库做一次非体育清理（不影响新抓取逻辑）──
    before = len(standards)
    standards = [s for s in standards if is_sports_for_cleanup(clean_sacinfo(s.get('title', '')))]
    removed = before - len(standards)
    if removed > 0:
        log(f"\n🗑️  清理旧库非体育标准：移除 {removed} 条，剩余 {len(standards)} 条")

    # ── 清洗 sacinfo 标签 ──
    for i, std in enumerate(standards):
        if std.get('title') and '<sacinfo>' in std['title']:
            standards[i]['title'] = clean_sacinfo(std['title'])

    # ── 仅核查模式 ──
    if check_only:
        log(f"\n🔍 核查现有 {len(standards)} 条标准状态…")
        changed = 0
        for std in list(standards):
            upd = check_status_online(std)
            if upd:
                j = next((k for k, s in enumerate(standards) if s['code'] == std['code']), None)
                if j is not None:
                    standards[j] = upd; changed += 1
                    log(f"  🔄 {std['code']}: {std.get('status')} → {upd['status']}")
            time.sleep(0.4)
        log(f"  状态变更: {changed} 条")
        save_db(db, standards, dry_run)
        return

    # ── 多源抓取 ──
    log(f"\n🌐 开始抓取（{len(KEYWORDS)} 个关键词 × 5 个平台）…")
    all_new    = []
    global_seen = set()   # 跨关键词去重（同一标准不重复入库）
    total_kw   = len(KEYWORDS)
    t_start    = time.time()

    for i, kw in enumerate(KEYWORDS, 1):
        log(f"\n  [{i:02d}/{total_kw}] 「{kw}」")

        # ① samr（早停分页，最多 MAX_PAGES 页）
        a = fetch_samr_all(kw)
        # ★ samr 已在内部按 title_has_keyword 过滤，无需再 filter_by_title

        # ② 团标 ttbz
        b_raw = fetch_ttbz(kw)
        b = filter_by_title(b_raw, kw)

        # ③ 地标 dbba
        c_raw = fetch_dbba(kw)
        c = filter_by_title(c_raw, kw)

        # ④ openstd（全文公开，同样全文搜索→需过滤）
        d_raw = fetch_openstd(kw)
        d = filter_by_title(d_raw, kw)

        # ⑤ cssn
        e_raw = fetch_cssn(kw)
        e = filter_by_title(e_raw, kw)

        # 合并，跨关键词去重
        batch = []
        for item in (a + b + c + d + e):
            nc = norm_code(item.get('code', ''))
            if nc and nc not in global_seen:
                global_seen.add(nc)
                batch.append(item)

        elapsed = int(time.time() - t_start)
        log(f"         国标:{len(a)}  团标:{len(b)}  地标:{len(c)}"
            f"  全文公开:{len(d)}  cssn:{len(e)}  净新增:{len(batch)}"
            f"  已用时:{elapsed}s")

        all_new.extend(batch)
        time.sleep(SLEEP_KW)

    # ── 合并去重 ──
    log(f"\n🔀 合并（本次 {len(all_new)} 条 → 与现有 {len(standards)} 条合并）…")
    standards, added, updated_n = merge(standards, all_new)
    log(f"  新增 {added} | 更新 {updated_n} | 最终 {len(standards)}")

    if added == 0 and len(standards) == 0:
        log("\n  ⚠️  未抓取到任何标准，建议运行 --debug 查看响应内容")

    # ── 自动补全：发布机构 ──
    log("\n🔧 补全：发布机构…")
    filled = 0
    for s in standards:
        if not s.get('issuedBy'):
            val = infer_issued_by(s.get('code', ''), s.get('issueDate'))
            if val: s['issuedBy'] = val; filled += 1
        elif '、' not in s.get('issuedBy', ''):
            inf = infer_issued_by(s.get('code', ''), s.get('issueDate'))
            if '、' in inf and s['issuedBy'] in inf:
                s['issuedBy'] = inf; filled += 1
    log(f"  补填 {filled} 条")

    # ── 自动补全：版本替代关系 ──
    log("\n🔧 补全：版本替代关系…")
    log(f"  发现 {auto_fill_replaces(standards)} 条")

    # ── AI摘要 ──
    has_key = bool(QWEN_KEY or DEEPSEEK_KEY)
    if use_ai and not has_key:
        log("\n⚠️  --ai 需先在 scripts/.env 配置 QWEN_KEY 或 DEEPSEEK_KEY")
    elif has_key or use_ai:
        missing = sum(1 for s in standards if not s.get('summary', '').strip())
        log(f"\n🤖 AI摘要（缺 {missing} 条）…")
        standards = ai_enrich_batch(standards, force=use_ai)
    else:
        missing = sum(1 for s in standards if not s.get('summary', '').strip())
        if missing:
            log(f"\n💡 {missing} 条缺摘要（配置 QWEN_KEY/DEEPSEEK_KEY 后运行 --ai 补全）")

    save_db(db, standards, dry_run)

    # ── 报告 ──
    active = sum(1 for s in standards if s.get('status') == '现行')
    abol   = sum(1 for s in standards if s.get('status') == '废止')
    coming = sum(1 for s in standards if s.get('status') == '即将实施')
    miss_issued   = sum(1 for s in standards if not s.get('issuedBy'))
    miss_summary  = sum(1 for s in standards if not s.get('summary', '').strip())
    miss_replaces = sum(1 for s in standards if s.get('status') == '废止' and not s.get('replacedBy'))

    src_counts = {}
    for s in standards:
        k = s.get('source', 'unknown')
        src_counts[k] = src_counts.get(k, 0) + 1

    log(f"\n{'='*65}")
    log(f"📊 总 {len(standards)} 条 | 现行 {active} | 废止 {abol} | 即将实施 {coming}")
    log(f"📊 来源: " + "  ".join(f"{k}:{v}" for k, v in sorted(src_counts.items())))
    log(f"📋 缺发布机构: {miss_issued}  缺摘要: {miss_summary}  废止缺替代: {miss_replaces}")
    total_elapsed = int(time.time() - t_start)
    log(f"⏱  总耗时: {total_elapsed//60}分{total_elapsed%60}秒")

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--dry',   action='store_true', help='预览，不写入')
    p.add_argument('--check', action='store_true', help='仅核查状态')
    p.add_argument('--ai',    action='store_true', help='强制重新生成所有AI摘要')
    p.add_argument('--debug', action='store_true', help='调试模式（显示详细日志）')
    args = p.parse_args()
    DEBUG_MODE = args.debug
    run(dry_run=args.dry, check_only=args.check, use_ai=args.ai)
