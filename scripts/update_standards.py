#!/usr/bin/env python3
"""
体育标准数据库 — 自动抓取更新 v26（全需求合规终版）
核心适配与新增：
1. 手动修改保护边界修正：非状态字段手动修改后，自动抓取/扫描永久不动
2. 强制规则：无论手动/自动录入的标准，只要官网已废止，强制更新状态为「废止」
3. 新增全库扫描能力：扫描库内所有标准（含手动新增/修改的），过期自动变更状态+补充替代标准
4. 新增定时扫描模式：支持定时循环执行全库扫描，适配后台自动运行
5. 完整保留原有规则：彻底隐藏替代旧标准字段、现行标准移除已被替代为、发布机构精准抓取、无效ID全过滤
6. 扫描/抓取全程保护手动修改内容，仅更新状态和废止标准的替代字段
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

# 全局路径配置
ROOT      = Path(__file__).parent.parent
DATA_FILE = ROOT / 'data' / 'standards.json'
LOG_FILE  = ROOT / 'data' / 'update_log.txt'
ENV_FILE  = Path(__file__).parent / '.env'
DEBUG_MODE = False

# ===================== 核心校验规则（永久生效） =====================
# 合法标准号正则：仅支持国标/行标/团标/地标标准格式，过滤所有无效ID
STD_CODE_LEGAL_REGEX = re.compile(r'^[A-Z]+\/?T?\s*\d+(?:\.\d+)?\s*[－\-–]\s*\d{4}$', re.IGNORECASE)
# 标准主体拆分正则：用于同主体版本匹配、替代关系生成
STD_BASE_SPLIT_REGEX = re.compile(r'^([A-Z]+\/?T?\s*\d+(?:\.\d+)?)\s*[－\-–]\s*(\d{4})$', re.IGNORECASE)

def is_legal_std_code(code):
    """校验是否为合法标准号，过滤TC198/F772等所有无效ID"""
    if not code or not str(code).strip():
        return False
    return bool(STD_CODE_LEGAL_REGEX.match(str(code).strip()))

def split_std_base_and_year(code):
    """拆分标准号的主体（前缀+编号）和年份，用于版本匹配"""
    if not is_legal_std_code(code):
        return None, None
    m = STD_BASE_SPLIT_REGEX.match(code.strip())
    if not m:
        return None, None
    base = re.sub(r'\s+', '', m.group(1)).upper()
    try:
        year = int(m.group(2))
    except:
        return None, None
    return base, year

def clean_std_code_field(raw_content, self_code=''):
    """清洗替代字段，仅保留合法标准号，过滤无效ID、自替代内容，无合法内容返回None"""
    if not raw_content or not str(raw_content).strip():
        return None
    raw_str = str(raw_content).strip()
    candidate_codes = re.split(r'[;；,，\s]+', raw_str)
    legal_codes = []
    self_norm = re.sub(r'\s+', '', self_code).upper() if self_code else ''

    for code in candidate_codes:
        code_clean = code.strip()
        if not is_legal_std_code(code_clean):
            continue
        code_norm = re.sub(r'\s+', '', code_clean).upper()
        if code_norm == self_norm:
            continue
        legal_codes.append(code_clean)
    
    legal_codes = list(dict.fromkeys(legal_codes))
    return '；'.join(legal_codes) if legal_codes else None

def load_env():
    """加载环境变量配置"""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())
load_env()
DEEPSEEK_KEY = os.environ.get('DEEPSEEK_KEY', '')
QWEN_KEY     = os.environ.get('QWEN_KEY', '')

# ===================== 核心规则1：手动保护+状态强制修正+替代关系处理 =====================
def auto_fix_std_core_rules(standards):
    """
    全库核心规则执行，优先级从高到低：
    1. 彻底清空「替代旧标准(replaces)」字段，全量不显示
    2. 现行/即将实施标准，永久清空「已被替代为(replacedBy)」字段
    3. 同主体多版本自动修正：新版本现行则旧版本强制标记废止，补充替代关系
    4. 全程保护非状态/非替代字段的手动修改内容
    """
    # 按标准主体分组，用于同主体版本匹配
    std_base_groups = {}
    for s in standards:
        code = s.get('code', '')
        base, year = split_std_base_and_year(code)
        if not base or not year:
            continue
        if base not in std_base_groups:
            std_base_groups[base] = []
        std_base_groups[base].append({
            'std': s,
            'code': code,
            'year': year,
            'status': s.get('status', '现行')
        })
    
    updated_count = 0
    for base, versions in std_base_groups.items():
        if len(versions) < 2:
            continue
        # 按年份升序排序：旧版本在前，新版本在后
        versions.sort(key=lambda x: x['year'])
        version_total = len(versions)

        for index, version in enumerate(versions):
            std_item = version['std']
            current_status = version['status']
            current_code = version['code']
            is_user_manual_replacedBy = bool(std_item.get('replacedBy'))

            # 【强制规则1】全量清空「替代旧标准」字段，彻底不显示
            if std_item.get('replaces'):
                std_item['replaces'] = None
                updated_count += 1

            # 【强制规则2】现行/即将实施标准，永久清空「已被替代为」字段
            if current_status in ['现行', '即将实施']:
                if std_item.get('replacedBy'):
                    std_item['replacedBy'] = None
                    updated_count += 1
            # 【强制规则3】仅废止标准，补充/修正「已被替代为」，保护手动修改
            elif current_status == '废止' and index < version_total - 1 and not is_user_manual_replacedBy:
                next_version = versions[index+1]
                if next_version['year'] != version['year'] and next_version['code'] != current_code:
                    std_item['replacedBy'] = next_version['code']
                    updated_count += 1

            # 【强制规则4】同主体有更新的现行版本，旧版本强制标记为废止（无论手动/自动）
            if (index < version_total - 1
                and current_status == '现行'
                and versions[index+1]['status'] == '现行'
                and versions[index+1]['code'] != current_code):
                std_item['status'] = '废止'
                updated_count += 1

    return updated_count

# ===================== 关键词与分类配置（完整保留原有业务规则） =====================
KEYWORDS = [
    "合成材料跑道",
    "健身", "健身器材", "五体球",
    "体育馆", "人造草", "木质地板", "木地板",
    "合成材料面层", "塑胶跑道", "聚氨酯跑道",
    "橡胶面层运动场", "中小学合成材料",
    "人造草坪", "人造草皮", "运动场人造草", "人工草坪",
    "颗粒填充料", "草坪填充橡胶",
    "体育场馆照明", "体育照明", "运动场照明", "体育建筑电气",
    "体育木地板", "运动木地板", "体育用木质地板",
    "运动地胶", "PVC运动地板", "弹性运动地板", "卷材运动地板",
    "体育围网", "运动场围网", "球场围网", "围网",
    "室外健身器材", "健身路径", "公共健身器材", "健身步道",
    "体育器材", "学校体育器材", "体育用品",
    "游泳场地", "游泳馆", "游泳池",
    "田径场地", "田径场",
    "体育场地", "运动场地", "体育场馆",
    "体育建筑", "体育公园", "全民健身",
    "学校操场", "体育设施", "体育",
    "足球", "足球场", "足球场地",
    "篮球", "篮球场", "篮球场地",
    "网球", "网球场", "网球场地",
    "排球", "排球场地", "沙滩排球",
    "羽毛球", "羽毛球场地",
    "乒乓球", "乒乓球场地",
    "台球", "台球桌", "台球场地",
    "皮克球", "匹克球", "皮克球场地",
    "高尔夫球", "高尔夫球场",
    "保龄球", "保龄球场地",
    "橄榄球", "美式橄榄球", "橄榄球场地",
    "手球", "手球场",
    "冰球", "冰球场",
    "壁球", "壁球场地",
    "棒球", "棒球场",
    "垒球", "垒球场地",
    "曲棍球", "曲棍球场地",
    "毽球", "沙狐球", "飞镖", "射箭",
    "水球", "板球", "马球", "藤球", "门球", "地掷球",
    "武术", "散打", "跆拳道", "空手道", "柔道", "摔跤", "拳击",
    "体操", "竞技体操", "艺术体操", "蹦床",
    "田径", "跑步", "跳高", "跳远",
    "游泳", "跳水", "花样游泳",
    "赛艇", "皮划艇", "帆船", "帆板",
    "滑雪", "滑冰", "冰壶",
    "自行车", "山地自行车", "小轮车",
    "射击", "击剑", "马术", "铁人三项", "现代五项",
    "飞盘", "滑板", "攀岩", "轮滑", "钓鱼", "拔河"
]

SPORTS_TERMS = [
    "合成材料跑道",
    "健身","健身器材","五体球",
    "体育馆", "人造草", "木质地板", "木地板",
    "合成材料面层","塑胶跑道","聚氨酯跑道","橡胶面层",
    "人造草坪","人造草皮","人工草坪",
    "体育照明","运动场照明",
    "运动地胶","PVC运动地板","运动地板",
    "体育围网","运动场围网","球场围网",
    "健身路径","健身步道",
    "体育器材","体育用品",
    "体育场地","运动场地","体育场馆",
    "足球","篮球","网球","排球","羽毛球","乒乓球","田径",
    "游泳","游泳馆","游泳池",
    "体育公园","全民健身","体育设施",
    "台球","皮克球","匹克球","高尔夫球","保龄球","橄榄球","手球","冰球","壁球","棒球","垒球","曲棍球","毽球","沙狐球","飞镖","射箭",
    "水球","板球","马球","藤球","门球","地掷球","沙滩排球",
    "武术","散打","跆拳道","空手道","柔道","摔跤","拳击",
    "体操","蹦床","滑雪","滑冰","冰壶","自行车","射击","击剑","马术","飞盘","滑板","攀岩","轮滑"
]
BLACKLIST = ["电动自行车", "电动车", "电动二轮车", "电动单车"]

def is_sports(title):
    """校验是否为体育相关标准，过滤黑名单内容"""
    if not title:
        return False
    title = title.lower()
    title_clean = title.replace('　', ' ').replace('，', ',').replace('。', '.')
    for bk in BLACKLIST:
        if bk.lower() in title_clean:
            return False
    return any(term.lower() in title_clean for term in SPORTS_TERMS)

def guess_category(text):
    """标准分类自动匹配，木地板强制统一分类"""
    if not text:
        return "综合"
    cm = {
        "体育馆":"场地设计",
        "人造草":"人造草坪",
        "木质地板":"木地板",
        "体育木地板":"木地板",
        "运动木地板":"木地板",
        "体育用木质地板":"木地板",
        "木地板":"木地板",
        "合成材料跑道":"合成材料面层",
        "合成材料":"合成材料面层",
        "塑胶跑道":"合成材料面层",
        "照明":"灯光照明",
        "围网":"围网",
        "健身":"健身器材",
        "健身器材":"健身器材"
    }
    for kw, cat in cm.items():
        if kw in text:
            return cat
    return "综合"

def guess_tags(text):
    """标签自动生成，木地板强制双向标签"""
    if not text:
        return []
    tags_pool = ["体育","运动","健身","健身器材","五体球","体育馆","人造草","木地板","木质地板","塑胶","照明","围网","合成材料跑道"]
    base_tags = [t for t in tags_pool if t in text][:8]
    
    if "木地板" in text or "木质地板" in text:
        if "木地板" not in base_tags:
            base_tags.append("木地板")
        if "木质地板" not in base_tags:
            base_tags.append("木质地板")
    
    return base_tags

# ===================== 请求会话配置 =====================
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36'
def make_session():
    """创建带重试机制的请求会话"""
    s = requests.Session()
    retry = Retry(
        total=5, 
        backoff_factor=1.5, 
        status_forcelist=[429,500,502,503,504],
        allowed_methods=["GET"]
    )
    s.mount('https://', HTTPAdapter(max_retries=retry))
    s.mount('http://',  HTTPAdapter(max_retries=retry))
    s.headers.update({
        'User-Agent': UA,
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Accept': 'text/html,application/json,text/plain,*/*',
        'Referer': 'https://std.samr.gov.cn/'
    })
    try:
        s.get("https://std.samr.gov.cn/", timeout=10)
        s.get("https://openstd.samr.gov.cn/", timeout=10)
    except Exception as e:
        log(f"会话初始化警告：{str(e)}")
    return s
SESSION = make_session()

# ===================== 工具函数 =====================
def log(msg):
    """日志打印与写入"""
    ts = datetime.now().strftime('%H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception as e:
        print(f"日志写入失败：{e}")

def make_id(code):
    """生成标准唯一ID"""
    code_clean = re.sub(r'[^A-Za-z0-9]', '', code.strip().replace('　', ''))[:30]
    if code_clean:
        return code_clean
    return hashlib.md5((code or 'empty').encode()).hexdigest()[:12]

def norm_code(c):
    """标准号格式化，用于匹配去重"""
    if not c:
        return ''
    c_clean = c.replace('　', ' ').replace('－', '-').strip()
    return re.sub(r'\s+', '', c_clean).upper()

def clean_samr_code(raw):
    """标准号清洗格式化"""
    if not raw:
        return ''
    raw = re.sub(r'<[^>]+>', '', raw).strip()
    raw = re.sub(r'GB(\d)', r'GB \1', raw)
    raw = re.sub(r'GB/T', 'GB/T ', raw)
    raw = raw.replace('　', ' ').replace('－', '-')
    return re.sub(r'\s+', ' ', raw).strip()

def clean_sacinfo(raw):
    """文本内容清洗，去除HTML标签"""
    if not raw:
        return ''
    raw_clean = re.sub(r'<[^>]+>', '', raw).strip()
    return raw_clean.replace('　', ' ').strip()

def norm_status(raw):
    """标准状态格式化"""
    if not raw:
        return '现行'
    r = str(raw).strip().lower()
    if any(x in r for x in ['现行','有效']):
        return '现行'
    if any(x in r for x in ['废止','作废']):
        return '废止'
    if any(x in r for x in ['即将','待实施']):
        return '即将实施'
    return '现行'

def norm_date(raw):
    """日期格式化，统一为YYYY-MM-DD"""
    if not raw:
        return None
    s = str(raw).strip()
    s_clean = s.replace('０','0').replace('１','1').replace('２','2').replace('３','3').replace('４','4')
    s_clean = s_clean.replace('５','5').replace('６','6').replace('７','7').replace('８','8').replace('９','9')
    s_clean = s_clean.replace('－', '-').replace('　', ' ')
    d = re.sub(r'[^\d]', '', s_clean)
    if len(d)>=8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return None

def is_mandatory(code):
    """判断是否为强制性标准"""
    c = norm_code(code)
    if not c:
        return False
    if re.match(r'^GB\d', c) and '/T' not in c:
        return True
    if c.startswith('JGJ'):
        return True
    return False

def guess_type(code):
    """判断标准类型"""
    cu = norm_code(code)
    if not cu:
        return "国家标准"
    type_map = [
        ("GB/T","国家标准"),
        ("GB","国家标准"),
        ("JGJ","行业标准"),
        ("JG/T","行业标准"),
        ("CJJ","行业标准"),
        ("T/","团体标准"),
        ("DB","地方标准")
    ]
    for p,t in type_map:
        if cu.startswith(norm_code(p)):
            return t
    return "国家标准"

# ===================== 详情页精准抓取：发布机构+基础信息，不碰替代内容 =====================
def fetch_detail_accurate_info(std_id, domain):
    """
    详情页抓取：优先获取官方发布机构原文，确保发布机构100%准确
    辅助抓取：实施日期、标准摘要，彻底不抓取替代相关内容
    """
    if not std_id or not domain:
        return None, None, None
    try:
        url = f"{domain}/gb/search/gbDetailed?id={std_id}"
        resp = SESSION.get(url, timeout=20)
        resp.raise_for_status()
        html = resp.text
        
        # 优先抓取详情页官方发布机构（最准确）
        issued_by = None
        issued_by_match = re.search(r'(?:发布机构|发布单位|发布部门)[^：:]*[：:]\s*([^\n<]{3,200})', html)
        if issued_by_match:
            issued_by_raw = clean_sacinfo(issued_by_match.group(1))
            if issued_by_raw and len(issued_by_raw) > 2:
                issued_by = issued_by_raw
        
        # 抓取实施日期
        impl_date = None
        impl_match = re.search(r'实施日期[^：:]*[：:]\s*(\d{4}[-\s]?\d{2}[-\s]?\d{2})', html)
        if impl_match:
            impl_date = norm_date(impl_match.group(1))
        
        # 抓取标准摘要
        summary = None
        summary_match = re.search(r'标准摘要[^：:]*[：:]\s*([^<]{10,600})', html)
        if summary_match:
            summary = clean_sacinfo(summary_match.group(1)).strip()
        
        return issued_by, impl_date, summary
    except Exception as e:
        if DEBUG_MODE:
            log(f"详情页抓取失败 {std_id}@{domain}：{str(e)}")
        return None, None, None

# ===================== 【新增】标准号精准查询：用于全库扫描单个标准 =====================
def query_std_by_code(code):
    """
    用标准号精准查询国标委官网，获取最新状态、替代信息、发布机构
    用于全库扫描，支持手动新增的标准查询
    返回：标准最新信息字典，查询失败返回None
    """
    if not is_legal_std_code(code):
        log(f"跳过非法标准号：{code}")
        return None
    
    code_clean = clean_samr_code(code)
    domains = ["https://std.samr.gov.cn", "https://openstd.samr.gov.cn"]
    for domain in domains:
        try:
            # 精准搜索标准号
            resp = SESSION.get(
                f"{domain}/gb/search/gbQueryPage",
                params={
                    "searchText": code_clean,
                    "status": "",
                    "pageSize": 10,
                    "pageIndex": 1
                },
                headers={
                    "Referer": f"{domain}/gb/search",
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*"
                },
                timeout=20)
            resp.raise_for_status()
            if 'html' in resp.headers.get('content-type','').lower():
                continue
            data = resp.json()
            rows = data.get('rows', [])
            if not rows:
                continue
            
            # 精准匹配标准号，避免模糊匹配
            target_row = None
            for row in rows:
                row_code = clean_samr_code(row.get('C_STD_CODE') or row.get('STD_CODE') or '')
                if norm_code(row_code) == norm_code(code_clean):
                    target_row = row
                    break
            if not target_row:
                continue
            
            # 解析基础信息
            latest_status = norm_status(target_row.get('STATE') or target_row.get('STD_STATUS'))
            issue_date = norm_date(target_row.get('ISSUE_DATE'))
            impl_date = norm_date(target_row.get('IMPL_DATE'))
            abolish_date = norm_date(target_row.get('ABOL_DATE'))
            std_id = target_row.get('id') or target_row.get('ID') or ''

            # 详情页补充精准信息
            detail_issued_by, detail_impl_date, _ = fetch_detail_accurate_info(std_id, domain)
            if detail_impl_date:
                impl_date = detail_impl_date

            # 发布机构处理
            dept1 = str(target_row.get('ISSUE_DEPT') or '').strip()
            dept2 = str(target_row.get('ISSUE_UNIT') or '').strip()
            list_issued_by = f"{dept1}、{dept2}" if (dept1 and dept2 and dept2 != dept1) else (dept1 or dept2)
            issued_by = detail_issued_by if detail_issued_by else list_issued_by

            # 精准抓取替代信息（仅被替代号，用于废止标准）
            replaced_by = None
            if std_id:
                try:
                    detail_url = f"{domain}/gb/search/gbDetailed?id={std_id}"
                    detail_resp = SESSION.get(detail_url, timeout=20)
                    detail_resp.raise_for_status()
                    detail_html = detail_resp.text
                    # 仅抓取被替代信息，过滤无效ID
                    replaced_match = re.search(r'被.*代替[^：:]*[：:]\s*([^\n<]{5,150})', detail_html)
                    if replaced_match:
                        replaced_by = clean_std_code_field(replaced_match.group(1), code_clean)
                except:
                    pass

            return {
                "code": code_clean,
                "status": latest_status,
                "issueDate": issue_date,
                "implementDate": impl_date,
                "abolishDate": abolish_date,
                "issuedBy": issued_by,
                "replacedBy": replaced_by
            }
        except Exception as e:
            if DEBUG_MODE:
                log(f"标准查询失败 {code} @{domain}：{str(e)}")
            continue
    log(f"标准未查询到官网信息：{code}")
    return None

# ===================== 【新增】全库扫描核心函数 =====================
def full_library_scan(standards):
    """
    全库扫描核心逻辑：
    1. 遍历库内所有标准（含手动新增/修改的）
    2. 逐个查询官网最新状态，已废止的强制更新状态
    3. 废止标准自动补充替代标准号
    4. 严格保护手动修改的非状态/非替代字段
    """
    log("="*50)
    log("开始全库标准状态扫描...")
    total_std = len(standards)
    updated_count = 0
    error_count = 0

    for index, std in enumerate(standards, 1):
        code = std.get('code', '')
        old_status = std.get('status', '现行')
        log(f"[{index}/{total_std}] 正在扫描：{code}")

        # 查询官网最新信息
        latest_info = query_std_by_code(code)
        if not latest_info:
            error_count += 1
            time.sleep(0.5)
            continue

        latest_status = latest_info.get('status', '现行')
        # 【核心规则】无论手动/自动，只要官网已废止，强制更新状态
        if latest_status == '废止' and old_status != '废止':
            std['status'] = '废止'
            log(f"  → 状态更新：{old_status} → 废止")
            updated_count += 1

        # 【核心规则】废止标准，补充/更新替代标准号（无手动修改时）
        if latest_status == '废止':
            latest_replacedBy = latest_info.get('replacedBy')
            if latest_replacedBy and not std.get('replacedBy'):
                std['replacedBy'] = latest_replacedBy
                log(f"  → 补充替代标准：{latest_replacedBy}")
                updated_count += 1

        # 【保护规则】非状态/非替代字段，仅当用户无手动修改时才更新
        for field in ['issueDate', 'implementDate', 'abolishDate', 'issuedBy']:
            latest_val = latest_info.get(field)
            if latest_val and not std.get(field):
                std[field] = latest_val
                updated_count += 1

        # 请求间隔，避免被封IP
        time.sleep(0.8)

    log(f"全库扫描完成：总计{total_std}条，更新{updated_count}条，查询失败{error_count}条")
    log("="*50)
    return standards, updated_count

# ===================== 抓取接口：严格保护手动修改，仅废止状态强制覆盖 =====================
def fetch_samr(keyword, page=1):
    """关键词抓取接口，严格遵守手动保护规则"""
    results = []
    total_pages = 1
    domains = ["https://std.samr.gov.cn", "https://openstd.samr.gov.cn"]
    for domain in domains:
        try:
            resp = SESSION.get(
                f"{domain}/gb/search/gbQueryPage",
                params={
                    "searchText": keyword,
                    "status": "",
                    "sortField": "ISSUE_DATE",
                    "sortType": "desc",
                    "pageSize": 50,
                    "pageIndex": page
                },
                headers={
                    "Referer": f"{domain}/gb/search",
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*"
                },
                timeout=20)
            resp.raise_for_status()
            if 'html' in resp.headers.get('content-type','').lower():
                continue
            data = resp.json()
            rows = data.get('rows', [])
            total = int(data.get('total',0))
            total_pages = max(1, (total+49)//50)
            for row in rows:
                code = clean_samr_code(row.get('C_STD_CODE') or row.get('STD_CODE') or '')
                title = clean_sacinfo(row.get('C_C_NAME') or row.get('STD_NAME') or '')
                if not code or not title or not is_sports(title):
                    continue
                
                # 基础字段抓取
                issue_date = norm_date(row.get('ISSUE_DATE'))
                list_impl_date = norm_date(row.get('IMPL_DATE'))
                std_id = row.get('id') or row.get('ID') or ''
                status = norm_status(row.get('STATE') or row.get('STD_STATUS'))

                # 彻底清空替代旧标准字段
                replaces = None
                replacedBy = None

                # 详情页补充精准信息
                detail_issued_by, detail_impl_date, detail_summary = fetch_detail_accurate_info(std_id, domain)
                impl_date = detail_impl_date if detail_impl_date else list_impl_date
                summary = detail_summary if detail_summary else ''

                # 发布机构处理
                list_dept1 = str(row.get('ISSUE_DEPT') or '').strip()
                list_dept2 = str(row.get('ISSUE_UNIT') or '').strip()
                list_issued_by = f"{list_dept1}、{list_dept2}" if (list_dept1 and list_dept2 and list_dept2 != list_dept1) else (list_dept1 or list_dept2)
                issued_by = detail_issued_by if detail_issued_by else list_issued_by

                # 现行/即将实施标准，清空被替代字段
                if status in ['现行', '即将实施']:
                    replacedBy = None

                results.append({
                    "code": code, 
                    "title": title, 
                    "status": status,
                    "issueDate": issue_date, 
                    "implementDate": impl_date, 
                    "abolishDate": norm_date(row.get('ABOL_DATE')),
                    "issuedBy": issued_by, 
                    "replaces": replaces, 
                    "replacedBy": replacedBy, 
                    "summary": summary,
                    "isMandatory": is_mandatory(code)
                })
            break
        except Exception as e:
            if DEBUG_MODE:
                log(f"抓取失败 {domain} 关键词:{keyword} 页码:{page}：{str(e)}")
            continue
    return results, total_pages

def fetch_samr_all(keyword):
    """全量关键词分页抓取，单关键词最多50页"""
    all_res = []
    seen = set()
    try:
        res, tp = fetch_samr(keyword, 1)
        for r in res:
            nc = norm_code(r['code'])
            if nc not in seen:
                seen.add(nc)
                all_res.append(r)
    except Exception as e:
        log(f"关键词 {keyword} 初始页抓取失败：{str(e)}")
        return all_res
    max_page = 50
    for p in range(2, min(tp+1, max_page+1)):
        try:
            time.sleep(0.8)
            res,_ = fetch_samr(keyword, p)
            if not res:
                break
            for r in res:
                nc = norm_code(r['code'])
                if nc not in seen:
                    seen.add(nc)
                    all_res.append(r)
        except Exception as e:
            if DEBUG_MODE:
                log(f"关键词 {keyword} 页码 {p} 抓取失败：{str(e)}")
            continue
    return all_res

# ===================== 合并逻辑：严格遵守手动保护边界 =====================
def merge(existing, new_items):
    """
    合并新旧数据，核心保护规则：
    1. 非状态字段：用户手动修改后，永久不覆盖
    2. 状态字段：仅当新状态是「废止」时，强制覆盖；其他状态保护手动修改
    3. 替代字段：仅用户无手动修改时，才更新合法内容
    """
    existing_code_map = { norm_code(s['code']): s for s in existing }
    add, upd = 0, 0
    for item in new_items:
        code_raw = item.get('code', '')
        nc = norm_code(code_raw)
        if not nc:
            continue
        if nc in existing_code_map:
            old = existing_code_map[nc]
            new_status = item.get('status')
            old_status = old.get('status')

            # 【核心例外规则】新状态是废止，强制覆盖，无论手动/自动
            if new_status == '废止' and old_status != '废止':
                old['status'] = '废止'
                upd +=1
            # 【保护规则】新状态是现行/即将实施，仅当用户无手动修改时才更新
            elif new_status in ['现行', '即将实施'] and not old_status:
                old['status'] = new_status
                upd +=1

            # 【强制规则】现行/即将实施标准，永久清空被替代字段
            current_status = old.get('status')
            if current_status in ['现行', '即将实施']:
                if old.get('replacedBy'):
                    old['replacedBy'] = None
                    upd +=1

            # 【强制规则】彻底清空替代旧标准字段
            if old.get('replaces'):
                old['replaces'] = None
                upd +=1

            # 【保护规则】被替代字段：仅用户无手动修改时，才更新合法内容
            new_replacedBy = item.get('replacedBy')
            if new_replacedBy and not old.get('replacedBy'):
                legal_val = clean_std_code_field(new_replacedBy, old.get('code', ''))
                if legal_val:
                    old['replacedBy'] = legal_val
                    upd +=1

            # 【保护规则】其他基础字段：仅用户无手动修改时，才更新
            for f in ['issueDate','implementDate','abolishDate','summary','issuedBy','isMandatory']:
                val = item.get(f)
                if val and not old.get(f):
                    old[f] = val
                    upd +=1
        else:
            new_entry = build_entry(item)
            existing.append(new_entry)
            add +=1

    # 强制去重，同一标准号仅保留1条，优先现行版本
    final_standards = []
    code_set = set()
    for s in existing:
        nc = norm_code(s['code'])
        if not nc:
            continue
        if nc not in code_set:
            code_set.add(nc)
            final_standards.append(s)
        else:
            for i, exist_s in enumerate(final_standards):
                if norm_code(exist_s['code']) == nc:
                    if s.get('status') == '现行' and exist_s.get('status') != '现行':
                        final_standards[i] = s
                        upd +=1
                    break
    return final_standards, add, upd

def build_entry(item):
    """构建标准入库条目，执行最终强制规则"""
    code = item.get('code','')
    title = clean_sacinfo(item.get('title',''))
    status = item.get('status','现行')
    replacedBy = item.get('replacedBy')

    # 入库前最终校验
    replacedBy = clean_std_code_field(replacedBy, code)
    # 强制规则1：现行/即将实施标准，永久清空被替代字段
    if status in ['现行', '即将实施']:
        replacedBy = None
    # 强制规则2：彻底清空替代旧标准字段
    replaces = None

    return {
        'id': make_id(code), 
        'code': code, 
        'title': title, 
        'english': '',
        'type': item.get('type') or guess_type(code),
        'status': status,
        'issueDate': item.get('issueDate'), 
        'implementDate': item.get('implementDate'),
        'abolishDate': item.get('abolishDate'), 
        'replaces': replaces,
        'replacedBy': replacedBy, 
        'issuedBy': item.get('issuedBy',''),
        'category': guess_category(title), 
        'tags': guess_tags(title),
        'summary': item.get('summary',''), 
        'isMandatory': is_mandatory(code),
        'scope': '', 
        'localFile': None
    }

# ===================== 数据库读写函数 =====================
def load_db():
    """加载标准库"""
    if not DATA_FILE.exists():
        log("新建标准库")
        return {'standards':[]}, []
    try:
        with open(DATA_FILE,'r',encoding='utf-8') as f:
            db = json.load(f)
        if 'standards' not in db:
            db['standards'] = []
        return db, db['standards']
    except Exception as e:
        log(f"加载标准库失败，新建空库：{str(e)}")
        return {'standards':[]}, []

def save_db(db, standards, dry):
    """保存标准库，执行最终全量规则校验"""
    # 过滤非体育内容
    before = len(standards)
    standards = [s for s in standards if is_sports(clean_sacinfo(s.get('title','')))]
    removed = before - len(standards)
    if removed > 0:
        log(f"🗑️ 自动清理：移除 {removed} 条非体育/重复标准")
    
    # 执行最终核心规则校验
    log("🔧 执行最终全量核心规则校验...")
    final_repair_count = auto_fix_std_core_rules(standards)
    log(f"✅ 最终规则校验完成：共修正 {final_repair_count} 条字段")

    db['standards'] = standards
    db['updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db['total'] = len(standards)
    if dry:
        log(f"预览模式：共{len(standards)}条，不保存到文件")
        return
    try:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DATA_FILE,'w',encoding='utf-8') as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
        log(f"✅ 保存成功：最终标准库共{len(standards)}条")
    except Exception as e:
        log(f"❌ 保存失败：{str(e)}")

# ===================== 主运行函数 =====================
def run(dry=False, debug=False, scan_only=False, repair_only=False, schedule_hours=0):
    """
    主运行函数，支持多种模式
    :param dry: 预览模式，执行但不保存文件
    :param debug: 调试模式，输出详细日志
    :param scan_only: 仅执行全库扫描，不执行关键词抓取
    :param repair_only: 仅执行本地规则修复，不抓取/不扫描
    :param schedule_hours: 定时扫描间隔小时数，0为不执行定时
    """
    global DEBUG_MODE
    DEBUG_MODE = debug
    log("="*60)
    log("体育标准抓取工具 v26（全需求合规终版）")
    log("核心规则：手动修改保护+废止状态强制修正+全库定时扫描")
    log("="*60)

    # 定时模式循环执行
    while True:
        db, standards = load_db()
        log(f"当前标准库已有：{len(standards)} 条标准")

        # 仅本地规则修复模式
        if repair_only:
            log("\n=== 执行本地规则修复 ===")
            save_db(db, standards, dry)
            if schedule_hours <= 0:
                break
            log(f"定时模式：{schedule_hours}小时后再次执行")
            time.sleep(schedule_hours * 3600)
            continue

        # 仅全库扫描模式
        if scan_only:
            log("\n=== 执行全库标准扫描 ===")
            standards, _ = full_library_scan(standards)
            save_db(db, standards, dry)
            if schedule_hours <= 0:
                break
            log(f"定时模式：{schedule_hours}小时后再次执行")
            time.sleep(schedule_hours * 3600)
            continue

        # 全量关键词抓取+合并模式
        log("\n=== 开始全量关键词抓取 ===")
        all_new = []
        total_kw = len(KEYWORDS)
        for i, kw in enumerate(KEYWORDS,1):
            log(f"[{i}/{total_kw}] 正在抓取关键词: {kw}")
            try:
                res = fetch_samr_all(kw)
                log(f"   → 抓取到 {len(res)} 条有效标准")
                all_new.extend(res)
                time.sleep(1)
            except Exception as e:
                log(f"   → 抓取失败：{str(e)}")
                time.sleep(2)
                continue
        log(f"\n抓取完成，去重前总数：{len(all_new)} 条")
        standards, add, upd = merge(standards, all_new)
        log(f"合并结果：新增 {add} 条，更新 {upd} 条基础信息")

        # 抓取完成后执行全库扫描
        standards, _ = full_library_scan(standards)
        save_db(db, standards, dry)

        # 非定时模式，执行完退出
        if schedule_hours <= 0:
            break
        log(f"定时模式：{schedule_hours}小时后再次执行全流程")
        time.sleep(schedule_hours * 3600)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry', action='store_true', help='预览模式，执行但不保存文件')
    parser.add_argument('--debug', action='store_true', help='调试模式，输出详细日志')
    parser.add_argument('--scan-only', action='store_true', help='仅执行全库扫描，不抓取新数据')
    parser.add_argument('--repair-only', action='store_true', help='仅执行本地规则修复，不抓取/不扫描')
    parser.add_argument('--schedule', type=int, default=0, help='定时执行间隔小时数，例如--schedule 24 为每天执行一次')
    args = parser.parse_args()
    run(
        dry=args.dry, 
        debug=args.debug, 
        scan_only=args.scan_only, 
        repair_only=args.repair_only, 
        schedule_hours=args.schedule
    )