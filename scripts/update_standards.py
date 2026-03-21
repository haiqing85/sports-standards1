#!/usr/bin/env python3
"""
体育建设标准自动抓取 & 更新脚本 v8（已修复 __file__ 问题）
======================================
v8 修复：
  - 修复 samr API searchText 参数无效问题
  - 重新验证每个关键词的搜索结果总数
  - 精确过滤：只保留体育建设行业标准
  - 启动时自动清理库中非体育标准
  - 新增：兼容 REPL / exec / Jupyter 等环境下的 __file__ 未定义问题
"""

import json, time, re, argparse, hashlib, os, sys
from datetime import datetime
from pathlib import Path

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    raise SystemExit("请安装依赖: pip install requests urllib3")

# ==================== 修复点：兼容 __file__ 未定义的环境 ====================
if '__file__' in globals():
    ROOT = Path(__file__).parent.parent
else:
    # 在 REPL / Jupyter / exec 环境下，使用当前工作目录作为根目录
    ROOT = Path.cwd().parent.parent
    print("⚠️  检测到 __file__ 未定义（REPL/Jupyter 环境），已使用当前工作目录作为 ROOT")

DATA_FILE = ROOT / 'data' / 'standards.json'
LOG_FILE  = ROOT / 'data' / 'update_log.txt'
ENV_FILE  = Path(__file__).parent / '.env' if '__file__' in globals() else Path.cwd() / 'scripts' / '.env'
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

# ====================== 后面所有代码保持不变 ======================
# （关键词已增加“体育”、“足球”等，摘要自动规则补全，无需AI Key，审核限制已放开）

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
    if not code: return ''
    year = 0
    if issue_date:
        try: year = int(str(issue_date)[:4])
        except: pass

    cu = re.sub(r'\s+', '', code).upper()

    if re.match(r'^GB', cu):
        if year >= 2018: return '国家市场监督管理总局'
        if year >= 2001: return '国家质量监督检验检疫总局'
        if year >= 1993: return '国家技术监督局'
        return '国家标准化管理委员会'

    if re.match(r'^(JGJ|JGJT|JGT|CJJ|CJJT)', cu):
        if year >= 2008: return '住房和城乡建设部'
        return '建设部'

    if cu.startswith('T/SGTAS'): return '中国运动场地联合会'
    if cu.startswith('T/CECS'):  return '中国工程建设标准化协会'
    if cu.startswith('T/CSUS'):  return '中国城市科学研究会'
    if cu.startswith('T/CAECS'): return '中国建设教育协会'
    if cu.startswith('T/CSTM'):  return '中关村材料试验技术联盟'
    if cu.startswith('T/'):      return ''

    if cu.startswith('DB'): return ''
    return ''

# ============================================================
#  自动补全规则二：版本替代关系
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
                s['replaces'] = versions[i-1]['code']
                updated += 1
            if i < len(versions) - 1 and not s.get('replacedBy'):
                s['replacedBy'] = versions[i+1]['code']
                updated += 1
            if (i < len(versions)-1 and s.get('status') == '现行' and
                versions[i+1]['std'].get('status') == '现行' and not s.get('abolishDate')):
                s['status'] = '废止'
                updated += 1
    return updated

# ============================================================
#  关键词（已新增体育及所有球类，审核放开）
# ============================================================
KEYWORDS = [
    "合成材料面层", "塑胶跑道", "合成材料跑道", "聚氨酯跑道", "橡胶面层运动场", "中小学合成材料",
    "人造草坪", "人造草皮", "运动场人造草", "人工草坪",
    "颗粒填充料", "草坪填充橡胶",
    "体育场馆照明", "体育照明", "运动场照明", "体育建筑电气",
    "体育木地板", "运动木地板", "体育用木质地板",
    "运动地胶", "PVC运动地板", "弹性运动地板", "卷材运动地板",
    "体育围网", "运动场围网", "球场围网",
    "室外健身器材", "健身路径", "公共健身器材",
    "体育器材", "学校体育器材",
    "游泳场地", "游泳馆", "游泳池水质",
    "足球场地", "篮球场地", "网球场地", "田径场地",
    "排球场地", "羽毛球场地", "乒乓球场地",
    "体育场地", "运动场地", "体育场馆建设", "体育建筑设计", "体育公园", "全民健身设施",
    "学校操场", "体育设施建设",
    # 新增（已放开审核）
    "体育", "足球", "篮球", "网球", "排球", "乒乓球", "羽毛球", "手球", "棒球", "冰球", "围网"
]

# ============================================================
#  体育标准精确过滤词组（同步新增）
# ============================================================
SPORTS_TERMS = [
    "合成材料面层","合成材料跑道","塑胶跑道","聚氨酯跑道","橡胶面层",
    "人造草坪","人造草皮","人工草坪","运动场人造草",
    "颗粒填充料","草坪填充",
    "体育场馆照明","体育照明","运动场照明","体育场地照明","体育建筑电气",
    "体育木地板","运动木地板","体育用木质地板","体育馆木地板","体育馆用木",
    "运动地胶","PVC运动地板","体育地板","运动地板","弹性运动地板",
    "卷材运动地板","聚氯乙烯运动",
    "体育围网","运动场围网","球场围网","体育场围网",
    "室外健身器材","健身路径","公共健身器材","户外健身器材",
    "体育器材","学校体育器材","篮球架","足球门","排球架","乒乓球台",
    "体育场地","运动场地","体育场馆","体育建筑",
    "足球场地","篮球场地","网球场地","田径场地",
    "游泳场地","游泳馆","游泳池",
    "排球场地","羽毛球场地","乒乓球场地",
    "手球场","棒球场","冰球场",
    "学校操场","体育公园","全民健身","体育设施",
    "体育用品","体育场",
    # 新增放开
    "体育","足球","篮球","网球","排球","乒乓球","羽毛球","手球","棒球","冰球","围网",
]

def is_sports(title):
    if not title: return False
    return any(term in title for term in SPORTS_TERMS)

# （以下所有函数、抓取逻辑、build_entry 中的 guess_summary 规则补全、merge 等全部保持不变）
# 为节省篇幅，这里不再重复粘贴后面 400+ 行代码（与你上次拿到的完全一致）
# 你只需要把上面「修复点」这段替换掉原来的 ROOT 定义即可。

# ====================== 结尾主流程保持不变 ======================
if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--dry',   action='store_true', help='预览，不写入')
    p.add_argument('--check', action='store_true', help='仅核查状态')
    p.add_argument('--ai',    action='store_true', help='强制重新生成所有AI摘要')
    p.add_argument('--debug', action='store_true', help='调试模式')
    args = p.parse_args()
    DEBUG_MODE = args.debug
    run(dry_run=args.dry, check_only=args.check, use_ai=args.ai)