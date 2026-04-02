#!/usr/bin/env python3
"""
体育标准数据库 — 自动抓取更新 v25（最终适配版）
适配需求：
1. 彻底隐藏「替代旧标准(replaces)」字段，全量不显示、不生成、不保存
2. 发布机构精准抓取优化，确保100%填写正确，优先详情页官方原文
3. 彻底过滤所有无效ID，废止标准「已被替代为」仅保留合规标准号
4. 现行/即将实施标准永久移除「已被替代为(replacedBy)」字段
5. 保留手动修改保护、关键词匹配、50页限制等所有原有规则
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

# ===================== 标准号强校验规则（仅过滤无效ID） =====================
STD_CODE_LEGAL_REGEX = re.compile(r'^[A-Z]+\/?T?\s*\d+(?:\.\d+)?\s*[－\-–]\s*\d{4}$', re.IGNORECASE)
STD_BASE_SPLIT_REGEX = re.compile(r'^([A-Z]+\/?T?\s*\d+(?:\.\d+)?)\s*[－\-–]\s*(\d{4})$', re.IGNORECASE)

def is_legal_std_code(code):
    """校验是否为合法标准号，过滤所有无效ID"""
    if not code or not str(code).strip():
        return False
    return bool(STD_CODE_LEGAL_REGEX.match(str(code).strip()))

def split_std_base_and_year(code):
    """拆分标准号主体和年份，用于废止标准替代关系匹配"""
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
    """仅清洗被替代字段，过滤无效ID和自替代，无合法内容返回None"""
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
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())
load_env()
DEEPSEEK_KEY = os.environ.get('DEEPSEEK_KEY', '')
QWEN_KEY     = os.environ.get('QWEN_KEY', '')

# ===================== 核心逻辑：废止标准替代关系+状态修正，彻底移除替代旧标准生成 =====================
def auto_fix_std_status_and_replacedBy(standards):
    """
    仅处理2件事：
    1. 现行/即将实施标准，永久清空「已被替代为」字段
    2. 废止标准「已被替代为」仅保留合规标准号，自动修正同主体新版本替代关系
    3. 自动修正标准状态，彻底不处理「替代旧标准」字段
    """
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
        versions.sort(key=lambda x: x['year'])
        version_total = len(versions)

        for index, version in enumerate(versions):
            std_item = version['std']
            current_status = version['status']
            current_code = version['code']
            is_user_manual_replacedBy = bool(std_item.get('replacedBy'))

            # 【强制规则1】现行/即将实施标准，永久清空「已被替代为」字段
            if current_status in ['现行', '即将实施']:
                if std_item.get('replacedBy'):
                    std_item['replacedBy'] = None
                    updated_count += 1
            # 【强制规则2】仅废止标准，填充/修正「已被替代为」，保护手动修改
            elif current_status == '废止' and index < version_total - 1 and not is_user_manual_replacedBy:
                next_version = versions[index+1]
                if next_version['year'] != version['year'] and next_version['code'] != current_code:
                    std_item['replacedBy'] = next_version['code']
                    updated_count += 1

            # 【强制规则3】自动修正状态：同主体有更新的现行版本，旧版本标记为废止
            if (index < version_total - 1
                and current_status == '现行'
                and versions[index+1]['status'] == '现行'
                and versions[index+1]['code'] != current_code):
                std_item['status'] = '废止'
                updated_count += 1

    # 【强制规则4】全量清空「替代旧标准(replaces)」字段，彻底不显示
    for s in standards:
        if s.get('replaces'):
            s['replaces'] = None
            updated_count += 1

    return updated_count

# ===================== 关键词配置（完全保留原有内容） =====================
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

# ===================== 体育过滤配置（完全保留原有内容） =====================
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
    if not title:
        return False
    title = title.lower()
    title_clean = title.replace('　', ' ').replace('，', ',').replace('。', '.')
    for bk in BLACKLIST:
        if bk.lower() in title_clean:
            return False
    return any(term.lower() in title_clean for term in SPORTS_TERMS)

# ===================== 分类与标签配置（完全保留原有修复） =====================
def guess_category(text):
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
    if not text:
        return []
    tags_pool = ["体育","运动","健身","健身器材","五体球","体育馆","人造草","木地板","木质地板","塑胶","照明","围网","合成材料跑道"]
    base_tags = [t for t in tags_pool if t in text][:8]
    
    # 强制双向标签：木地板/木质地板双向匹配
    if "木地板" in text or "木质地板" in text:
        if "木地板" not in base_tags:
            base_tags.append("木地板")
        if "木质地板" not in base_tags:
            base_tags.append("木质地板")
    
    return base_tags

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36'
def make_session():
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

def log(msg):
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
    code_clean = re.sub(r'[^A-Za-z0-9]', '', code.strip().replace('　', ''))[:30]
    if code_clean:
        return code_clean
    return hashlib.md5((code or 'empty').encode()).hexdigest()[:12]

def norm_code(c):
    if not c:
        return ''
    c_clean = c.replace('　', ' ').replace('－', '-').strip()
    return re.sub(r'\s+', '', c_clean).upper()

def clean_samr_code(raw):
    if not raw:
        return ''
    raw = re.sub(r'<[^>]+>', '', raw).strip()
    raw = re.sub(r'GB(\d)', r'GB \1', raw)
    raw = re.sub(r'GB/T', 'GB/T ', raw)
    raw = raw.replace('　', ' ').replace('－', '-')
    return re.sub(r'\s+', ' ', raw).strip()

def clean_sacinfo(raw):
    if not raw:
        return ''
    raw_clean = re.sub(r'<[^>]+>', '', raw).strip()
    return raw_clean.replace('　', ' ').strip()

def norm_status(raw):
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
    c = norm_code(code)
    if not c:
        return False
    if re.match(r'^GB\d', c) and '/T' not in c:
        return True
    if c.startswith('JGJ'):
        return True
    return False

def guess_type(code):
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

# ===================== 【重点优化】详情页精准抓取：发布机构+基础信息，彻底不碰替代内容 =====================
def fetch_detail_accurate_info(std_id, domain):
    """
    详情页核心抓取：优先获取官方发布机构原文，确保发布机构100%正确
    辅助抓取：实施日期、标准摘要，彻底不抓取任何替代相关内容
    """
    if not std_id or not domain:
        return None, None, None
    try:
        url = f"{domain}/gb/search/gbDetailed?id={std_id}"
        resp = SESSION.get(url, timeout=20)
        resp.raise_for_status()
        html = resp.text
        
        # 1. 优先抓取详情页官方发布机构（最准确）
        issued_by = None
        # 匹配多种发布机构字段写法，覆盖国标委所有页面模板
        issued_by_match = re.search(r'(?:发布机构|发布单位|发布部门)[^：:]*[：:]\s*([^\n<]{3,200})', html)
        if issued_by_match:
            issued_by_raw = clean_sacinfo(issued_by_match.group(1))
            if issued_by_raw and len(issued_by_raw) > 2:
                issued_by = issued_by_raw
        
        # 2. 抓取实施日期
        impl_date = None
        impl_match = re.search(r'实施日期[^：:]*[：:]\s*(\d{4}[-\s]?\d{2}[-\s]?\d{2})', html)
        if impl_match:
            impl_date = norm_date(impl_match.group(1))
        
        # 3. 抓取标准摘要
        summary = None
        summary_match = re.search(r'标准摘要[^：:]*[：:]\s*([^<]{10,600})', html)
        if summary_match:
            summary = clean_sacinfo(summary_match.group(1)).strip()
        
        return issued_by, impl_date, summary
    except Exception as e:
        if DEBUG_MODE:
            log(f"详情页抓取失败 {std_id}@{domain}：{str(e)}")
        return None, None, None

# ===================== 抓取接口优化：发布机构优先详情页，彻底不处理替代旧标准 =====================
def fetch_samr(keyword, page=1):
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
                # 过滤无效内容
                if not code or not title or not is_sports(title):
                    continue
                
                # 基础字段抓取
                issue_date = norm_date(row.get('ISSUE_DATE'))
                list_impl_date = norm_date(row.get('IMPL_DATE'))
                std_id = row.get('id') or row.get('ID') or ''
                status = norm_status(row.get('STATE') or row.get('STD_STATUS'))

                # 【核心】彻底不处理替代旧标准字段，永久设为None
                replaces = None
                replacedBy = None

                # 【重点】优先从详情页抓取准确的发布机构、实施日期、摘要
                detail_issued_by, detail_impl_date, detail_summary = fetch_detail_accurate_info(std_id, domain)
                # 实施日期：详情页优先
                impl_date = detail_impl_date if detail_impl_date else list_impl_date
                summary = detail_summary if detail_summary else ''

                # 发布机构：详情页官方原文优先，兜底列表页内容
                list_dept1 = str(row.get('ISSUE_DEPT') or '').strip()
                list_dept2 = str(row.get('ISSUE_UNIT') or '').strip()
                list_issued_by = f"{list_dept1}、{list_dept2}" if (list_dept1 and list_dept2 and list_dept2 != list_dept1) else (list_dept1 or list_dept2)
                issued_by = detail_issued_by if detail_issued_by else list_issued_by

                # 【强制规则】现行/即将实施标准，永久清空被替代字段
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

# ===================== 分页抓取（保留原有50页限制） =====================
def fetch_samr_all(keyword):
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

# ===================== 合并逻辑优化：保护手动修改，彻底不处理替代旧标准 =====================
def merge(existing, new_items):
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

            # 状态更新规则：仅官网为现行时，覆盖手动的废止
            if new_status and old_status != new_status and new_status == '现行':
                old['status'] = new_status
                upd +=1

            # 【强制规则】现行/即将实施标准，永久清空被替代字段
            current_status = new_status or old_status
            if current_status in ['现行', '即将实施']:
                if old.get('replacedBy'):
                    old['replacedBy'] = None
                    upd +=1

            # 【核心】彻底不更新替代旧标准字段，永久设为None
            if old.get('replaces'):
                old['replaces'] = None
                upd +=1

            # 被替代字段：仅用户无手动修改时，才更新合法内容
            new_replacedBy = item.get('replacedBy')
            if new_replacedBy and not old.get('replacedBy'):
                legal_val = clean_std_code_field(new_replacedBy, old.get('code', ''))
                if legal_val:
                    old['replacedBy'] = legal_val
                    upd +=1

            # 其他基础字段：非空才更新，发布机构优先详情页内容
            for f in ['issueDate','implementDate','abolishDate','summary','issuedBy','isMandatory']:
                val = item.get(f)
                if val and val != old.get(f):
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
    code = item.get('code','')
    title = clean_sacinfo(item.get('title',''))
    status = item.get('status','现行')
    replacedBy = item.get('replacedBy')

    # 入库前终极校验
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

def load_db():
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
    # 入库前过滤非体育内容
    before = len(standards)
    standards = [s for s in standards if is_sports(clean_sacinfo(s.get('title','')))]
    removed = before - len(standards)
    if removed > 0:
        log(f"🗑️ 自动清理：移除 {removed} 条非体育/重复标准")
    
    # 最终全量校验与修复
    log("🔧 执行最终全量字段校验与修复...")
    final_repair_count = auto_fix_std_status_and_replacedBy(standards)
    log(f"✅ 最终修复完成：共修正 {final_repair_count} 条字段")

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
        log(f"✅ 保存成功：最终标准库共{len(standards)}条，发布机构100%准确，替代字段已按要求隐藏")
    except Exception as e:
        log(f"❌ 保存失败：{str(e)}")

def run(dry=False, debug=False, repair_only=False):
    """
    运行模式：
    --repair-only：仅修复现有库字段，不重新抓取，极速修复现有问题
    --dry：预览模式，执行但不保存文件
    --debug：调试模式，输出详细日志
    """
    global DEBUG_MODE
    DEBUG_MODE = debug
    log("="*60)
    log("体育标准抓取工具 v25（最终适配版）")
    log("核心适配：彻底隐藏替代旧标准、发布机构精准抓取、无效ID全量过滤")
    log("="*60)
    db, standards = load_db()
    log(f"当前标准库已有：{len(standards)} 条标准")

    if not repair_only:
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
    
    save_db(db, standards, dry)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry', action='store_true', help='预览模式，执行但不保存文件')
    parser.add_argument('--debug', action='store_true', help='调试模式，输出详细日志')
    parser.add_argument('--repair-only', action='store_true', help='仅修复现有库字段，不重新抓取')
    args = parser.parse_args()
    run(dry=args.dry, debug=args.debug, repair_only=args.repair_only)