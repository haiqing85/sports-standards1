#!/usr/bin/env python3
"""
体育建设标准自动抓取 & 更新脚本 v5
======================================
本地国内运行最佳，可访问所有国内平台。

数据来源（按优先级）：
  ① 官方平台（国内IP可直连）
     - 全国标准信息公共服务平台 std.samr.gov.cn
     - 国家标准全文公开系统    openstd.samr.gov.cn
     - 全国团体标准信息平台    ttbz.org.cn
     - 地方标准数据库          dbba.sacinfo.org.cn
  ② 国内搜索引擎（补充发现）
     - 百度搜索
     - 搜狗搜索
     - 360搜索
  ③ AI大模型（结构化 + 摘要生成）
     - DeepSeek API（免费额度，需自备Key）
     - 通义千问 API（免费额度，需自备Key）
  ④ 内置兜底数据（GitHub Actions 环境下使用）

运行方式：
  python scripts/update_standards.py              # 完整抓取
  python scripts/update_standards.py --check      # 仅核查状态
  python scripts/update_standards.py --builtin    # 仅内置数据
  python scripts/update_standards.py --ai         # 启用AI补全摘要
  python scripts/update_standards.py --dry        # 预览不写入

配置 AI Key（可选，不填也能正常运行）：
  设置环境变量 DEEPSEEK_KEY=your_key
  设置环境变量 QWEN_KEY=your_key
  或在脚本同目录建 .env 文件写入：
    DEEPSEEK_KEY=sk-xxxxx
    QWEN_KEY=sk-xxxxx
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

# ── 读取 .env 文件里的 Key ──────────────────────────────────
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
#  内置兜底数据（30条，GitHub Actions 环境用）
# ============================================================
BUILTIN_STANDARDS = [
    {"code":"GB 36246-2018","title":"中小学合成材料面层运动场地","type":"国家标准","status":"现行",
     "issueDate":"2018-05-14","implementDate":"2018-11-01","issuedBy":"国家市场监督管理总局",
     "isMandatory":True,"category":"合成材料面层","tags":["跑道","操场","中小学","塑胶","安全"],
     "summary":"规定了中小学合成材料面层运动场地的技术要求、试验方法、检验规则，重点对有害物质限量提出强制性要求。"},
    {"code":"GB/T 14833-2011","title":"合成材料跑道","type":"国家标准","status":"现行",
     "issueDate":"2011-12-30","implementDate":"2012-10-01","issuedBy":"国家质量监督检验检疫总局",
     "isMandatory":False,"category":"合成材料面层","tags":["跑道","田径","塑胶"],
     "summary":"规定了合成材料跑道的分类、要求、试验方法、检验规则及标志、运输和贮存。"},
    {"code":"JG/T 477-2015","title":"运动场地合成材料面层","type":"行业标准","status":"现行",
     "issueDate":"2015-01-14","implementDate":"2015-08-01","issuedBy":"住房和城乡建设部",
     "isMandatory":False,"category":"合成材料面层","tags":["塑胶跑道","合成材料","运动场"],
     "summary":"规定了运动场地合成材料面层的术语和定义、分类、要求、试验方法、检验规则及标识、运输和贮存。"},
    {"code":"GB/T 22517.6-2011","title":"体育场地使用要求及检验方法 第6部分：田径场地","type":"国家标准","status":"现行",
     "issueDate":"2011-07-29","implementDate":"2012-04-01","issuedBy":"国家质量监督检验检疫总局",
     "isMandatory":False,"category":"合成材料面层","tags":["田径","跑道","场地","检验"],
     "summary":"规定了田径场地面层材料的技术要求、检验方法和使用要求。"},
    {"code":"DB11/T 1827-2021","title":"学校操场合成材料面层有害物质限量","type":"地方标准","status":"现行",
     "issueDate":"2021-01-19","implementDate":"2021-05-01","issuedBy":"北京市市场监督管理局",
     "isMandatory":True,"category":"合成材料面层","tags":["北京","学校操场","有害物质","塑胶"],
     "summary":"北京市地方标准，严于国家标准，规定了学校操场合成材料面层有害物质的限量要求。"},
    {"code":"DB31/T 1150-2019","title":"中小学运动场地合成材料面层技术要求","type":"地方标准","status":"现行",
     "issueDate":"2019-05-06","implementDate":"2019-08-01","issuedBy":"上海市市场监督管理局",
     "isMandatory":True,"category":"合成材料面层","tags":["上海","学校操场","合成材料"],
     "summary":"上海市地方标准，对中小学运动场地合成材料面层提出严格技术要求，优于国家标准。"},
    {"code":"DB44/T 2321-2021","title":"广东省学校运动场地合成材料面层技术规范","type":"地方标准","status":"现行",
     "issueDate":"2021-09-26","implementDate":"2021-12-26","issuedBy":"广东省市场监督管理局",
     "isMandatory":False,"category":"合成材料面层","tags":["广东","学校","合成材料","跑道"],
     "summary":"广东省地方标准，规定了广东省学校运动场地合成材料面层的技术要求和检验方法。"},
    {"code":"T/SGTAS 001-2019","title":"合成材料运动场地面层施工与验收规范","type":"团标","status":"现行",
     "issueDate":"2019-03-01","implementDate":"2019-04-01","issuedBy":"中国体育用品业联合会",
     "isMandatory":False,"category":"合成材料面层","tags":["合成材料","施工","验收","跑道"],
     "summary":"规定了合成材料运动场地面层的施工流程、质量控制要求及验收标准。"},
    {"code":"JG/T 388-2012","title":"运动场人造草","type":"行业标准","status":"现行",
     "issueDate":"2012-04-05","implementDate":"2012-10-01","issuedBy":"住房和城乡建设部",
     "isMandatory":False,"category":"人造草坪","tags":["人造草","足球","运动场","草坪"],
     "summary":"规定了运动场人造草的术语、分类、技术要求、试验方法、检验规则及标识、运输和贮存。"},
    {"code":"GB/T 36527-2018","title":"运动场地人造草坪系统","type":"国家标准","status":"现行",
     "issueDate":"2018-09-17","implementDate":"2019-04-01","issuedBy":"国家市场监督管理总局",
     "isMandatory":False,"category":"人造草坪","tags":["人造草坪","系统","运动场地"],
     "summary":"规定了运动场地人造草坪系统的设计、施工、验收和维护保养要求。"},
    {"code":"GB/T 22517.2-2017","title":"体育场地使用要求及检验方法 第2部分：足球场地","type":"国家标准","status":"现行",
     "issueDate":"2017-09-07","implementDate":"2018-04-01","issuedBy":"国家质量监督检验检疫总局",
     "isMandatory":False,"category":"人造草坪","tags":["足球","人造草","场地","检验"],
     "summary":"规定了足球场地面层的技术要求、检验方法和使用要求，适用于人造草坪足球场地。"},
    {"code":"GB/T 32085-2015","title":"人造草填充材料 橡胶颗粒","type":"国家标准","status":"现行",
     "issueDate":"2015-09-11","implementDate":"2016-03-01","issuedBy":"国家质量监督检验检疫总局",
     "isMandatory":False,"category":"颗粒填充料","tags":["橡胶颗粒","人造草","填充料"],
     "summary":"规定了用于人造草坪填充的橡胶颗粒的分类、技术要求、检验方法和检验规则。"},
    {"code":"T/SGTAS 002-2019","title":"人造草坪运动场地系统施工与验收规范","type":"团标","status":"现行",
     "issueDate":"2019-03-01","implementDate":"2019-04-01","issuedBy":"中国体育用品业联合会",
     "isMandatory":False,"category":"人造草坪","tags":["人造草","施工","验收"],
     "summary":"规定了人造草坪运动场地系统的施工和验收要求，包括基础工程和草坪铺设要求。"},
    {"code":"GB/T 36536-2018","title":"体育场馆照明设计及检测标准","type":"国家标准","status":"现行",
     "issueDate":"2018-09-17","implementDate":"2019-04-01","issuedBy":"国家市场监督管理总局",
     "isMandatory":False,"category":"灯光照明","tags":["照明","体育场馆","LED","灯光"],
     "summary":"规定了体育场馆照明设计原则、照度标准值、照明质量、节能要求及检测方法。"},
    {"code":"JGJ 153-2016","title":"体育场馆照明设计及检测标准","type":"行业标准","status":"现行",
     "issueDate":"2016-08-18","implementDate":"2017-04-01","issuedBy":"住房和城乡建设部",
     "isMandatory":True,"category":"灯光照明","tags":["照明","体育场馆","灯光设计","检测"],
     "summary":"规定了体育场馆照明设计参数、照度标准、均匀度、眩光控制和能效要求，及检测评估方法。"},
    {"code":"T/SGTAS 005-2020","title":"运动场地LED照明系统技术规范","type":"团标","status":"现行",
     "issueDate":"2020-06-01","implementDate":"2020-07-01","issuedBy":"中国体育用品业联合会",
     "isMandatory":False,"category":"灯光照明","tags":["LED","照明","运动场地"],
     "summary":"规定了运动场地LED照明系统的技术要求、设计规范和验收标准。"},
    {"code":"GB/T 51048-2014","title":"体育建筑电气设计规范","type":"国家标准","status":"现行",
     "issueDate":"2014-12-02","implementDate":"2015-08-01","issuedBy":"住房和城乡建设部",
     "isMandatory":True,"category":"灯光照明","tags":["体育建筑","电气设计","照明"],
     "summary":"规定了体育建筑电气设计的基本要求，包括供配电、照明、接地等各系统的设计规范。"},
    {"code":"JG/T 354-2012","title":"体育用木质地板","type":"行业标准","status":"现行",
     "issueDate":"2012-01-09","implementDate":"2012-07-01","issuedBy":"住房和城乡建设部",
     "isMandatory":False,"category":"木地板","tags":["木地板","体育","篮球","排球"],
     "summary":"规定了体育用木质地板的分类、技术要求、试验方法、检验规则及标识、运输和贮存。"},
    {"code":"GB/T 22517.3-2011","title":"体育场地使用要求及检验方法 第3部分：篮球场地","type":"国家标准","status":"现行",
     "issueDate":"2011-07-29","implementDate":"2012-04-01","issuedBy":"国家质量监督检验检疫总局",
     "isMandatory":False,"category":"木地板","tags":["篮球","场地","木地板","室内"],
     "summary":"规定了篮球场地面层材料的技术要求、检验方法，包括木地板和合成材料地板。"},
    {"code":"JG/T 449-2014","title":"弹性地板","type":"行业标准","status":"现行",
     "issueDate":"2014-01-16","implementDate":"2014-08-01","issuedBy":"住房和城乡建设部",
     "isMandatory":False,"category":"PVC运动地胶","tags":["PVC","弹性地板","运动","地胶"],
     "summary":"规定了弹性地板（包括PVC运动地板）的术语、分类、技术要求、试验方法和检验规则。"},
    {"code":"GB/T 22517.7-2011","title":"体育场地使用要求及检验方法 第7部分：健身房地面","type":"国家标准","status":"现行",
     "issueDate":"2011-07-29","implementDate":"2012-04-01","issuedBy":"国家质量监督检验检疫总局",
     "isMandatory":False,"category":"PVC运动地胶","tags":["健身房","地面","PVC","运动地板"],
     "summary":"规定了健身房地面材料的技术要求、检验方法和使用要求。"},
    {"code":"GB/T 28231-2011","title":"体育围网","type":"国家标准","status":"现行",
     "issueDate":"2011-07-29","implementDate":"2012-01-01","issuedBy":"国家质量监督检验检疫总局",
     "isMandatory":False,"category":"围网","tags":["围网","体育场","防护网"],
     "summary":"规定了体育围网的术语定义、分类、技术要求、试验方法、检验规则及标识、运输和贮存。"},
    {"code":"GB 19272-2011","title":"室外健身器材的安全 通用要求","type":"国家标准","status":"现行",
     "issueDate":"2011-12-30","implementDate":"2012-08-01","issuedBy":"国家质量监督检验检疫总局",
     "isMandatory":True,"category":"健身路径","tags":["健身器材","室外","安全","公园"],
     "summary":"规定了室外健身器材的设计和制造安全通用要求、试验方法、标志、使用说明及安装安全要求。"},
    {"code":"GB/T 3976-2014","title":"学校体育器材 配备目录","type":"国家标准","status":"现行",
     "issueDate":"2014-06-09","implementDate":"2014-10-01","issuedBy":"国家质量监督检验检疫总局",
     "isMandatory":False,"category":"体育器材","tags":["学校","体育器材","配备"],
     "summary":"规定了中小学体育器材的配备目录和基本要求，为学校体育设施建设的重要依据。"},
    {"code":"JGJ 31-2003","title":"体育建筑设计规范","type":"行业标准","status":"现行",
     "issueDate":"2003-04-30","implementDate":"2003-09-01","issuedBy":"建设部",
     "isMandatory":True,"category":"场地设计","tags":["体育建筑","设计规范","场馆"],
     "summary":"规定了体育建筑设计的基本要求、功能分区、技术指标，包括运动场地的各项设计要求。"},
    {"code":"GB/T 37546-2019","title":"公共体育场地设施建设技术要求","type":"国家标准","status":"现行",
     "issueDate":"2019-06-04","implementDate":"2020-01-01","issuedBy":"国家市场监督管理总局",
     "isMandatory":False,"category":"场地设计","tags":["公共体育","场地设施","建设"],
     "summary":"规定了公共体育场地设施规划、建设和管理的基本技术要求。"},
    {"code":"GB/T 40115-2021","title":"体育公园建设指南","type":"国家标准","status":"现行",
     "issueDate":"2021-05-21","implementDate":"2021-12-01","issuedBy":"国家市场监督管理总局",
     "isMandatory":False,"category":"场地设计","tags":["体育公园","建设指南","全民健身"],
     "summary":"规定了体育公园选址、规划、建设和运营管理的技术要求和指南。"},
    {"code":"GB/T 22517.1-2016","title":"体育场地使用要求及检验方法 第1部分：通用要求","type":"国家标准","status":"现行",
     "issueDate":"2016-01-27","implementDate":"2016-12-01","issuedBy":"国家质量监督检验检疫总局",
     "isMandatory":False,"category":"综合","tags":["通用要求","场地","检验"],
     "summary":"规定了体育场地的通用使用要求和检验方法，是系列标准的基础部分。"},
    {"code":"GB/T 22517.5-2011","title":"体育场地使用要求及检验方法 第5部分：网球场地","type":"国家标准","status":"现行",
     "issueDate":"2011-07-29","implementDate":"2012-04-01","issuedBy":"国家质量监督检验检疫总局",
     "isMandatory":False,"category":"合成材料面层","tags":["网球","场地","合成材料"],
     "summary":"规定了网球场地面层材料的技术要求、检验方法和使用要求。"},
    {"code":"GB/T 14960-2009","title":"体育用人造草皮","type":"国家标准","status":"废止",
     "issueDate":"2009-06-01","implementDate":"2010-01-01","abolishDate":"2017-06-01",
     "replacedBy":"JG/T 388-2012","issuedBy":"国家质量监督检验检疫总局",
     "isMandatory":False,"category":"人造草坪","tags":["人造草","体育","草皮"],
     "summary":"【已废止】原规定体育用人造草皮的术语、分类、技术要求等，已由JG/T 388-2012替代。"},
]

# ============================================================
#  搜索关键词
# ============================================================
KEYWORDS = [
    "合成材料面层 体育", "塑胶跑道 标准", "合成材料跑道 GB",
    "人造草坪 运动场 标准", "人造草 JG/T", "人造草填充 橡胶颗粒",
    "体育场馆照明 标准", "运动场照明 JGJ", "LED照明 体育场地",
    "体育木地板 JG/T", "运动木地板 标准", "弹性地板 PVC运动",
    "体育围网 GB/T", "室外健身器材 GB 19272",
    "体育场地 标准 GB/T 22517", "运动场地 建设 标准",
    "体育建筑设计规范 JGJ", "体育公园 建设 标准",
    "足球场地 标准", "篮球场地 木地板 标准",
    "网球场地 合成材料", "田径场地 跑道 标准",
    "游泳池 标准", "学校操场 合成材料 标准",
    "颗粒填充料 人造草", "健身路径 器材 标准",
    "体育设施建设 技术要求",
]

SPORTS_KW = [
    "体育","运动","健身","竞技","跑道","操场","球场","场馆",
    "合成材料","人造草","草坪","塑胶","围网","木地板","PVC",
    "弹性地板","颗粒","游泳","篮球","足球","网球","排球",
    "羽毛球","田径","乒乓","健身器材","灯光",
]

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36'

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
    cm = {"合成材料":"合成材料面层","塑胶跑道":"合成材料面层",
          "人造草":"人造草坪","草坪":"人造草坪",
          "照明":"灯光照明","灯光":"灯光照明",
          "木地板":"木地板","PVC":"PVC运动地胶","弹性地板":"PVC运动地胶",
          "围网":"围网","健身器材":"健身路径","健身路径":"健身路径",
          "体育器材":"体育器材","颗粒":"颗粒填充料","游泳":"游泳场地",
          "建筑":"场地设计","设计规范":"场地设计"}
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
        'summary':       item.get('summary') or f"规定了{title}的技术要求、试验方法及相关规定。",
        'isMandatory':   item.get('isMandatory', is_mandatory(code)),
        'scope':         item.get('scope') or f"适用于{title}相关场合",
        'localFile':     f"downloads/{make_id(code)}.pdf",
    }

# ============================================================
#  官方来源（本地国内运行效果最佳）
# ============================================================
def fetch_samr(keyword):
    """全国标准信息公共服务平台"""
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
            rows = (resp.json().get('rows') or
                    resp.json().get('data',{}).get('rows',[]) or [])
            for row in rows:
                code  = (row.get('STD_CODE') or row.get('stdCode') or '').strip()
                title = (row.get('STD_NAME') or row.get('stdName') or '').strip()
                if not code or not title or not is_sports(title): continue
                hcno = row.get('PLAN_CODE') or row.get('hcno') or ''
                results.append({
                    'code': code, 'title': title,
                    'status': norm_status(row.get('STD_STATUS') or ''),
                    'issueDate': norm_date(row.get('ISSUE_DATE') or row.get('issueDate')),
                    'implementDate': norm_date(row.get('IMPL_DATE') or row.get('implDate')),
                    'abolishDate': norm_date(row.get('ABOL_DATE') or row.get('abolDate')),
                    'issuedBy': (row.get('ISSUE_DEPT') or row.get('issueDept') or '').strip(),
                    'isMandatory': is_mandatory(code),
                    'readUrl': f"https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno={hcno}" if hcno else None,
                })
            if results: break
        except Exception: continue
    return results

def fetch_ttbz(keyword):
    """全国团体标准信息平台"""
    results = []
    try:
        resp = SESSION.post("https://www.ttbz.org.cn/api/search/standard",
                            json={"keyword": keyword, "pageIndex": 1, "pageSize": 30},
                            headers={'Referer':'https://www.ttbz.org.cn/'}, timeout=20)
        if resp.ok:
            for row in (resp.json().get('Data') or resp.json().get('data') or []):
                code  = (row.get('StdCode') or row.get('stdCode') or '').strip()
                title = (row.get('StdName') or row.get('stdName') or '').strip()
                if code and title and is_sports(title):
                    results.append({'code': code, 'title': title, 'type': '团标',
                        'status': norm_status(row.get('Status') or '现行'),
                        'issueDate': norm_date(row.get('IssueDate')),
                        'implementDate': norm_date(row.get('ImplementDate')),
                        'issuedBy': (row.get('OrgName') or '').strip(),
                        'isMandatory': False})
    except Exception as e:
        log(f"    ttbz: {e}")
    return results

def fetch_dbba(keyword):
    """地方标准数据库"""
    results = []
    try:
        resp = SESSION.get('https://dbba.sacinfo.org.cn/api/standard/list',
                           params={"searchText": keyword, "pageSize": 30, "pageNum": 1},
                           headers={'Referer':'https://dbba.sacinfo.org.cn/'}, timeout=20)
        if resp.ok:
            for item in ((resp.json().get('data') or {}).get('list') or []):
                code  = (item.get('stdCode') or '').strip()
                title = (item.get('stdName') or '').strip()
                if code and title and is_sports(title):
                    results.append({'code': code, 'title': title, 'type': '地方标准',
                        'status': norm_status(item.get('status') or ''),
                        'issueDate': norm_date(item.get('publishDate')),
                        'implementDate': norm_date(item.get('implementDate')),
                        'issuedBy': (item.get('publishDept') or '').strip(),
                        'isMandatory': False})
    except Exception as e:
        log(f"    dbba: {e}")
    return results

# ============================================================
#  搜索引擎来源（辅助发现新标准编号）
# ============================================================
STD_CODE_PATTERN = re.compile(
    r'\b(GB[/ T]*[\d]+-\d{4}|GB/T[\s]*[\d]+-\d{4}|'
    r'JG/T[\s]*[\d]+-\d{4}|JGJ[\s]*[\d]+-\d{4}|'
    r'T/[A-Z]+[\s]*[\d]+-\d{4}|DB\d+/T?[\s]*[\d]+-\d{4})\b'
)

def extract_codes_from_html(html_text, context_keyword):
    """从HTML文本中提取标准编号"""
    codes = STD_CODE_PATTERN.findall(html_text)
    results = []
    for code in set(codes):
        code = re.sub(r'\s+', ' ', code).strip()
        results.append({
            'code': code,
            'title': f"{context_keyword}相关标准",
            'status': '现行',
            'isMandatory': is_mandatory(code),
        })
    return results

def fetch_baidu(keyword):
    """百度搜索 —— 发现标准编号"""
    results = []
    try:
        resp = SESSION.get('https://www.baidu.com/s',
                           params={'wd': f'{keyword} 标准编号 GB JG', 'rn': '20'},
                           headers={'Referer': 'https://www.baidu.com/',
                                    'Accept': 'text/html,application/xhtml+xml'},
                           timeout=15)
        if resp.ok:
            results = extract_codes_from_html(resp.text, keyword)
            if results:
                log(f"    百度发现 {len(results)} 个标准编号")
    except Exception as e:
        log(f"    百度: {e}")
    return results

def fetch_sogou(keyword):
    """搜狗搜索 —— 发现标准编号"""
    results = []
    try:
        resp = SESSION.get('https://www.sogou.com/web',
                           params={'query': f'{keyword} 国家标准 GB JG'},
                           headers={'Referer': 'https://www.sogou.com/',
                                    'Accept': 'text/html'},
                           timeout=15)
        if resp.ok:
            resp.encoding = 'utf-8'
            results = extract_codes_from_html(resp.text, keyword)
            if results:
                log(f"    搜狗发现 {len(results)} 个标准编号")
    except Exception as e:
        log(f"    搜狗: {e}")
    return results

def fetch_so360(keyword):
    """360搜索 —— 发现标准编号"""
    results = []
    try:
        resp = SESSION.get('https://www.so.com/s',
                           params={'q': f'{keyword} 体育标准 GB'},
                           headers={'Referer': 'https://www.so.com/',
                                    'Accept': 'text/html'},
                           timeout=15)
        if resp.ok:
            results = extract_codes_from_html(resp.text, keyword)
            if results:
                log(f"    360发现 {len(results)} 个标准编号")
    except Exception as e:
        log(f"    360: {e}")
    return results

# ============================================================
#  AI大模型来源（生成摘要 + 补全信息）
# ============================================================
def ai_enrich_standard(std, provider='deepseek'):
    """
    用AI大模型为标准生成准确摘要和补全信息。
    provider: 'deepseek' 或 'qwen'
    """
    if provider == 'deepseek' and not DEEPSEEK_KEY:
        return None
    if provider == 'qwen' and not QWEN_KEY:
        return None

    prompt = f"""你是一个中国标准化专家。请根据以下标准的基本信息，补全其摘要内容。

标准编号：{std.get('code','')}
标准名称：{std.get('title','')}
标准类型：{std.get('type','')}
发布机构：{std.get('issuedBy','')}
发布日期：{std.get('issueDate','')}

请用2-3句话简洁描述该标准的主要内容和规定范围。只返回摘要文字，不要其他说明。"""

    try:
        if provider == 'deepseek':
            resp = SESSION.post(
                'https://api.deepseek.com/chat/completions',
                json={"model": "deepseek-chat",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 200, "temperature": 0.3},
                headers={'Authorization': f'Bearer {DEEPSEEK_KEY}',
                         'Content-Type': 'application/json'},
                timeout=30
            )
        else:  # qwen
            resp = SESSION.post(
                'https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation',
                json={"model": "qwen-turbo",
                      "input": {"messages": [{"role": "user", "content": prompt}]},
                      "parameters": {"max_tokens": 200}},
                headers={'Authorization': f'Bearer {QWEN_KEY}',
                         'Content-Type': 'application/json'},
                timeout=30
            )
        if resp.ok:
            data = resp.json()
            if provider == 'deepseek':
                return data.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
            else:
                return data.get('output', {}).get('text', '').strip()
    except Exception as e:
        log(f"    AI({provider})补全失败: {e}")
    return None

def ai_enrich_batch(standards, max_count=50):
    """批量用AI补全缺少摘要的标准"""
    provider = 'deepseek' if DEEPSEEK_KEY else ('qwen' if QWEN_KEY else None)
    if not provider:
        log("  ⚠️  未配置AI Key，跳过AI摘要补全")
        log("     如需启用：在 scripts/.env 文件中添加 DEEPSEEK_KEY=your_key")
        return standards

    log(f"\n🤖 AI摘要补全（使用{provider}，最多处理{max_count}条）…")
    enriched = 0
    for i, std in enumerate(standards):
        # 只补全摘要为默认占位文字的条目
        summary = std.get('summary', '')
        if summary and '技术要求、试验方法及相关规定' not in summary:
            continue
        if enriched >= max_count:
            break
        new_summary = ai_enrich_standard(std, provider)
        if new_summary and len(new_summary) > 10:
            standards[i]['summary'] = new_summary
            enriched += 1
            log(f"  ✅ [{std['code']}] 摘要已补全")
        time.sleep(0.5)  # 避免触发限速

    log(f"  AI补全完成：{enriched} 条")
    return standards

# ============================================================
#  核查状态
# ============================================================
def check_status_online(std):
    code = std.get('code','')
    if not code: return None
    try:
        resp = SESSION.post("https://std.samr.gov.cn/gb/search/gbQueryPage",
                            json={"searchText": code, "pageSize": 5, "pageIndex": 1},
                            timeout=12)
        if not resp.ok: return None
        for row in (resp.json().get('rows') or []):
            rc = (row.get('STD_CODE') or '').strip()
            if rc and norm_code(rc) == norm_code(code):
                ns = norm_status(row.get('STD_STATUS',''))
                if ns and ns != std.get('status'):
                    upd = dict(std)
                    upd['status'] = ns
                    if ns == '废止':
                        upd['abolishDate'] = norm_date(row.get('ABOL_DATE')) or datetime.now().strftime('%Y-%m-%d')
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
            for f in ('status','abolishDate','implementDate','issueDate','issuedBy','readUrl'):
                nv = item.get(f)
                if nv and nv != orig.get(f):
                    orig[f] = nv; changed = True
            if changed: updated_n += 1
        else:
            existing.append(build_entry(item))
            idx[cn] = len(existing)-1
            added += 1
    return existing, added, updated_n

def save_db(db, standards, dry_run):
    today = datetime.now().strftime('%Y-%m-%d')
    db.update({'standards': standards, 'updated': today,
               'version': today.replace('-','.'), 'total': len(standards)})
    if dry_run:
        log(f"\n🔵 [预览] {len(standards)} 条，不写入文件"); return
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    log(f"\n✅ 写入完成：{len(standards)} 条  版本 {today}")

def load_db():
    if not DATA_FILE.exists():
        log("⚠️  数据文件不存在，将从空白建库")
        return {'standards': []}, []
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            db = json.load(f)
        standards = db.get('standards') or []
        log(f"📦 现有标准数: {len(standards)} 条")
        return db, standards
    except Exception as e:
        log(f"⚠️  数据文件损坏({e})，从空白建库")
        return {'standards': []}, []

# ============================================================
#  主流程
# ============================================================
def run(dry_run=False, check_only=False, builtin_only=False, use_ai=False):
    log("="*60)
    log(f"体育标准数据库 — 自动抓取更新 v5")
    log(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    ai_status = f"{'DeepSeek' if DEEPSEEK_KEY else '通义千问' if QWEN_KEY else '未配置'}"
    log(f"AI补全: {ai_status}  |  模式: {'[仅内置]' if builtin_only else '[预览]' if dry_run else '[仅核查]' if check_only else '[完整]'}")
    log("="*60)

    db, standards = load_db()

    # ── 内置数据（基础保障）──────────────────────────────────
    log(f"\n📚 Step 1：导入内置标准数据（{len(BUILTIN_STANDARDS)} 条）…")
    standards, b_added, b_upd = merge(standards, BUILTIN_STANDARDS)
    log(f"  新增 {b_added} | 更新 {b_upd} | 当前总量 {len(standards)}")

    if builtin_only:
        save_db(db, standards, dry_run); return

    # ── 状态核查 ────────────────────────────────────────────
    if standards:
        log(f"\n🔍 Step 2：核查标准状态…")
        changed = 0
        for i, std in enumerate(standards[:50]):
            upd = check_status_online(std)
            if upd:
                idx2 = next((j for j,s in enumerate(standards) if s['code']==std['code']), None)
                if idx2 is not None:
                    standards[idx2] = upd; changed += 1
                    log(f"  🔄 {std['code']}: {std.get('status')} → {upd['status']}")
            time.sleep(0.4)
        log(f"  状态变更 {changed} 条")

    if check_only:
        save_db(db, standards, dry_run); return

    # ── 官方平台（本地最佳）────────────────────────────────
    log(f"\n🌐 Step 3：官方平台抓取（{len(KEYWORDS)} 个关键词）…")
    all_new, official_ok = [], False
    for i, kw in enumerate(KEYWORDS, 1):
        log(f"  [{i:02d}/{len(KEYWORDS)}] {kw}")
        a = fetch_samr(kw)
        b = fetch_ttbz(kw)
        c = fetch_dbba(kw) if i % 3 == 0 else []
        got = len(a)+len(b)+len(c)
        if got:
            all_new.extend(a+b+c)
            official_ok = True
            log(f"         samr+{len(a)} ttbz+{len(b)} dbba+{len(c)}")
        time.sleep(1.0)

    # ── 搜索引擎（辅助发现新编号）────────────────────────
    log(f"\n🔎 Step 4：搜索引擎辅助发现…")
    search_new = []
    for kw in KEYWORDS[:10]:  # 取前10个关键词搜索
        s_a = fetch_baidu(kw)
        s_b = fetch_sogou(kw)
        s_c = fetch_so360(kw)
        search_new.extend(s_a + s_b + s_c)
        time.sleep(1.0)

    # 搜索引擎发现的编号，再去官方平台查详情
    if search_new:
        log(f"  搜索引擎共发现 {len(search_new)} 个编号，去官方平台核实…")
        for item in search_new[:30]:
            detail = fetch_samr(item['code'])
            if detail:
                all_new.extend(detail)
                log(f"    ✅ 核实成功: {item['code']}")
            time.sleep(0.5)

    # ── 合并 ───────────────────────────────────────────────
    if all_new:
        log(f"\n🔀 Step 5：合并去重（{len(all_new)} 条原始数据）…")
        before = len(standards)
        standards, added, updated_n = merge(standards, all_new)
        log(f"  新增 {added} | 更新 {updated_n} | 总量 {len(standards)}")
    else:
        log(f"\n  官方平台{'未返回数据（IP受限）' if not official_ok else '已完成'}，搜索引擎辅助{'有' if search_new else '无'}发现")

    # ── AI补全摘要（可选）─────────────────────────────────
    if use_ai:
        standards = ai_enrich_batch(standards, max_count=50)

    # ── 保存 ───────────────────────────────────────────────
    save_db(db, standards, dry_run)

    total  = len(standards)
    active = sum(1 for s in standards if s.get('status')=='现行')
    abol   = sum(1 for s in standards if s.get('status')=='废止')
    coming = sum(1 for s in standards if s.get('status')=='即将实施')
    log(f"\n📊 总 {total} | 现行 {active} | 废止 {abol} | 即将实施 {coming}")

    if not official_ok:
        log("\n💡 提示：官方平台在GitHub Actions中无法访问（IP限制）")
        log("   建议在国内电脑本地运行此脚本，可获取更多标准数据")

if __name__ == '__main__':
    p = argparse.ArgumentParser(description='体育标准自动抓取更新工具 v5')
    p.add_argument('--dry',     action='store_true', help='预览模式，不写入文件')
    p.add_argument('--check',   action='store_true', help='仅核查现有标准状态')
    p.add_argument('--builtin', action='store_true', help='仅导入内置标准数据')
    p.add_argument('--ai',      action='store_true', help='启用AI大模型补全摘要')
    args = p.parse_args()
    run(dry_run=args.dry, check_only=args.check,
        builtin_only=args.builtin, use_ai=args.ai)
