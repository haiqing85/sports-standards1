#!/usr/bin/env python3
"""
体育建设标准自动抓取 & 更新脚本 v10 (精简性能版)
======================================
更新：
  - 彻底移除：培训、照明、产业、康养、户外、登山、设施
  - 核心逻辑：精准匹配球类、跑道、草坪、围网及场地建设
  - 性能优化：简化过滤逻辑，提升全量深挖速度
"""

import json, time, re, os, hashlib
from datetime import datetime
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- 配置区 ---
ROOT = Path(__file__).parent.parent
DATA_FILE = ROOT / 'data' / 'standards.json'
LOG_FILE = ROOT / 'data' / 'update_log.txt'

# 关键词列表 (严格按照要求精简)
KEYWORDS = [
    "体育", "足球", "篮球", "网球", "排球", "乒乓球", "羽毛球", "手球", "棒球", "冰球",
    "合成材料面层", "塑胶跑道", "合成材料跑道", "聚氨酯跑道", "橡胶面层", "中小学合成材料",
    "人造草坪", "人造草皮", "人工草坪", "颗粒填充料", "草坪填充",
    "体育木地板", "运动木地板", "体育用木质地板", "运动地胶", "PVC运动地板",
    "围网", "运动场围网", "球场围网", "体育围网",
    "体育器材", "学校体育器材", "健身器材",
    "游泳场地", "游泳馆", "游泳池水质",
    "足球场地", "篮球场地", "网球场地", "田径场地", "排球场地", "羽毛球场地", "乒乓球场地",
    "体育场地", "运动场地", "体育场馆建设", "体育建筑设计", "体育公园", "学校操场"
]

def is_sports(title):
    if not title: return False
    # 只要标题包含关键词列表中的任何一个，且不含你排除的那几个干扰项
    exclude = ['培训', '照明', '产业', '康养', '户外', '登山', '设施']
    t = title.upper()
    if any(ex in t for ex in exclude): return False
    return any(kw in t for kw in KEYWORDS)

# --- 抓取引擎 (支持多页深挖) ---
def make_session():
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    s.mount('https://', HTTPAdapter(max_retries=retries))
    s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'})
    return s

SESSION = make_session()

def fetch_ttbz(kw):
    res = []
    for p in range(1, 6): # 团标深挖5页
        try:
            r = SESSION.post("https://www.ttbz.org.cn/api/search/standard", 
                             json={"keyword": kw, "pageIndex": p, "pageSize": 30}, timeout=15)
            rows = r.json().get('Data') or []
            if not rows: break
            for row in rows:
                title = row.get('StdName', '')
                if is_sports(title):
                    res.append({
                        'code': row.get('StdCode'), 'title': title, 'type': '团标',
                        'status': '现行', 'issuedBy': row.get('OrgName'),
                        'issueDate': row.get('IssueDate'), 'implementDate': row.get('ImplementDate')
                    })
            if len(rows) < 30: break
            time.sleep(0.3)
        except: break
    return res

def fetch_dbba(kw):
    res = []
    for p in range(1, 6): # 地标深挖5页
        try:
            r = SESSION.get('https://dbba.sacinfo.org.cn/api/standard/list',
                            params={"searchText": kw, "pageSize": 30, "pageNum": p}, timeout=15)
            items = r.json().get('data', {}).get('list') or []
            if not items: break
            for item in items:
                title = item.get('stdName', '')
                if is_sports(title):
                    res.append({
                        'code': item.get('stdCode'), 'title': title, 'type': '地方标准',
                        'status': '现行', 'issuedBy': item.get('publishDept'),
                        'issueDate': item.get('publishDate'), 'implementDate': item.get('implementDate')
                    })
            if len(items) < 30: break
            time.sleep(0.3)
        except: break
    return res

# (此处省略了 samr 抓取和通用的辅助函数，逻辑与你库中一致，建议保留原有辅助函数以确准 ID 生成)

def run():
    print(f"🚀 开始全量同步，关键词总数: {len(KEYWORDS)}")
    # ... 原有的合并存储逻辑 ...
    # 建议你直接把上面的 KEYWORDS 和 is_sports 逻辑替换进你现有的脚本
    print("✅ 同步完成")

if __name__ == '__main__':
    run()