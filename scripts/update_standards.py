#!/usr/bin/env python3
"""
体育建设标准自动抓取 & 更新脚本 v9
======================================
v9 修复（针对用户反馈）：
- 移除过于宽泛的关键词（"体育"、"足球"、"篮球"等单独词汇）
- 删除BROAD_KEYWORDS逻辑，所有关键词都进行严格过滤
- 精确过滤：只保留体育场地建设、设施器材相关标准
- 支持多机构联合发布（发布单位可包含多个机构）
- 严格摘要生成：基于标题关键词精确匹配，避免胡乱生成
- 优化搜索策略：增加关键词变体，提高覆盖率
- 启动时自动清理库中非体育建设标准

运行方式：
python scripts/update_standards.py        # 完整抓取
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

ROOT = Path(__file__).parent.parent
DATA_FILE = ROOT / 'data' / 'standards.json'
LOG_FILE = ROOT / 'data' / 'update_log.txt'
ENV_FILE = Path(__file__).parent / '.env'
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
QWEN_KEY = os.environ.get('QWEN_KEY', '')

# ============================================================
# 发布机构推断
# ============================================================

def infer_issued_by(code, issue_date):
    """根据编号前缀+发布年份推断发布机构"""
    if not code:
        return ''
    
    year = 0
    if issue_date:
        try:
            year = int(str(issue_date)[:4])
        except:
            pass
    
    cu = re.sub(r'\s+', '', code).upper()
    
    if re.match(r'^GB', cu):
        if year >= 2018:
            return '国家市场监督管理总局'
        if year >= 2001:
            return '国家质量监督检验检疫总局'
        if year >= 1993:
            return '国家技术监督局'
        return '国家标准化管理委员会'
    
    if re.match(r'^(JGJ|JGJT|JGT|CJJ|CJJT)', cu):
        if year >= 2008:
            return '住房和城乡建设部'
        return '建设部'
    
    if cu.startswith('T/SGTAS'):
        return '中国运动场地联合会'
    if cu.startswith('T/CECS'):
        return '中国工程建设标准化协会'
    if cu.startswith('T/CSUS'):
        return '中国城市科学研究会'
    if cu.startswith('T/CAECS'):
        return '中国建设教育协会'
    if cu.startswith('T/CSTM'):
        return '中关村材料试验技术联盟'
    if cu.startswith('T/'):
        return ''
    
    if cu.startswith('DB'):
        return ''
    
    return ''

# ============================================================
# 版本替代关系
# ============================================================

def auto_fill_replaces(standards):
    """自动发现同编号不同年份的版本关系"""
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
            if (i < len(versions) - 1 and s.get('status') == '现行' 
                and versions[i+1]['std'].get('status') == '现行'
                and not s.get('abolishDate')):
                s['status'] = '废止'
                updated += 1
    
    return updated

# ============================================================
# 关键词（精简为体育建设相关）
# ============================================================

KEYWORDS = [
    # 合成材料面层/塑胶跑道
    "合成材料面层",
    "塑胶跑道",
    "合成材料跑道",
    "聚氨酯跑道",
    "橡胶面层运动场",
    "中小学合成材料面层",
    
    # 人造草坪
    "人造草坪",
    "人造草皮",
    "运动场人造草",
    "人工草坪运动场",
    
    # 颗粒填充料
    "颗粒填充料",
    "草坪填充橡胶",
    
    # 灯光照明
    "体育场馆照明",
    "体育照明设计",
    "运动场照明",
    "体育建筑电气设计",
    
    # 木地板
    "体育木地板",
    "运动木地板",
    "体育用木质地板",
    "体育馆木地板",
    
    # PVC运动地板
    "运动地胶",
    "PVC运动地板",
    "弹性运动地板",
    "卷材运动地板",
    
    # 围网
    "体育围网",
    "运动场围网",
    "球场围网",
    
    # 健身器材
    "室外健身器材",
    "健身路径器材",
    "公共健身器材",
    "健身步道设施",
    "全民健身设施",
    
    # 体育器材
    "体育器材设施",
    "学校体育器材",
    
    # 游泳场地
    "游泳场地设施",
    "游泳馆建设",
    "游泳池水质",
    
    # 球类场地
    "足球场地建设",
    "足球场建设",
    "篮球场地建设",
    "篮球场建设",
    "网球场地建设",
    "网球场建设",
    "田径场地建设",
    "田径场建设",
    "排球场地建设",
    "羽毛球场地建设",
    "乒乓球场地建设",
    "手球场地建设",
    "棒球场地建设",
    "冰球场地建设",
    "曲棍球场地建设",
    
    # 综合体育/场馆
    "体育场地建设",
    "运动场地建设",
    "体育场馆建设",
    "体育建筑设计",
    "体育公园建设",
    "学校操场建设",
    "体育设施建设",
]

# ============================================================
# 体育建设标准精确过滤词组
# ============================================================

SPORTS_CONSTRUCTION_TERMS = [
    # 面层材料类
    "合成材料面层","合成材料跑道","塑胶跑道","聚氨酯跑道","橡胶面层",
    "人造草坪","人造草皮","人工草坪","运动场人造草",
    "颗粒填充料","草坪填充","橡胶颗粒",
    
    # 照明电气类
    "体育场馆照明","体育照明","运动场照明","体育场地照明","体育建筑电气",
    
    # 地板类
    "体育木地板","运动木地板","体育用木质地板","体育馆木地板","体育馆用木",
    "运动地胶","PVC运动地板","体育地板","运动地板","弹性运动地板",
    "卷材运动地板","聚氯乙烯运动",
    
    # 围网防护类
    "体育围网","运动场围网","球场围网","体育场围网",
    
    # 健身设施类
    "室外健身器材","健身路径","公共健身器材","户外健身器材",
    "健身步道","全民健身设施",
    
    # 体育器材类
    "体育器材","学校体育器材","篮球架","足球门","排球架","乒乓球台",
    
    # 场地建设类
    "体育场地","运动场地","体育场馆","体育建筑","体育场建设",
    "足球场地","篮球场地","网球场地","田径场地",
    "游泳场地","游泳馆","游泳池",
    "排球场地","羽毛球场地","乒乓球场地",
    "手球场地","棒球场地","冰球场地","曲棍球场地",
    "学校操场","体育公园","体育设施",
    
    # 排水、基础等配套设施
    "体育场地排水","运动场排水","田径场排水",
]

def is_sports_construction(title):
    """严格判断是否为体育建设标准"""
    if not title or not title.strip():
        return False
    
    title = title.strip()
    
    # 必须包含体育建设相关词汇
    has_sports_construction = any(term in title for term in SPORTS_CONSTRUCTION_TERMS)
    
    if not has_sports_construction:
        return False
    
    # 排除明显无关的内容
    exclude_terms = [
        "教学","教材","教程","课程","培训","考试",
        "用品","礼品","纪念品","服饰","服装","鞋",
        "营养","饮料","食品","保健品",
        "医疗","康复","保健","按摩",
        "软件","系统平台","信息技术",
        "管理规范","管理办法","管理规定",
        "服务规范","服务质量",
        "职业","从业人员","教练员","裁判员",
    ]
    
    if any(ex in title for ex in exclude_terms):
        return False
    
    return True

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36'

def make_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=[429,500,502,503,504])
    s.mount('https://', HTTPAdapter(max_retries=retry))
    s.mount('http://', HTTPAdapter(max_retries=retry))
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
    if not raw:
        return ''
    return re.sub(r'</?sacinfo>', '', raw).strip()

def clean_samr_code(raw):
    if not raw:
        return ''
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
    if any(x in raw for x in ['现行','有效','执行','施行']):
        return '现行'
    if any(x in raw for x in ['废止','作废','撤销','废弃']):
        return '废止'
    if any(x in raw for x in ['即将','待实施','未实施']):
        return '即将实施'
    return '现行'

def norm_date(raw):
    if not raw:
        return None
    raw = str(raw).strip()
    if raw.isdigit() and len(raw) >= 10:
        try:
            ts = int(raw)
            if ts > 1e11:
                ts //= 1000
            return datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
        except Exception:
            pass
    cleaned = re.sub(r'[^\d]', '', raw)
    if len(cleaned) >= 8:
        return f"{cleaned[:4]}-{cleaned[4:6]}-{cleaned[6:8]}"
    return None

def is_mandatory(code):
    c = norm_code(code)
    if re.match(r'^GB\d', c) and '/T' not in c:
        return True
    if c.startswith('JGJ'):
        return True
    return False

def guess_type(code):
    cu = norm_code(code)
    for prefix, t in [("GB/T","国家标准"),("GB","国家标准"),("JGJ","行业标准"), 
                      ("JG/T","行业标准"),("CJJ","行业标准"),("T/","团标"),("DB","地方标准")]:
        if cu.startswith(norm_code(prefix)):
            return t
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
        "足球场地":"足球场地","足球场建设":"足球场地",
        "篮球场地":"篮球场地","篮球场建设":"篮球场地",
        "网球场地":"网球场地","网球场建设":"网球场地",
        "田径场地":"田径场地","田径场建设":"田径场地",
    }
    for kw, cat in cm.items():
        if kw in text:
            return cat
    return "综合"

def guess_tags(text):
    return [t for t in ["体育","运动","塑胶","合成材料","人造草","照明", 
                        "木地板","围网","健身","颗粒","游泳","篮球","足球", 
                        "网球","田径","排球","羽毛球","跑道","场地","学校"] 
            if t in text][:6]

def build_entry(item):
    code = item.get('code','')
    title = clean_sacinfo(item.get('title',''))
    
    issued_by = item.get('issuedBy','').strip()
    if not issued_by:
        issued_by = infer_issued_by(code, item.get('issueDate'))
    
    return {
        'id': make_id(code),
        'code': code,
        'title': title,
        'english': '',
        'type': item.get('type') or guess_type(code),
        'status': item.get('status','现行'),
        'issueDate': item.get('issueDate') or None,
        'implementDate': item.get('implementDate') or None,
        'abolishDate': item.get('abolishDate') or None,
        'replaces': item.get('replaces') or None,
        'replacedBy': item.get('replacedBy') or None,
        'issuedBy': issued_by,
        'category': item.get('category') or guess_category(title),
        'tags': item.get('tags') or guess_tags(title),
        'summary': item.get('summary') or '',
        'isMandatory': item.get('isMandatory', is_mandatory(code)),
        'scope': '',
        'localFile': item.get('localFile') or None,
    }

# ============================================================
# 来源一：std.samr.gov.cn
# ============================================================

def fetch_samr(keyword, page=1):
    """v9：严格过滤"""
    results = []
    total_pages = 1
    
    try:
        resp = SESSION.post(
            "https://std.samr.gov.cn/gb/search/gbQueryPage",
            json={
                "searchText": keyword,
                "status": "",
                "sortField": "ISSUE_DATE",
                "sortType": "desc",
                "pageSize": 50,
                "pageIndex": page,
            },
            headers={
                'Referer': 'https://std.samr.gov.cn/',
                'Origin': 'https://std.samr.gov.cn',
                'Content-Type': 'application/json',
                'Accept': 'application/json, text/plain, */*',
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
                            log(f"  [DEBUG] samr p{page}: rows={len(rows)} total={total}")
                        
                        for row in rows:
                            code = clean_samr_code(
                                row.get('C_STD_CODE') or row.get('STD_CODE') or row.get('stdCode') or ''
                            ).strip()
                            title = clean_sacinfo(
                                row.get('C_C_NAME') or row.get('STD_NAME') or row.get('stdName') or ''
                            ).strip()
                            
                            if not code or not title:
                                continue
                            
                            if not is_sports_construction(title):
                                if DEBUG_MODE:
                                    log(f"  [DEBUG] 过滤非体育建设: {title[:40]}...")
                                continue
                            
                            issued_by = (row.get('ISSUE_DEPT') or row.get('C_ISSUE_DEPT') or '').strip()
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
                                'code': code,
                                'title': title,
                                'status': norm_status(row.get('STATE') or row.get('STD_STATUS') or ''),
                                'issueDate': issue_date,
                                'implementDate': norm_date(row.get('ACT_DATE') or row.get('IMPL_DATE')),
                                'abolishDate': norm_date(row.get('ABOL_DATE')),
                                'issuedBy': issued_by,
                                'replaces': replaces_val,
                                'replacedBy': replaced_by_val,
                                'isMandatory': is_mandatory(code) or '强制' in (row.get('STD_NATURE') or ''),
                            })
                except Exception as e:
                    if DEBUG_MODE:
                        log(f"  [DEBUG] samr JSON解析异常: {e}")
    except Exception as e:
        if DEBUG_MODE:
            log(f"  [DEBUG] samr请求异常: {e}")
    
    return results, total_pages

def fetch_samr_all(keyword):
    """抓取关键词的全部分页"""
    all_results = []
    seen = set()
    
    results, total_pages = fetch_samr(keyword, 1)
    for r in results:
        if r['code'] not in seen:
            seen.add(r['code'])
            all_results.append(r)
    
    if total_pages > 1:
        log(f"  总页数:{total_pages}，继续抓取…")
        for page in range(2, min(total_pages + 1, 21)):
            time.sleep(0.6)
            results, _ = fetch_samr(keyword, page)
            if not results:
                break
            for r in results:
                if r['code'] not in seen:
                    seen.add(r['code'])
                    all_results.append(r)
    
    return all_results

# ============================================================
# 来源二：ttbz.org.cn
# ============================================================

def fetch_ttbz(keyword):
    results = []
    try:
        resp = SESSION.post(
            "https://www.ttbz.org.cn/api/search/standard",
            json={"keyword": keyword, "pageIndex": 1, "pageSize": 30},
            headers={
                'Referer': 'https://www.ttbz.org.cn/',
                'Origin': 'https://www.ttbz.org.cn',
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
                    code = (row.get('StdCode') or row.get('stdCode') or '').strip()
                    title = (row.get('StdName') or row.get('stdName') or '').strip()
                    
                    if not code or not title:
                        continue
                    
                    if not is_sports_construction(title):
                        continue
                    
                    results.append({
                        'code': code,
                        'title': title,
                        'type': '团标',
                        'status': norm_status(row.get('Status') or '现行'),
                        'issueDate': norm_date(row.get('IssueDate')),
                        'implementDate': norm_date(row.get('ImplementDate')),
                        'issuedBy': (row.get('OrgName') or '').strip(),
                        'isMandatory': False,
                    })
    except Exception as e:
        if DEBUG_MODE:
            log(f"  [DEBUG] ttbz异常: {e}")
    
    return results

# ============================================================
# 来源三：dbba.sacinfo.org.cn
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
            ct = resp.headers.get('content-type','')
            if 'json' in ct:
                data = resp.json()
                items = (data.get('data') or {}).get('list') or []
                
                for item in items:
                    code = (item.get('stdCode') or '').strip()
                    title = (item.get('stdName') or '').strip()
                    
                    if not code or not title:
                        continue
                    
                    if not is_sports_construction(title):
                        continue
                    
                    results.append({
                        'code': code,
                        'title': title,
                        'type': '地方标准',
                        'status': norm_status(item.get('status') or ''),
                        'issueDate': norm_date(item.get('publishDate')),
                        'implementDate': norm_date(item.get('implementDate')),
                        'issuedBy': (item.get('publishDept') or '').strip(),
                        'isMandatory': False,
                    })
    except Exception as e:
        if DEBUG_MODE:
            log(f"  [DEBUG] dbba异常: {e}")
    
    return results

# ============================================================
# 摘要自动生成
# ============================================================

def generate_summary_by_rule(std):
    """根据标题关键词精确生成摘要"""
    title = std.get('title', '')
    category = std.get('category', '')
    code = std.get('code', '')
    
    summary_rules = [
        (["塑胶跑道", "合成材料跑道", "聚氨酯跑道"], 
         f"本标准规定了{title}的技术要求、试验方法、检验规则及标志、包装、运输和贮存要求。适用于各类学校、体育场馆及公共体育设施的塑胶跑道面层的设计、施工和验收。"),
        
        (["合成材料面层", "合成材料运动场"], 
         f"本标准规定了合成材料运动场地面层的技术要求、物理性能指标、有害物质限量、试验方法及检验规则。适用于中小学、体育场馆等场所的合成材料面层建设与验收。"),
        
        (["中小学合成材料"], 
         f"本标准专门针对中小学合成材料面层运动场地，规定了有毒有害物质限量、物理性能要求、试验方法及验收标准，保障学生运动安全。"),
        
        (["人造草坪", "人造草皮", "人工草坪"], 
         f"本标准规定了运动场地人造草坪系统的技术要求、试验方法及检验规则，包括草丝规格、物理性能、填充材料要求等，适用于足球场、橄榄球场等运动场地建设。"),
        
        (["体育场馆照明", "体育照明"], 
         f"本标准规定了体育场馆照明的照度标准、均匀度、眩光控制、节能指标及检测方法，适用于各类室内外体育场馆、训练场地的照明设计、安装和验收。"),
        
        (["运动场照明"], 
         f"本标准规定了运动场地照明的设计要求、技术参数及验收标准，包括照度、均匀度、色温等关键指标，确保运动员和观众的视觉舒适度。"),
        
        (["体育木地板", "运动木地板"], 
         f"本标准规定了体育用木质地板的材料要求、物理性能、冲击吸收、垂直变形等技术指标及试验方法，适用于室内篮球馆、排球馆、羽毛球馆等体育场馆。"),
        
        (["PVC运动地板", "运动地胶", "弹性运动地板"], 
         f"本标准规定了弹性运动地板（PVC地板）的技术要求、试验方法和检验规则，包括尺寸稳定性、冲击吸收、摩擦系数等性能指标，适用于室内体育运动场地。"),
        
        (["体育围网", "运动场围网", "球场围网"], 
         f"本标准规定了体育围网的技术要求、试验方法、检验规则及安装规范，包括网片强度、立柱承载力、防腐处理等指标，适用于各类球场及运动场地周边防护。"),
        
        (["室外健身器材", "健身路径器材"], 
         f"本标准规定了室外健身器材的安全要求、技术要求、试验方法及检验规则，涵盖结构强度、稳定性、材料耐候性等关键指标，适用于公共场所室外健身器材。"),
        
        (["健身步道"], 
         f"本标准规定了健身步道的建设技术要求，包括路面材质、宽度、坡度、标识系统、配套设施等指标，适用于公园、社区、城市绿道等场所健身步道建设。"),
        
        (["全民健身设施"], 
         f"本标准规定了全民健身设施的建设标准、配置要求、安全规范及验收准则，适用于社区、公园、学校等公共场所的全民健身设施规划与建设。"),
        
        (["体育器材", "篮球架", "足球门", "排球架", "乒乓球台"], 
         f"本标准规定了{title}的技术要求、安全要求、试验方法及检验规则，适用于体育场地器材的生产制造和质量验收。"),
        
        (["游泳场地", "游泳馆", "游泳池"], 
         f"本标准规定了游泳场地的设计要求、水质标准、设施配置及安全管理规范，适用于各类室内外游泳池、游泳馆的规划设计、施工建设和运营管理。"),
        
        (["足球场地", "足球场建设"], 
         f"本标准规定了足球场地的尺寸要求、场地面层技术指标、排水系统、配套设施标准及检测方法，适用于各级别足球场地的设计、建设和验收。"),
        
        (["篮球场地", "篮球场建设"], 
         f"本标准规定了篮球场地的场地尺寸、面层材料性能、场地标线及配套设施要求，适用于室内外篮球场地的设计、建设和使用验收。"),
        
        (["网球场地", "网球场建设"], 
         f"本标准规定了网球场地的尺寸要求、面层材料技术指标、排水系统及配套设施标准，适用于各类硬地、草地、红土等网球场地的建设与验收。"),
        
        (["田径场地", "田径场建设"], 
         f"本标准规定了田径场地的跑道尺寸、弯道技术要求、面层性能指标及场地配套设施标准，适用于各类室内外田径场地的设计、建设和验收。"),
        
        (["羽毛球场地", "排球场", "乒乓球场地"], 
         f"本标准规定了{title}的技术要求、场地尺寸、面层性能及配套设施标准，适用于相应体育场馆的建设与验收。"),
        
        (["颗粒填充", "草坪填充"], 
         f"本标准规定了人造草坪填充材料（橡胶颗粒）的技术要求、试验方法及检验规则，包括有害物质限量、粒径分布、弹性等性能指标。"),
        
        (["体育场地", "运动场地", "体育场馆建设"], 
         f"本标准规定了体育场地建设的规划布局、技术要求、质量验收及运营管理规范，适用于各类体育场馆、运动场地的设计与建设。"),
        
        (["体育建筑"], 
         f"本标准规定了体育建筑的设计原则、功能分区、技术要求及配套设施标准，适用于体育场、体育馆、游泳馆等体育建筑的规划设计。"),
        
        (["体育公园"], 
         f"本标准规定了体育公园的建设标准、设施配置、景观设计及运营管理要求，适用于城市体育公园、全民健身中心的规划与建设。"),
    ]
    
    for keywords, summary in summary_rules:
        if any(kw in title for kw in keywords):
            return summary
    
    cu = re.sub(r'\s+', '', code).upper()
    
    if 'JGJ' in cu or 'JGT' in cu:
        return f"本标准（{code}）为住房和城乡建设部发布的行业标准，规定了{title}的技术要求、设计规范和验收标准，适用于相关体育建筑与运动场地的工程建设。"
    
    if cu.startswith('T/'):
        return f"本标准为团体标准，规定了{title}的技术要求、试验方法和检验规则，适用于相关体育场地设施的设计、建设和验收。"
    
    if cu.startswith('DB'):
        return f"本标准为地方标准，规定了{title}的技术要求、质量标准和验收规范，适用于本地区相关体育场地设施的建设与管理。"
    
    return f"本标准规定了{title}的技术要求、试验方法和检验规则，适用于相关体育场地设施的设计、建设、检验和验收。"

def auto_fill_summary(standards):
    """全库扫描，对缺摘要的标准生成摘要"""
    filled = 0
    for s in standards:
        if not s.get('summary', '').strip():
            summary = generate_summary_by_rule(s)
            if summary:
                s['summary'] = summary
                filled += 1
    return filled

# ============================================================
# AI摘要补全
# ============================================================

def ai_enrich_standard(std):
    provider = 'qwen' if QWEN_KEY else ('deepseek' if DEEPSEEK_KEY else None)
    if not provider:
        return None
    
    prompt = (f"你是中国标准化专家。用2-3句话描述该标准主要内容和适用范围，只返回描述。\n"
              f"编号：{std.get('code','')} 名称：{std.get('title','')}")
    
    try:
        if provider == 'deepseek':
            resp = SESSION.post(
                'https://api.deepseek.com/chat/completions',
                json={"model":"deepseek-chat", "messages":[{"role":"user","content":prompt}], 
                      "max_tokens":200,"temperature":0.3},
                headers={'Authorization':f'Bearer {DEEPSEEK_KEY}', 'Content-Type':'application/json'},
                timeout=30)
            if resp.ok:
                return resp.json().get('choices',[{}])[0].get('message',{}).get('content','').strip()
        else:
            resp = SESSION.post(
                'https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation',
                json={"model":"qwen-turbo", "input":{"messages":[{"role":"user","content":prompt}]}, 
                      "parameters":{"max_tokens":200}},
                headers={'Authorization':f'Bearer {QWEN_KEY}', 'Content-Type':'application/json'},
                timeout=30)
            if resp.ok:
                return resp.json().get('output',{}).get('text','').strip()
    except Exception as e:
        if DEBUG_MODE:
            log(f"  AI失败: {e}")
    
    return None

def ai_enrich_batch(standards, force=False):
    provider = 'DeepSeek' if DEEPSEEK_KEY else ('通义千问/百炼' if QWEN_KEY else None)
    if not provider:
        log("  ⚠️ 未配置AI Key，跳过AI摘要")
        return standards
    
    log(f"🤖 AI摘要（{provider}，{'强制全部' if force else '仅补缺'}）…")
    enriched = 0
    
    for i, std in enumerate(standards):
        if not force and std.get('summary','').strip():
            continue
        s = ai_enrich_standard(std)
        if s and len(s) > 10:
            standards[i]['summary'] = s
            enriched += 1
            log(f"  ✅ [{std['code']}] {s[:40]}…")
            time.sleep(0.5)
    
    log(f" 完成：AI补全/更新 {enriched} 条摘要")
    return standards

# ============================================================
# 核查状态
# ============================================================

def check_status_online(std):
    code = std.get('code','')
    if not code:
        return None
    
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
    except Exception:
        pass
    
    return None

# ============================================================
# 合并
# ============================================================

def merge(existing, new_items):
    idx = {norm_code(s['code']): i for i, s in enumerate(existing)}
    added = updated_n = 0
    
    for item in new_items:
        cn = norm_code(item.get('code',''))
        if not cn:
            continue
        
        if cn in idx:
            orig, changed = existing[idx[cn]], False
            for f in ('status','abolishDate','implementDate','issueDate','issuedBy','replaces','replacedBy'):
                nv = item.get(f)
                if nv and nv != orig.get(f):
                    orig[f] = nv
                    changed = True
            if changed:
                updated_n += 1
        else:
            existing.append(build_entry(item))
            idx[cn] = len(existing)-1
            added += 1
    
    return existing, added, updated_n

def load_db():
    if not DATA_FILE.exists():
        log("⚠️ data/standards.json 不存在，从空白开始")
        return {'standards': []}, []
    
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            db = json.load(f)
            standards = db.get('standards') or []
            log(f"📦 现有标准数: {len(standards)} 条")
            return db, standards
    except Exception as e:
        log(f"⚠️ 文件损坏({e})，从空白开始")
        return {'standards': []}, []

def save_db(db, standards, dry_run):
    today = datetime.now().strftime('%Y-%m-%d')
    db.update({'standards':standards,'updated':today, 
               'version':today.replace('-','.'),'total':len(standards)})
    
    if dry_run:
        log(f"\n🔵 [预览] {len(standards)} 条，不写入")
        return
    
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    log(f"\n✅ 写入完成：{len(standards)} 条 版本 {today}")

# ============================================================
# 主流程
# ============================================================

def run(dry_run=False, check_only=False, use_ai=False):
    global DEBUG_MODE
