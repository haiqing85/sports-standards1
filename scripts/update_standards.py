#!/usr/bin/env python3
"""
体育建设标准自动抓取 & 更新脚本 v6
======================================
数据来源（全部来自在线平台，无内置数据）：
  ① 官方平台（本地国内运行直连）
     - std.samr.gov.cn     全国标准信息公共服务平台
     - openstd.samr.gov.cn 国家标准全文公开系统
     - ttbz.org.cn         全国团体标准信息平台
     - dbba.sacinfo.org.cn 地方标准数据库
  ② 国内搜索引擎（辅助发现新标准）
     - 百度 / 搜狗 / 360搜索
  ③ AI大模型（补全摘要，需配置Key）
     - 阿里云百炼（通义千问）
     - DeepSeek

注意：本脚本在 GitHub Actions 环境下因 IP 被限制可能抓取为空，
      建议在国内电脑本地运行后将 standards.json 上传至仓库。

运行方式：
  python scripts/update_standards.py         # 完整抓取
  python scripts/update_standards.py --check # 仅核查现有标准状态
  python scripts/update_standards.py --ai    # 启用AI补全摘要
  python scripts/update_standards.py --dry   # 预览不写入

AI Key 配置（在 scripts/.env 文件中填写）：
  QWEN_KEY=sk-xxxxx       # 阿里云百炼/通义千问
  DEEPSEEK_KEY=sk-xxxxx   # DeepSeek
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

# ── 读取 .env 文件 ──────────────────────────────────────────
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
#  搜索关键词
# ============================================================
KEYWORDS = [
    "合成材料面层 体育场地", "塑胶跑道 国家标准",
    "人造草坪 运动场 标准", "人造草 JG/T",
    "体育场馆照明 标准", "运动场照明 JGJ",
    "体育木地板 JG/T", "弹性地板 PVC运动地板 标准",
    "体育围网 GB/T",
    "室外健身器材 安全 标准",
    "体育场地 GB/T 22517",
    "体育建筑设计规范 JGJ",
    "体育公园 全民健身 标准",
    "足球场地 人造草 标准",
    "篮球场地 木地板 标准",
    "网球场地 合成材料 标准",
    "田径场地 跑道 标准",
    "游泳场地 标准",
    "学校操场 合成材料 有害物质",
    "颗粒填充料 橡胶颗粒 人造草",
    "健身路径 器材 标准",
    "体育设施建设 技术要求",
    "运动地板 PVC 弹性 标准",
    "体育器材 配备 学校",
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
#  工具函数
# ============================================================
def make_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429,500,502,503,504])
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
    for prefix, t in [("GB/T","国家标准"),("GB ","国家标准"),("JGJ","行业标准"),
                       ("JG/T","行业标准"),("CJJ","行业标准"),("T/","团标"),("DB","地方标准")]:
        if cu.startswith(prefix.upper().strip()): return t
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

# ============================================================
#  来源一：全国标准信息公共服务平台
# ============================================================
def fetch_samr(keyword):
    results = []
    for url, payload in [
        ("https://std.samr.gov.cn/gb/search/gbQueryPage",
         {"searchText": keyword, "status": "", "sortField": "ISSUE_DATE",
          "sortType": "desc", "pageSize": 50, "pageIndex": 1}),
        ("https://std.samr.gov.cn/search/std",
         {"keyword": keyword, "pageSize": 50, "pageNum": 1}),
    ]:
        try:
            resp = SESSION.post(url, json=payload, timeout=20,
                                headers={'Referer':'https://std.samr.gov.cn/',
                                         'Content-Type':'application/json'})
            if not resp.ok: continue
            data = resp.json()
            rows = (data.get('rows') or
                    data.get('data',{}).get('rows',[]) or
                    data.get('result',{}).get('data',[]) or [])
            for row in rows:
                code  = (row.get('STD_CODE') or row.get('stdCode') or '').strip()
                title = (row.get('STD_NAME') or row.get('stdName') or '').strip()
                if not code or not title or not is_sports(title): continue
                hcno = row.get('PLAN_CODE') or row.get('hcno') or ''
                results.append({
                    'code':          code,
                    'title':         title,
                    'status':        norm_status(row.get('STD_STATUS') or ''),
                    'issueDate':     norm_date(row.get('ISSUE_DATE') or row.get('issueDate')),
                    'implementDate': norm_date(row.get('IMPL_DATE') or row.get('implDate')),
                    'abolishDate':   norm_date(row.get('ABOL_DATE') or row.get('abolDate')),
                    'issuedBy':      (row.get('ISSUE_DEPT') or row.get('issueDept') or '').strip(),
                    'isMandatory':   is_mandatory(code),
                    'readUrl':       f"https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno={hcno}" if hcno else None,
                })
            if results: break
        except Exception: continue
    return results

# ============================================================
#  来源二：全国团体标准信息平台
# ============================================================
def fetch_ttbz(keyword):
    results = []
    try:
        resp = SESSION.post(
            "https://www.ttbz.org.cn/api/search/standard",
            json={"keyword": keyword, "pageIndex": 1, "pageSize": 30},
            headers={'Referer':'https://www.ttbz.org.cn/'},
            timeout=20
        )
        if resp.ok:
            for row in (resp.json().get('Data') or resp.json().get('data') or []):
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
        log(f"    ttbz: {e}")
    return results

# ============================================================
#  来源三：地方标准数据库
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
            for item in ((resp.json().get('data') or {}).get('list') or []):
                code  = (item.get('stdCode') or '').strip()
                title = (item.get('stdName') or '').strip()
                if code and title and is_sports(title):
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
        log(f"    dbba: {e}")
    return results

# ============================================================
#  来源四：国内搜索引擎（辅助发现标准编号）
# ============================================================
def extract_codes(html, keyword):
    codes = STD_CODE_RE.findall(html)
    results = []
    for code in set(codes):
        code = re.sub(r'\s+', ' ', code).strip()
        results.append({
            'code':       code,
            'title':      f'{keyword}相关标准',
            'status':     '现行',
            'isMandatory': is_mandatory(code),
        })
    return results

def fetch_baidu(keyword):
    try:
        resp = SESSION.get('https://www.baidu.com/s',
                           params={'wd': f'{keyword} 标准 GB JG', 'rn': '20'},
                           headers={'Referer':'https://www.baidu.com/'}, timeout=15)
        if resp.ok:
            r = extract_codes(resp.text, keyword)
            if r: log(f"    百度发现 {len(r)} 个编号")
            return r
    except Exception as e:
        log(f"    百度: {e}")
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
        log(f"    搜狗: {e}")
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
        log(f"    360: {e}")
    return []

# ============================================================
#  来源五：AI大模型（补全摘要）
# ============================================================
def ai_enrich_standard(std):
    provider = None
    if QWEN_KEY:     provider = 'qwen'
    if DEEPSEEK_KEY: provider = 'deepseek'
    if not provider: return None

    prompt = (f"你是中国标准化专家。请根据以下标准信息，用2-3句话准确描述其主要内容和适用范围。"
              f"只返回描述文字，不要加任何前缀或说明。\n\n"
              f"标准编号：{std.get('code','')}\n"
              f"标准名称：{std.get('title','')}\n"
              f"发布机构：{std.get('issuedBy','')}\n"
              f"发布日期：{std.get('issueDate','')}")
    try:
        if provider == 'deepseek':
            resp = SESSION.post(
                'https://api.deepseek.com/chat/completions',
                json={"model":"deepseek-chat",
                      "messages":[{"role":"user","content":prompt}],
                      "max_tokens":200,"temperature":0.3},
                headers={'Authorization':f'Bearer {DEEPSEEK_KEY}',
                         'Content-Type':'application/json'},
                timeout=30
            )
            if resp.ok:
                return resp.json().get('choices',[{}])[0].get('message',{}).get('content','').strip()
        else:  # qwen / 阿里云百炼
            resp = SESSION.post(
                'https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation',
                json={"model":"qwen-turbo",
                      "input":{"messages":[{"role":"user","content":prompt}]},
                      "parameters":{"max_tokens":200}},
                headers={'Authorization':f'Bearer {QWEN_KEY}',
                         'Content-Type':'application/json'},
                timeout=30
            )
            if resp.ok:
                return resp.json().get('output',{}).get('text','').strip()
    except Exception as e:
        log(f"    AI补全失败: {e}")
    return None

def ai_enrich_batch(standards, max_count=100):
    provider = 'DeepSeek' if DEEPSEEK_KEY else ('通义千问/百炼' if QWEN_KEY else None)
    if not provider:
        log("  ⚠️  未配置AI Key，跳过摘要补全")
        log("     在 scripts/.env 中添加 QWEN_KEY=sk-xxx 或 DEEPSEEK_KEY=sk-xxx")
        return standards

    log(f"\n🤖 AI摘要补全（{provider}，最多 {max_count} 条）…")
    enriched = 0
    for i, std in enumerate(standards):
        if enriched >= max_count: break
        if std.get('summary','').strip():  # 已有摘要则跳过
            continue
        summary = ai_enrich_standard(std)
        if summary and len(summary) > 10:
            standards[i]['summary'] = summary
            enriched += 1
            log(f"  ✅ [{std['code']}] {summary[:40]}…")
        time.sleep(0.5)
    log(f"  AI补全完成：{enriched} 条")
    return standards

# ============================================================
#  核查现有标准状态
# ============================================================
def check_status_online(std):
    code = std.get('code','')
    if not code: return None
    try:
        resp = SESSION.post(
            "https://std.samr.gov.cn/gb/search/gbQueryPage",
            json={"searchText": code, "pageSize": 5, "pageIndex": 1},
            timeout=12
        )
        if not resp.ok: return None
        for row in (resp.json().get('rows') or []):
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
            idx[cn] = len(existing) - 1
            added += 1
    return existing, added, updated_n

# ============================================================
#  读写
# ============================================================
def load_db():
    if not DATA_FILE.exists():
        log("⚠️  data/standards.json 不存在，将从空白开始")
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
    log("="*60)
    log(f"体育标准数据库 — 自动抓取更新 v6")
    log(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    ai_info = f"{'DeepSeek' if DEEPSEEK_KEY else '通义千问/百炼' if QWEN_KEY else '未配置（不影响抓取）'}"
    log(f"AI摘要: {ai_info}")
    log("="*60)

    db, standards = load_db()

    # ── Step 1：核查现有标准状态 ─────────────────────────────
    if standards:
        log(f"\n🔍 Step 1：核查 {len(standards)} 条标准最新状态…")
        changed = 0
        for i, std in enumerate(standards):
            upd = check_status_online(std)
            if upd:
                j = next((k for k,s in enumerate(standards) if s['code']==std['code']), None)
                if j is not None:
                    standards[j] = upd; changed += 1
                    log(f"  🔄 {std['code']}: {std.get('status')} → {upd['status']}")
            time.sleep(0.4)
        log(f"  状态变更 {changed} 条")
    else:
        log("\n📋 数据库为空，直接开始抓取")

    if check_only:
        save_db(db, standards, dry_run); return

    # ── Step 2：官方平台抓取 ────────────────────────────────
    log(f"\n🌐 Step 2：官方平台抓取（{len(KEYWORDS)} 个关键词）…")
    all_new, official_ok = [], False
    for i, kw in enumerate(KEYWORDS, 1):
        log(f"  [{i:02d}/{len(KEYWORDS)}] {kw}")
        a = fetch_samr(kw);   time.sleep(0.8)
        b = fetch_ttbz(kw);   time.sleep(0.6)
        c = fetch_dbba(kw) if i % 3 == 0 else []; time.sleep(0.5)
        got = len(a)+len(b)+len(c)
        if got:
            all_new.extend(a+b+c); official_ok = True
            log(f"         samr:{len(a)} ttbz:{len(b)} dbba:{len(c)}")

    # ── Step 3：搜索引擎辅助发现 ────────────────────────────
    log(f"\n🔎 Step 3：搜索引擎辅助发现新编号…")
    found_codes, search_new = set(), []
    for kw in KEYWORDS[:12]:
        for fn in [fetch_baidu, fetch_sogou, fetch_so360]:
            items = fn(kw)
            for item in items:
                cn = norm_code(item['code'])
                if cn not in found_codes:
                    found_codes.add(cn)
                    search_new.append(item)
            time.sleep(0.8)

    # 搜索引擎发现的编号，去官方平台获取完整信息
    if search_new:
        log(f"  搜索引擎共发现 {len(search_new)} 个编号，核实中…")
        for item in search_new[:40]:
            detail = fetch_samr(item['code'])
            if detail:
                all_new.extend(detail)
                log(f"    ✅ {item['code']}")
            time.sleep(0.6)

    # ── Step 4：合并去重 ────────────────────────────────────
    if all_new:
        log(f"\n🔀 Step 4：合并（{len(all_new)} 条原始数据）…")
        before = len(standards)
        standards, added, updated_n = merge(standards, all_new)
        log(f"  新增 {added} | 更新 {updated_n} | 总量 {len(standards)}")
    else:
        log(f"\n  ⚠️  本次未抓取到数据")
        if not official_ok:
            log("  原因：GitHub Actions 服务器 IP 被国内平台限制")
            log("  建议：在国内电脑本地运行此脚本，效果最佳")

    # ── Step 5：AI补全摘要（可选）─────────────────────────
    if use_ai:
        standards = ai_enrich_batch(standards)

    # ── 保存 ───────────────────────────────────────────────
    save_db(db, standards, dry_run)

    total  = len(standards)
    active = sum(1 for s in standards if s.get('status')=='现行')
    abol   = sum(1 for s in standards if s.get('status')=='废止')
    coming = sum(1 for s in standards if s.get('status')=='即将实施')
    log(f"\n📊 总 {total} | 现行 {active} | 废止 {abol} | 即将实施 {coming}")

if __name__ == '__main__':
    p = argparse.ArgumentParser(description='体育标准自动抓取更新工具 v6')
    p.add_argument('--dry',   action='store_true', help='预览模式，不写入文件')
    p.add_argument('--check', action='store_true', help='仅核查现有标准状态')
    p.add_argument('--ai',    action='store_true', help='启用AI大模型补全摘要')
    args = p.parse_args()
    run(dry_run=args.dry, check_only=args.check, use_ai=args.ai)
