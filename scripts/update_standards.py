#!/usr/bin/env python3
"""
体育建设标准自动抓取 & 更新脚本 v9 (全量合并版)
======================================
v9 更新日志：
  - 移除屏蔽词：培训、照明、产业、康养、户外、登山、设施
  - 增强球类：足球、篮球、网球、排球、乒乓球、羽毛球、手球、棒球、冰球
  - 增强结构：增加围网、木地板、运动地胶等
  - 翻页升级：团标(ttbz)与地标(dbba)开启自动翻页深挖模式
  - 过滤放开：只要涉及体育相关关键词，一律抓取入库
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
#  抓取关键词 (已去掉：培训、照明、产业、康养、户外、登山、设施)
# ============================================================
KEYWORDS = [
    "体育", "足球", "篮球", "网球", "排球", "乒乓球", "羽毛球", "手球", "棒球", "冰球", "围网",
    "合成材料面层", "塑胶跑道", "合成材料跑道", "聚氨酯跑道", "橡胶面层运动场", "中小学合成材料",
    "人造草坪", "人造草皮", "运动场人造草", "人工草坪", "颗粒填充料", "草坪填充橡胶",
    "体育木地板", "运动木地板", "体育用木质地板",
    "运动地胶", "PVC运动地板", "弹性运动地板", "卷材运动地板",
    "体育围网", "运动场围网", "球场围网",
    "室外健身器材", "健身路径", "公共健身器材", "体育器材", "学校体育器材",
    "游泳场地", "游泳馆", "游泳池水质",
    "足球场地", "篮球场地", "网球场地", "田径场地", "排球场地", "羽毛球场地", "乒乓球场地",
    "体育场地", "运动场地", "体育场馆建设", "体育建筑设计", "体育公园", "学校操场",
]

# ============================================================
#  过滤逻辑 (放开限制版)
# ============================================================
def is_sports(title):
    if not title: return False
    # 核心词：只要包含以下词汇即视为相关
    include_terms = [
        '体育', '运动', '比赛', '赛事', '场馆', '健身', '训练', '器材', 
        '场地', '跑道', '草坪', '球场', '木地板', '围网', '游泳', '冰雪',
        '足球', '篮球', '网球', '排球', '乒乓球', '羽毛球', '手球', '棒球', '冰球'
    ]
    # 强制排除词（用户明确要求去掉的内容）
    exclude_terms = ['培训', '照明', '产业', '康养', '户外', '登山', '设施']
    
    title_upper = title.upper()
    
    # 首先检查是否包含屏蔽词
    if any(ex in title_upper for ex in exclude_terms):
        return False
        
    # 然后检查是否属于相关体育词汇
    return any(term in title_upper for term in include_terms)

# ============================================================
#  基础辅助函数
# ============================================================
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
    except Exception: pass

def make_id(code):
    return re.sub(r'[^A-Za-z0-9]', '', code)[:30] or hashlib.md5(code.encode()).hexdigest()[:12]

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
    c = re.sub(r'\s+', '', code).upper()
    if re.match(r'^GB\d', c) and '/T' not in c: return True
    if c.startswith('JGJ'): return True
    return False

def guess_type(code):
    cu = re.sub(r'\s+', '', code).upper()
    for prefix, t in [("GB/T","国家标准"),("GB","国家标准"),("JGJ","行业标准"),
                       ("JG/T","行业标准"),("CJJ","行业标准"),("T/","团标"),("DB","地方标准")]:
        if cu.startswith(re.sub(r'\s+', '', prefix).upper()): return t
    return "国家标准"

# ============================================================
#  来源一：std.samr.gov.cn (国家/行业标准，已支持翻页)
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
                "