#!/usr/bin/env python3
"""
体育建设标准自动抓取 & 更新脚本 v18.1
======================================
来源A：samr GET 关键词搜索 → 国家标准（已验证有效）
来源B：已知重要标准编号精确查询 → 行标/地标/团标
       samr 对部分行标（JGJ/CJJ）有效，查到则写入真实数据
       查不到（地标/团标）则跳过，并提示手动录入

admin.html 后台手动补录不可自动抓取的标准。

运行方式：
  python scripts/update_standards.py            # 完整抓取
  python scripts/update_standards.py --test     # 诊断模式
  python scripts/update_standards.py --check    # 仅核查状态
  python scripts/update_standards.py --ai       # 启用AI补全摘要
  python scripts/update_standards.py --debug    # 调试模式
  python scripts/update_standards.py --dry      # 预览不写入
"""

import json, time, re, argparse, hashlib, os
from datetime import datetime
from pathlib import Path

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
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
#  来源B：已知重要标准编号（仅编号，标题/状态全部在线获取）
#  samr 对 JGJ/CJJ/JG/T 行业标准有效，对 DB/T/ 前缀通常无效。
#  查不到的不写入，运行结束会列出清单供手动录入。
# ============================================================
KNOWN_CODES = [
    # ── 行业标准：合成材料面层/跑道 ──
    "JG/T 477-2015",       # 运动场地合成材料面层
    "JG/T 553-2019",       # 运动场地合成材料面层质量控制

    # ── 行业标准：人造草坪 ──
    "JG/T 388-2012",       # 运动场人造草
    "JG/T 578-2022",       # 运动场地人造草坪系统施工规范

    # ── 行业标准：灯光照明 ──
    "JGJ 153-2016",        # 体育场馆照明设计及检测标准（强制）
    "JGJ/T 119-2008",      # 建筑照明术语标准

    # ── 行业标准：木地板 ──
    "JG/T 354-2012",       # 体育用木质地板
    "JG/T 563-2019",       # 运动木地板系统

    # ── 行业标准：弹性地板/PVC地板 ──
    "JG/T 449-2014",       # 弹性地板（运动场地用）

    # ── 行业标准：体育建筑设计 ──
    "JGJ 31-2003",         # 体育建筑设计规范（强制）
    "JGJ/T 191-2009",      # 固定式运动护板技术规程
    "JGJ/T 179-2009",      # 体育建筑电气设计规范

    # ── 行业标准：游泳池 ──
    "CJJ 122-2017",        # 游泳池给水排水工程技术规程
    "CJJ/T 244-2016",      # 游泳池水质标准

    # ── 行业标准：健身路径 ──
    "JG/T 341-2011",       # 健身路径设施技术要求
    "JG/T 500-2016",       # 攀爬类健身器材

    # ── 团体标准（samr查不到，提示手动录入）──
    "T/SGTAS 001-2019",    # 合成材料运动场地面层施工与验收规范
    "T/SGTAS 002-2019",    # 人造草坪运动场地系统施工与验收规范
    "T/SGTAS 005-2020",    # 运动场地LED照明系统技术规范
    "T/CECS 786-2021",     # 运动场地合成面层应用技术规程
    "T/CECS 867-2021",     # 运动场地人造草坪系统应用技术规程
    "T/CSUS 19-2021",      # 城市体育公园规划设计标准
    "T/CAECS 001-2020",    # 学校操场合成面层工程技术规程

    # ── 地方标准（samr查不到，提示手动录入）──
    "DB11/T 1827-2021",    # 北京：学校操场合成材料面层有害物质限量
    "DB31/T 1150-2019",    # 上海：中小学运动场地合成材料面层技术要求
    "DB44/T 2321-2021",    # 广东：学校运动场地合成材料面层技术规范
    "DB33/T 2204-2019",    # 浙江：中小学校合成材料面层运动场地建设规范
    "DB37/T 3692-2019",    # 山东：中小学运动场地合成材料面层技术规范
    "DB51/T 2748-2021",    # 四川：中小学合成材料运动场地建设技术规程
    "DB32/T 3953-2021",    # 江苏：学校运动场地合成材料面层技术规程
    "DB11/T 1223-2015",    # 北京：室外健身器材配置与管理规范
]

# ============================================================
#  体育标准过滤词组
# ============================================================
SPORTS_TERMS = [
    "合成材料面层","合成材料跑道","塑胶跑道","聚氨酯跑道","橡胶面层",
    "人造草坪","人造草皮","人工草坪","运动场人造草",
    "颗粒填充料","草坪填充","橡胶颗粒",
    "体育场馆照明","体育照明","运动场照明","体育场地照明","体育建筑电气",
    "体育木地板","运动木地板","体育用木质地板","体育馆木地板",
    "运动地胶","PVC运动地板","体育地板","运动地板","弹性运动地板",
    "卷材运动地板","弹性地板",
    "体育围网","运动场围网","球场围网",
    "室外健身器材","健身路径","公共健身器材","户外健身器材","健身步道",
    "体育器材","学校体育器材","健身器材","攀爬","护板",
    "体育场地","运动场地","体育场馆","体育建筑",
    "足球场地","篮球场地","网球场地","田径场地",
    "游泳场地","游泳馆","游泳池",
    "排球场地","羽毛球场地","乒乓球场地",
    "学校操场","体育公园","全民健身","体育设施",
    "健身活动中心","体育场",
]

def is_sports(title):
    if not title: return False
    return any(term in title for term in SPORTS_TERMS)

# 来源A：关键词搜索列表
KEYWORDS = [
    "合成材料面层", "塑胶跑道", "合成材料跑道", "聚氨酯跑道", "中小学合成材料",
    "人造草坪", "人造草皮", "运动场人造草", "人工草坪",
    "颗粒填充料", "草坪填充橡胶",
    "体育场馆照明", "体育照明", "运动场照明", "体育建筑电气",
    "体育木地板", "运动木地板", "体育用木质地板",
    "运动地胶", "运动地板", "弹性地板", "卷材运动地板",
    "体育围网", "运动场围网", "球场围网",
    "室外健身器材", "健身路径", "公共健身器材", "健身步道",
    "体育器材", "学校体育器材",
    "足球场地", "篮球场地", "网球场地", "田径场地",
    "排球场地", "羽毛球场地", "乒乓球场地",
    "游泳场地", "游泳馆", "游泳池",
    "体育场地", "运动场地", "体育场馆", "体育建筑",
    "体育公园", "全民健身", "学校操场", "体育设施",
]

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
      'AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/122.0.0.0 Safari/537.36')

def make_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=[429,500,502,503,504])
    s.mount('https://', HTTPAdapter(max_retries=retry))
    s.mount('http://',  HTTPAdapter(max_retries=retry))
    s.headers.update({
        'User-Agent':      UA,
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
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

def clean_sacinfo(raw):
    if not raw: return ''
    return re.sub(r'</?sacinfo>', '', raw).strip()

def clean_code(raw):
    if not raw: return ''
    parts = re.findall(r'<sacinfo>(.*?)</sacinfo>', raw)
    if parts:
        prefix = ''.join(parts[:-1]).strip()
        number = parts[-1].strip()
        slash_map = {
            'GBT':'GB/T','GBZ':'GB/Z','JGT':'JG/T','GAT':'GA/T',
            'JGJ':'JGJ','JGJT':'JGJ/T','CJJ':'CJJ','CJJT':'CJJ/T',
        }
        prefix = slash_map.get(prefix, prefix)
        return f"{prefix} {number}".strip() if prefix else number
    return re.sub(r'<[^>]+>', '', raw).strip()

def norm_status(raw):
    raw = str(raw or '').strip()
    if any(x in raw for x in ['现行','有效','执行','施行']): return '现行'
    if any(x in raw for x in ['废止','作废','撤销','废弃']):  return '废止'
    if any(x in raw for x in ['即将','待实施','未实施']):     return '即将实施'
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
    if c.startswith('JGJ') and '/T' not in c: return True
    return False

def guess_type(code):
    cu = norm_code(code)
    for prefix, t in [
        ("GB/T","国家标准"),("GB","国家标准"),
        ("JGJ","行业标准"),("JG/T","行业标准"),
        ("CJJ","行业标准"),("CJJ/T","行业标准"),
        ("T/","团标"),("DB","地方标准"),
    ]:
        if cu.startswith(norm_code(prefix)): return t
    return "国家标准"

def guess_category(text):
    cm = {
        "合成材料":"合成材料面层","塑胶跑道":"合成材料面层",
        "人造草":"人造草坪","草坪":"人造草坪",
        "照明":"灯光照明","灯光":"灯光照明","木地板":"木地板",
        "地胶":"PVC运动地胶","弹性地板":"PVC运动地胶","运动地板":"PVC运动地胶",
        "围网":"围网","健身器材":"健身路径","健身路径":"健身路径","健身步道":"健身路径",
        "攀爬":"健身路径","护板":"综合",
        "体育器材":"体育器材","颗粒填充":"颗粒填充料","橡胶颗粒":"颗粒填充料",
        "游泳":"游泳场地","体育建筑":"场地设计","体育公园":"场地设计",
    }
    for kw, cat in cm.items():
        if kw in text: return cat
    return "综合"

def guess_tags(text):
    return [t for t in [
        "体育","运动","塑胶","合成材料","人造草","照明","木地板","围网",
        "健身","颗粒","游泳","篮球","足球","网球","田径","排球","跑道","场地","学校",
    ] if t in text][:6]

def build_entry(item):
    code  = item.get('code', '')
    title = clean_sacinfo(item.get('title', ''))
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
        'replaces':      None,
        'replacedBy':    None,
        'issuedBy':      item.get('issuedBy', ''),
        'category':      item.get('category') or guess_category(title),
        'tags':          item.get('tags') or guess_tags(title),
        'summary':       item.get('summary') or '',
        'isMandatory':   item.get('isMandatory', is_mandatory(code)),
        'scope':         '',
        'localFile':     item.get('localFile') or None,
    }

# ============================================================
#  samr GET 搜索（v15 已验证）
# ============================================================
SAMR_URL = 'https://std.samr.gov.cn/gb/search/gbQueryPage'
SAMR_HDR = {
    'Referer':           'https://std.samr.gov.cn/gb/search',
    'Accept':            'application/json, text/plain, */*',
    'X-Requested-With':  'XMLHttpRequest',
}

def samr_get(keyword, page=1):
    try:
        resp = SESSION.get(SAMR_URL,
            params={'searchText': keyword, 'pageIndex': page,
                    'pageSize': 10, 'status': '',
                    'sortField': 'ISSUE_DATE', 'sortType': 'desc'},
            headers=SAMR_HDR, timeout=20)
        ct = resp.headers.get('content-type', '')
        if not resp.ok or 'html' in ct.lower():
            return [], 1
        data  = resp.json()
        rows  = data.get('rows') or []
        total = int(data.get('total') or 0)
        return rows, max(1, -(-total // 10)) if total else 1
    except Exception as e:
        if DEBUG_MODE: log(f"    ❌ samr [{keyword}]: {e}")
        return [], 1

def parse_rows(rows, force_type=None):
    results = []
    for row in rows:
        code  = clean_code(row.get('C_STD_CODE') or row.get('STD_CODE') or '').strip()
        title = clean_sacinfo(row.get('C_C_NAME') or row.get('STD_NAME') or '').strip()
        if not code or not title or not is_sports(title): continue
        results.append({
            'code':          code,
            'title':         title,
            'type':          force_type or guess_type(code),
            'status':        norm_status(row.get('STATE') or row.get('STD_STATUS') or ''),
            'issueDate':     norm_date(row.get('ISSUE_DATE')),
            'implementDate': norm_date(row.get('ACT_DATE') or row.get('IMPL_DATE')),
            'abolishDate':   norm_date(row.get('ABOL_DATE')),
            'issuedBy':      (row.get('ISSUE_DEPT') or row.get('C_ISSUE_DEPT') or '').strip(),
            'isMandatory':   is_mandatory(code) or '强制' in (row.get('STD_NATURE') or ''),
        })
    return results

# ============================================================
#  来源A：关键词搜索
# ============================================================
def fetch_by_keywords():
    log(f"\n🏛  来源A：关键词搜索（{len(KEYWORDS)} 个）…")
    results, seen = [], set()
    for i, kw in enumerate(KEYWORDS, 1):
        rows, total_pages = samr_get(kw, 1)
        found = parse_rows(rows)
        new   = [r for r in found if r['code'] not in seen]
        for r in new: seen.add(r['code'])
        results.extend(new)
        if total_pages > 1:
            for p in range(2, min(total_pages+1, 51)):
                time.sleep(0.4)
                rows2, _ = samr_get(kw, p)
                new2 = [r for r in parse_rows(rows2) if r['code'] not in seen]
                for r in new2: seen.add(r['code'])
                results.extend(new2)
        if new:
            log(f"  [{i:02d}/{len(KEYWORDS)}] 「{kw}」→ {len(new)} 条（累计 {len(results)}）")
        time.sleep(0.5)
    log(f"  来源A合计: {len(results)} 条")
    return results

# ============================================================
#  来源B：已知编号精确查询
# ============================================================
def fetch_by_known_codes():
    log(f"\n📋  来源B：已知编号精确查询（{len(KNOWN_CODES)} 个）…")
    results, seen = [], set()
    not_found_hb, not_found_db, not_found_tt = [], [], []

    for i, code in enumerate(KNOWN_CODES, 1):
        rows, _ = samr_get(code, 1)
        matched = None
        for row in rows:
            rc = clean_code(row.get('C_STD_CODE') or row.get('STD_CODE') or '').strip()
            if norm_code(rc) == norm_code(code):
                title = clean_sacinfo(row.get('C_C_NAME') or row.get('STD_NAME') or '').strip()
                if not title: continue
                matched = {
                    'code':          rc or code,
                    'title':         title,
                    'type':          guess_type(code),
                    'status':        norm_status(row.get('STATE') or ''),
                    'issueDate':     norm_date(row.get('ISSUE_DATE')),
                    'implementDate': norm_date(row.get('ACT_DATE') or row.get('IMPL_DATE')),
                    'abolishDate':   norm_date(row.get('ABOL_DATE')),
                    'issuedBy':      (row.get('ISSUE_DEPT') or row.get('C_ISSUE_DEPT') or '').strip(),
                    'isMandatory':   is_mandatory(code) or '强制' in (row.get('STD_NATURE') or ''),
                }
                break

        if matched and norm_code(matched['code']) not in seen:
            seen.add(norm_code(matched['code']))
            results.append(matched)
            log(f"  [{i:02d}/{len(KNOWN_CODES)}] ✅ [{matched['code']}] {matched['title'][:38]} | {matched['status']}")
        else:
            t = guess_type(code)
            if t == '行业标准':   not_found_hb.append(code)
            elif t == '地方标准': not_found_db.append(code)
            else:                 not_found_tt.append(code)
            log(f"  [{i:02d}/{len(KNOWN_CODES)}] ⚠️  {code}（{t}，samr未收录）")
        time.sleep(0.5)

    log(f"\n  来源B合计: {len(results)} 条查到")
    total_missing = len(not_found_hb) + len(not_found_db) + len(not_found_tt)
    if total_missing:
        log(f"\n  ─── 以下 {total_missing} 条需在 admin.html 手动录入 ───")
        if not_found_hb:
            log(f"  行业标准（{len(not_found_hb)} 条）：")
            for c in not_found_hb: log(f"    • {c}")
        if not_found_db:
            log(f"  地方标准（{len(not_found_db)} 条）：")
            for c in not_found_db: log(f"    • {c}")
        if not_found_tt:
            log(f"  团体标准（{len(not_found_tt)} 条）：")
            for c in not_found_tt: log(f"    • {c}")
    return results

# ============================================================
#  --test 诊断
# ============================================================
def run_test():
    log("=" * 60)
    log("🔬 诊断模式 v18.1")
    log("=" * 60)
    log("\n─── 1. 关键词搜索（4个样本）───")
    for kw in ["合成材料面层", "体育场地", "室外健身器材", "体育场馆照明"]:
        rows, tp = samr_get(kw, 1)
        found = parse_rows(rows)
        log(f"  「{kw}」→ {len(found)} 条体育标准，共{tp}页")
        for r in found[:2]: log(f"    ✅ [{r['code']}] {r['title'][:40]}")
    log("\n─── 2. 已知编号精确查询（行标6个）───")
    for code in [c for c in KNOWN_CODES if guess_type(c)=='行业标准'][:6]:
        rows, _ = samr_get(code, 1)
        matched = next((r for r in rows
                        if norm_code(clean_code(r.get('C_STD_CODE') or '')) == norm_code(code)), None)
        if matched:
            t = clean_sacinfo(matched.get('C_C_NAME') or '')
            log(f"  ✅ {code} → {t[:40]}")
        else:
            log(f"  ⚠️  {code} → samr未收录")
        time.sleep(0.4)
    log("\n" + "=" * 60)
    log("🔬 诊断完成")
    log("=" * 60)

# ============================================================
#  AI摘要
# ============================================================
def ai_enrich_standard(std):
    provider = 'qwen' if QWEN_KEY else ('deepseek' if DEEPSEEK_KEY else None)
    if not provider: return None
    prompt = (f"你是中国标准化专家。用2-3句话描述该标准主要内容和适用范围，只返回描述。\n"
              f"编号：{std.get('code','')}  名称：{std.get('title','')}")
    try:
        if provider == 'qwen':
            resp = SESSION.post(
                'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
                json={"model":"qwen-turbo","messages":[{"role":"user","content":prompt}],"max_tokens":200},
                headers={'Authorization':f'Bearer {QWEN_KEY}','Content-Type':'application/json'}, timeout=30)
            if resp.ok:
                return resp.json().get('choices',[{}])[0].get('message',{}).get('content','').strip()
        else:
            resp = SESSION.post(
                'https://api.deepseek.com/chat/completions',
                json={"model":"deepseek-chat","messages":[{"role":"user","content":prompt}],
                      "max_tokens":200,"temperature":0.3},
                headers={'Authorization':f'Bearer {DEEPSEEK_KEY}','Content-Type':'application/json'}, timeout=30)
            if resp.ok:
                return resp.json().get('choices',[{}])[0].get('message',{}).get('content','').strip()
    except Exception as e:
        if DEBUG_MODE: log(f"    AI失败: {e}")
    return None

def ai_enrich_batch(standards):
    provider = 'DeepSeek' if DEEPSEEK_KEY else ('通义千问/百炼' if QWEN_KEY else None)
    if not provider:
        log("  ⚠️  未配置AI Key，跳过"); return standards
    log(f"\n🤖 AI摘要补全（{provider}）…")
    enriched = 0
    for i, std in enumerate(standards):
        if std.get('summary','').strip(): continue
        s = ai_enrich_standard(std)
        if s and len(s) > 10:
            standards[i]['summary'] = s; enriched += 1
            log(f"  ✅ [{std['code']}] {s[:40]}…")
        time.sleep(0.5)
    log(f"  补全 {enriched} 条")
    return standards

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
            orig = existing[idx[cn]]; changed = False
            for f in ('status','abolishDate','implementDate','issueDate','issuedBy','title'):
                nv = item.get(f)
                if nv and nv != orig.get(f):
                    orig[f] = nv; changed = True
            if changed: updated_n += 1
        else:
            existing.append(build_entry(item))
            idx[cn] = len(existing)-1; added += 1
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
    log("=" * 60)
    log(f"体育标准数据库 — 自动抓取更新 v18.1")
    log(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"AI摘要: {'DeepSeek' if DEEPSEEK_KEY else '通义千问/百炼' if QWEN_KEY else '未配置'}")
    log("=" * 60)

    db, standards = load_db()

    for i, std in enumerate(standards):
        if std.get('title') and '<sacinfo>' in std['title']:
            standards[i]['title'] = clean_sacinfo(std['title'])
    before = len(standards)
    standards = [s for s in standards if is_sports(s.get('title',''))]
    if before - len(standards) > 0:
        log(f"🗑️  清理非体育标准：移除 {before-len(standards)} 条")

    if check_only:
        log(f"\n🔍 核查现有 {len(standards)} 条标准状态…")
        changed = 0
        for std in standards:
            code = std.get('code','')
            if not code: continue
            rows, _ = samr_get(code, 1)
            for row in rows:
                rc = clean_code(row.get('C_STD_CODE') or row.get('STD_CODE') or '')
                if norm_code(rc) == norm_code(code):
                    ns = norm_status(row.get('STATE') or '')
                    if ns and ns != std.get('status'):
                        old = std['status']; std['status'] = ns
                        if ns == '废止':
                            std['abolishDate'] = norm_date(row.get('ABOL_DATE')) or datetime.now().strftime('%Y-%m-%d')
                        changed += 1
                        log(f"  🔄 {code}: {old} → {ns}")
                    break
            time.sleep(0.5)
        log(f"  状态变更: {changed} 条")
        save_db(db, standards, dry_run)
        return

    all_new = []
    all_new.extend(fetch_by_keywords())
    all_new.extend(fetch_by_known_codes())

    log(f"\n🔀 合并（采集 {len(all_new)} 条）…")
    before2 = len(standards)
    standards, added, updated_n = merge(standards, all_new)
    log(f"  新增 {added} | 更新 {updated_n} | 原有 {before2} | 最终 {len(standards)}")

    type_counts = {}
    for s in standards:
        t = s.get('type','?'); type_counts[t] = type_counts.get(t,0)+1
    log(f"  类型分布: {type_counts}")

    if use_ai:
        standards = ai_enrich_batch(standards)

    save_db(db, standards, dry_run)

    active = sum(1 for s in standards if s.get('status')=='现行')
    abol   = sum(1 for s in standards if s.get('status')=='废止')
    coming = sum(1 for s in standards if s.get('status')=='即将实施')
    log(f"\n📊 总 {len(standards)} | 现行 {active} | 废止 {abol} | 即将实施 {coming}")
    log(f"\n💡 地标/团标请在 admin.html 后台手动录入（自动抓取暂不可行）")

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--dry',   action='store_true')
    p.add_argument('--check', action='store_true')
    p.add_argument('--ai',    action='store_true')
    p.add_argument('--debug', action='store_true')
    p.add_argument('--test',  action='store_true')
    args = p.parse_args()
    DEBUG_MODE = args.debug or args.test
    if args.test:
        run_test()
    else:
        run(dry_run=args.dry, check_only=args.check, use_ai=args.ai)
