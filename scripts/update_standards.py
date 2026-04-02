#!/usr/bin/env python3
"""
体育标准数据库 — 自动抓取更新 v23（替代关系专项修复版）
修复内容：
1. 彻底解决「替代标准/被替代标准填充TC198/F772等无效ID」问题，新增严格标准号格式校验
2. 强制规则：现行/即将实施标准，彻底移除「已被替代为(replacedBy)」项，仅保留替代旧标准字段
3. 废止标准「已被替代为」字段强制清洗，仅保留合规标准号，杜绝无效内容
4. 彻底杜绝自替代问题，自动过滤自身标准号
5. 优化详情页抓取逻辑，避免误抓页面提示文本中的非标准号内容
6. 兜底填充逻辑新增合规校验，不生成无效替代关系
7. 保留手动修改保护、发布单位原文、50页限制等所有原有规则
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

# ===================== 新增修复1：严格标准号格式校验与清洗核心函数 =====================
# 合规标准号正则：支持GB/T、GB、JGJ、JG/T、CJJ、T/、DB开头的标准号格式
STD_CODE_REGEX = re.compile(r'[A-Z]+\/?T?\s*\d+(?:\.\d+)?\s*[－\-–]\s*\d{4}', re.IGNORECASE)
# 标准号主体+年份拆分正则，用于自替代校验
STD_BASE_SPLIT_REGEX = re.compile(r'^([A-Z]+\/?T?\s*\d+(?:\.\d+)?)\s*[－\-–]\s*(\d{4})$', re.IGNORECASE)

def clean_valid_std_codes(raw_content, self_code=''):
    """
    清洗替代字段内容，仅保留合规的标准号，过滤TC198/F772等无效ID
    :param raw_content: 抓取到的原始替代内容
    :param self_code: 当前标准号，用于过滤自替代
    :return: 清洗后的合规标准号字符串，无有效内容返回None
    """
    if not raw_content:
        return None
    # 提取所有符合标准号格式的内容
    valid_codes = STD_CODE_REGEX.findall(str(raw_content))
    if not valid_codes:
        return None
    
    # 格式化清洗每个标准号
    cleaned_codes = []
    self_code_norm = norm_code(self_code)
    for code in valid_codes:
        code_clean = clean_samr_code(code)
        code_norm = norm_code(code_clean)
        # 过滤自替代、空内容
        if not code_norm or code_norm == self_code_norm:
            continue
        cleaned_codes.append(code_clean)
    
    # 去重后返回，无有效内容返回None
    cleaned_codes = list(dict.fromkeys(cleaned_codes))
    return '；'.join(cleaned_codes) if cleaned_codes else None

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

# ===================== 修复2：替代关系兜底填充，新增合规校验，杜绝无效内容 =====================
def auto_fill_replaces(standards):
    """
    仅兜底填充：只有官网没抓到真实替代号时，才自动补全
    严格校验同标准号不同年份，彻底杜绝错误替代、自替代、无效内容
    """
    groups = {}
    for s in standards:
        code = s.get('code', '')
        # 严格拆分【标准号主体+4位年份】，只匹配合规格式的标准号
        m = STD_BASE_SPLIT_REGEX.match(code.strip())
        if m:
            base = re.sub(r'\s+', '', m.group(1)).upper()
            try:
                year = int(m.group(2))
            except:
                continue
            if base not in groups:
                groups[base] = []
            groups[base].append({'std': s, 'year': year, 'code': code, 'code_norm': norm_code(code)})
    
    updated = 0
    for base, versions in groups.items():
        if len(versions) < 2:
            continue
        # 严格按年份升序排序，旧版本在前，新版本在后
        versions.sort(key=lambda x: x['year'])
        version_count = len(versions)
        
        for i, ver in enumerate(versions):
            s = ver['std']
            current_status = s.get('status', '现行')
            current_code = ver['code']
            current_code_norm = ver['code_norm']

            # 只有官网没抓到真实替代号，才自动填充替代旧标准
            if i > 0 and not s.get('replaces'):
                prev_ver = versions[i-1]
                # 严格校验：年份不同、标准主体完全一致、不是自身，才生成替代关系
                if (prev_ver['year'] != ver['year'] 
                    and prev_ver['code_norm'] != current_code_norm):
                    s['replaces'] = prev_ver['code']
                    updated += 1

            # 【核心规则】仅废止标准才填充被替代号，现行/即将实施强制清空
            if current_status == '废止' and i < version_count - 1 and not s.get('replacedBy'):
                next_ver = versions[i+1]
                if (next_ver['year'] != ver['year'] 
                    and next_ver['code_norm'] != current_code_norm):
                    s['replacedBy'] = next_ver['code']
                    updated += 1
            elif current_status in ['现行', '即将实施']:
                # 强制清空现行/即将实施标准的被替代字段，彻底去掉「已被替代为」项
                if s.get('replacedBy'):
                    s['replacedBy'] = None
                    updated += 1

            # 只有新版本是现行，才标记旧版本为废止
            if (i < version_count - 1 
                and s.get('status') == '现行' 
                and versions[i+1]['std'].get('status') == '现行'
                and versions[i+1]['code_norm'] != current_code_norm):
                s['status'] = '废止'
                updated += 1
    return updated

# ===================== 关键词配置（保留原有新增内容） =====================
KEYWORDS = [
    # 新增关键词
    "合成材料跑道",
    # 原有核心关键词
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

# ===================== 体育过滤配置（保留原有新增内容） =====================
SPORTS_TERMS = [
    # 新增术语
    "合成材料跑道",
    # 原有术语
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

# ===================== 分类与标签配置（保留原有修复） =====================
def guess_category(text):
    """所有木质地板相关，强制归为「木地板」，合成材料跑道归为合成材料面层"""
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
    """所有木地板/木质地板相关，强制加上双向标签，确保搜索全匹配"""
    if not text:
        return []
    tags_pool = ["体育","运动","健身","健身器材","五体球","体育馆","人造草","木地板","木质地板","塑胶","照明","围网","合成材料跑道"]
    base_tags = [t for t in tags_pool if t in text][:8]
    
    # 强制双向标签：只要有木地板/木质地板，两个标签都加上
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

# ===================== 修复3：详情页抓取，新增替代内容强制清洗，杜绝无效ID =====================
def fetch_detail_real_info(std_id, domain, self_code=''):
    """从官网详情页抓取真实的替代号，优先使用，抓取后强制清洗，仅保留合规标准号"""
    if not std_id or not domain:
        return None, None, None, None
    try:
        url = f"{domain}/gb/search/gbDetailed?id={std_id}"
        resp = SESSION.get(url, timeout=20)
        resp.raise_for_status()
        html = resp.text
        impl_date = None
        m = re.search(r'实施日期[^：:]*[：:]\s*(\d{4}[-\s]?\d{2}[-\s]?\d{2})', html)
        if m:
            impl_date = norm_date(m.group(1))
        
        # 精准匹配替代旧标准号，抓取后强制清洗
        replaces = None
        m = re.search(r'代替[^：:]*[：:]\s*([^\n<]{5,150})', html)
        if m:
            replaces_raw = m.group(1)
            replaces = clean_valid_std_codes(replaces_raw, self_code)
        
        # 精准匹配被新标准替代号，抓取后强制清洗
        replaced_by = None
        m = re.search(r'被.*代替[^：:]*[：:]\s*([^\n<]{5,150})', html)
        if m:
            replaced_by_raw = m.group(1)
            replaced_by = clean_valid_std_codes(replaced_by_raw, self_code)
        
        summary = None
        m = re.search(r'标准摘要[^：:]*[：:]\s*([^<]{10,600})', html)
        if m:
            summary = clean_sacinfo(m.group(1)).strip()
        return impl_date, replaces, replaced_by, summary
    except Exception as e:
        if DEBUG_MODE:
            log(f"详情页抓取失败 {std_id}@{domain}：{str(e)}")
        return None, None, None, None

# ===================== 抓取接口，新增自身标准号传入清洗 =====================
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
                if not code or not title:
                    continue
                if not is_sports(title):
                    continue
                issue_date = norm_date(row.get('ISSUE_DATE'))
                impl_date = norm_date(row.get('IMPL_DATE'))
                std_id = row.get('id') or row.get('ID') or ''
                status = norm_status(row.get('STATE') or row.get('STD_STATUS'))

                # 优先抓取官网真实的替代号、日期、摘要，传入自身标准号做清洗
                if std_id:
                    d_impl, d_rep, d_repd, d_sum = fetch_detail_real_info(std_id, domain, code)
                    if d_impl:
                        impl_date = d_impl
                    replaces = d_rep
                    replaced_by = d_repd
                    summary = d_sum
                else:
                    # 列表页抓取的内容也强制清洗
                    replaces_raw = clean_sacinfo(row.get('C_SUPERSEDE_CODE') or '')
                    replaces = clean_valid_std_codes(replaces_raw, code)
                    replaced_by_raw = clean_sacinfo(row.get('C_REPLACED_CODE') or '')
                    replaced_by = clean_valid_std_codes(replaced_by_raw, code)
                    summary = ''

                # 【核心规则】现行/即将实施标准，强制清空被替代字段
                if status in ['现行', '即将实施']:
                    replaced_by = None

                dept1 = str(row.get('ISSUE_DEPT') or '').strip()
                dept2 = str(row.get('ISSUE_UNIT') or '').strip()
                if dept1 and dept2 and dept2 != dept1:
                    issued_by = f"{dept1}、{dept2}"
                else:
                    issued_by = dept1 or dept2
                results.append({
                    "code": code, 
                    "title": title, 
                    "status": status,
                    "issueDate": issue_date, 
                    "implementDate": impl_date, 
                    "abolishDate": norm_date(row.get('ABOL_DATE')),
                    "issuedBy": issued_by, 
                    "replaces": replaces, 
                    "replacedBy": replaced_by, 
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

# ===================== 修复4：合并逻辑，新增替代字段清洗+手动修改保护+规则强制 =====================
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

            # 状态：只有官网是现行，才覆盖手动的废止
            if new_status and old_status != new_status:
                if new_status == '现行':
                    old['status'] = new_status
                    upd +=1

            # 【核心规则】现行/即将实施标准，强制清空被替代字段，不覆盖手动修改
            current_status = new_status or old_status
            if current_status in ['现行', '即将实施']:
                if old.get('replacedBy'):
                    old['replacedBy'] = None
                    upd +=1

            # 替代号：只有用户没手动修改，才用官网清洗后的真实数据
            for f in ['replaces','replacedBy']:
                val = item.get(f)
                # 仅当用户没手动填写、新值有效时才更新，保护手动修改
                if val and not old.get(f):
                    old[f] = val
                    upd +=1

            # 其他字段：非空才更新
            for f in ['issueDate','implementDate','abolishDate','summary']:
                val = item.get(f)
                if val and val != old.get(f):
                    old[f] = val
                    upd +=1
        else:
            existing.append(build_entry(item))
            add +=1

    # 强制去重，同一个标准号只保留1条
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
            replaced = False
            for i, exist_s in enumerate(final_standards):
                if norm_code(exist_s['code']) == nc:
                    if s.get('status') == '现行' and exist_s.get('status') != '现行':
                        final_standards[i] = s
                        replaced = True
                    break
            if replaced:
                upd +=1
    return final_standards, add, upd

def build_entry(item):
    code = item.get('code','')
    title = clean_sacinfo(item.get('title',''))
    status = item.get('status','现行')
    replaces = item.get('replaces')
    replacedBy = item.get('replacedBy')

    # 入库前最终清洗，确保合规
    replaces = clean_valid_std_codes(replaces, code)
    replacedBy = clean_valid_std_codes(replacedBy, code)
    # 强制规则：现行/即将实施标准，清空被替代字段
    if status in ['现行', '即将实施']:
        replacedBy = None

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
    before = len(standards)
    standards = [s for s in standards if is_sports(clean_sacinfo(s.get('title','')))]
    removed = before - len(standards)
    if removed > 0:
        log(f"🗑️ 自动清理：移除 {removed} 条非体育/电动自行车/重复标准")
    db['standards'] = standards
    db['updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db['total'] = len(standards)
    if dry:
        log(f"预览模式：共{len(standards)}条，不保存")
        return
    try:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DATA_FILE,'w',encoding='utf-8') as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
        log(f"✅ 保存成功：共{len(standards)}条（手动修改已保护，替代关系已全量修复）")
    except Exception as e:
        log(f"❌ 保存失败：{str(e)}")

def run(dry=False, debug=False, repair_only=False):
    """
    新增repair_only模式：仅修复现有库的替代关系，不重新抓取
    """
    global DEBUG_MODE
    DEBUG_MODE = debug
    log("="*60)
    log("体育标准抓取工具 v23（替代关系专项修复版）")
    log("核心修复：替代号无效ID过滤、现行标准移除已被替代为、废止标准替代号清洗")
    log("="*60)
    db, standards = load_db()
    log(f"当前已有：{len(standards)} 条")

    if not repair_only:
        log("\n开始抓取...")
        all_new = []
        total_kw = len(KEYWORDS)
        for i, kw in enumerate(KEYWORDS,1):
            log(f"[{i}/{total_kw}] 关键词: {kw}")
            try:
                res = fetch_samr_all(kw)
                log(f"   → 抓到 {len(res)} 条")
                all_new.extend(res)
                time.sleep(1)
            except Exception as e:
                log(f"   → 抓取失败：{str(e)}")
                time.sleep(2)
                continue
        log(f"\n抓取完成，去重前总数：{len(all_new)}")
        # 先合并，再兜底补全替代关系（绝不覆盖官网真实数据）
        standards, add, upd = merge(standards, all_new)
        log(f"合并结果：新增 {add} 条，更新 {upd} 条")
    
    # 全量修复替代关系，兜底填充+规则强制
    log("\n开始全量修复替代关系...")
    repair_count = auto_fill_replaces(standards)
    log(f"替代关系修复完成：共修正 {repair_count} 条字段")
    log(f"最终总计：{len(standards)} 条标准")
    
    save_db(db, standards, dry)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry', action='store_true', help='预览不保存')
    parser.add_argument('--debug', action='store_true', help='调试模式')
    parser.add_argument('--repair-only', action='store_true', help='仅修复现有库替代关系，不重新抓取')
    args = parser.parse_args()
    run(dry=args.dry, debug=args.debug, repair_only=args.repair_only)