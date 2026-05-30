#!/usr/bin/env python3
"""
体育行业标准抓取模块 v2 — sactc456.org.cn
全国体育标准化技术委员会 · 国家体育总局体育器材装备中心

数据源特点：
  - 118条体育行业标准（TY/T 前缀为主，含部分 GB/GB/T）
  - 共12页，每页10条，URL: ?pageNum=N
  - SSR 服务端渲染 HTML，直接解析表格，无需 API
  - 含「代替标准号」字段，可直接映射 replacedBy

⚠️  该域名仅限境内访问
    GitHub Actions 需使用 CNB (cnb.cool) 境内 Runner
    或在本地运行后 push JSON

集成方法（在 update_standards.py 的 run() 函数中加入）：
    from fetch_sactc import fetch_sactc_all
    sactc_new = fetch_sactc_all()
    all_new.extend(sactc_new)
"""

import re
import time
import json
import hashlib
import requests
from datetime import datetime
from bs4 import BeautifulSoup

# =====================================================================
# 配置
# =====================================================================
SACTC_BASE     = "http://sactc456.org.cn"
SACTC_LIST_URL = f"{SACTC_BASE}/tybz/home/standard"
REQUEST_DELAY  = 1.0   # 每页请求间隔（秒），勿过快，防止被封
PAGE_SIZE      = 10    # 网站固定每页10条
MAX_PAGES      = 50    # 安全上限，实际12页
FETCH_DETAIL   = True  # 是否抓取详情页（获取发布日期、实施日期）
DETAIL_DELAY   = 0.5   # 详情页请求间隔

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Referer": SACTC_BASE,
})

# =====================================================================
# 工具函数
# =====================================================================

def slog(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}][SACTC] {msg}", flush=True)

def norm_status(raw):
    """有效→现行，废止/作废→废止，待实施→即将实施"""
    r = str(raw or "").strip()
    if any(x in r for x in ["有效", "现行", "发布"]):
        return "现行"
    if any(x in r for x in ["废止", "作废", "替代"]):
        return "废止"
    if any(x in r for x in ["即将", "待实施", "计划"]):
        return "即将实施"
    return "现行"

def norm_date(raw):
    if not raw:
        return None
    d = re.sub(r"[^\d]", "", str(raw))
    if len(d) >= 8:
        year, month, day = int(d[:4]), int(d[4:6]), int(d[6:8])
        if 1950 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    if len(d) == 6:
        return f"{d[:4]}-{d[4:6]}"
    return None

def norm_code(c):
    if not c:
        return ""
    return re.sub(r"\s+", "", c.replace("　", " ").replace("－", "-")).upper()

def make_id(code):
    clean = re.sub(r"[^A-Za-z0-9]", "", code.strip())[:30]
    return clean if clean else hashlib.md5(code.encode()).hexdigest()[:12]

def guess_type(code):
    c = norm_code(code)
    if c.startswith("GB/T") or (c.startswith("GB") and "/T" not in c):
        return "国家标准"
    if c.startswith("T/"):
        return "团体标准"
    if c.startswith("DB"):
        return "地方标准"
    # TY/T = 体育行业推荐标准，TY = 体育行业强制标准
    return "行业标准"

def is_mandatory(code):
    c = norm_code(code)
    # TY（无/T）= 强制性行业标准；GB（无/T）= 强制性国家标准
    if re.match(r"^GB\d", c) and "/T" not in c:
        return True
    if re.match(r"^TY\d", c) and "/T" not in c:
        return True
    return False

def clean_replaced_by(raw, self_code=""):
    """清洗代替标准号字段"""
    if not raw or not str(raw).strip():
        return None
    items = re.split(r"[;；,，\s]+", str(raw).strip())
    result = []
    self_norm = norm_code(self_code)
    for item in items:
        item = item.strip()
        if not item:
            continue
        # 基本格式校验
        if not re.match(r"^[A-Z]+[/ ]*T?\s*\d", item, re.IGNORECASE):
            continue
        if norm_code(item) == self_norm:
            continue
        result.append(item)
    return "；".join(result) if result else None


# =====================================================================
# 详情页抓取（补充发布日期、实施日期）
# =====================================================================

def fetch_detail(detail_url):
    """
    抓取单个标准的详情页，提取：
      - 发布日期 issueDate
      - 实施日期 implementDate
      - 废止日期 abolishDate（如有）
      - 发布机构 issuedBy
    """
    if not detail_url:
        return {}
    try:
        resp = SESSION.get(detail_url, timeout=15)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "lxml")
        result = {}

        # 通用：从页面文本中用正则提取日期和机构
        text = soup.get_text(separator="\n")

        # 发布日期
        m = re.search(r"发布日期[：:\s]*(\d{4}[-年./]\d{1,2}[-月./]\d{0,2})", text)
        if m:
            result["issueDate"] = norm_date(m.group(1))

        # 实施日期
        m = re.search(r"实施日期[：:\s]*(\d{4}[-年./]\d{1,2}[-月./]\d{0,2})", text)
        if m:
            result["implementDate"] = norm_date(m.group(1))

        # 废止日期
        m = re.search(r"废止日期[：:\s]*(\d{4}[-年./]\d{1,2}[-月./]\d{0,2})", text)
        if m:
            result["abolishDate"] = norm_date(m.group(1))

        # 发布机构
        m = re.search(r"(?:发布机构|发布单位|归口单位)[：:\s]*([^\n]{3,60})", text)
        if m:
            result["issuedBy"] = m.group(1).strip()

        return result
    except Exception as e:
        return {}


# =====================================================================
# 列表页解析
# =====================================================================

def parse_list_page(html):
    """
    解析标准列表页 HTML，返回标准列表。
    表格列：序号 | 层级 | 标准号 | 中文名称 | 状态 | 制定/修订 | 代替标准号
    """
    soup = BeautifulSoup(html, "lxml")
    results = []

    # 定位表格（含标准数据的那张）
    table = None
    for t in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in t.find_all("th")]
        if any("标准号" in h for h in headers):
            table = t
            break

    if not table:
        # 降级：尝试找含 TY/T 或 GB 的行
        slog("⚠️  未找到标准表格，尝试降级解析")
        return results

    rows = table.find_all("tr")[1:]  # 跳过表头行
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 5:
            continue

        # 列索引（根据截图确认的列顺序）
        level_raw  = cols[1].get_text(strip=True)   # HB / GB
        code_cell  = cols[2]
        title_cell = cols[3]
        status_raw = cols[4].get_text(strip=True)   # 有效 / 废止
        # cols[5] = 制定/修订（暂不使用）
        replaced_raw = cols[6].get_text(strip=True) if len(cols) > 6 else ""

        # 标准号（优先取文本，链接作为详情入口）
        code = code_cell.get_text(strip=True)
        if not code:
            continue

        # 详情页链接（标准号或标题的 <a> 标签）
        detail_a = code_cell.find("a") or title_cell.find("a")
        detail_href = detail_a.get("href", "") if detail_a else ""
        if detail_href and not detail_href.startswith("http"):
            detail_href = SACTC_BASE + detail_href

        title = title_cell.get_text(strip=True)
        if not title:
            continue

        # 层级映射到标准类型
        std_type = {
            "HB": "行业标准",
            "GB": "国家标准",
            "DB": "地方标准",
            "TB": "团体标准",
        }.get(level_raw.upper(), guess_type(code))

        results.append({
            "code":        code,
            "title":       title,
            "type":        std_type,
            "status":      norm_status(status_raw),
            "replacedBy":  clean_replaced_by(replaced_raw, code),
            "replaces":    None,
            "_detail_url": detail_href,   # 内部字段，用于补充详情
            "_source":     "sactc456",
        })

    return results


def parse_total_pages(html):
    """从页面提取总页数"""
    m = re.search(r"共(\d+)页", html)
    if m:
        return int(m.group(1))
    m = re.search(r"共(\d+)条记录", html)
    if m:
        total = int(m.group(1))
        return (total + PAGE_SIZE - 1) // PAGE_SIZE
    return 1


# =====================================================================
# 主抓取函数
# =====================================================================

def fetch_sactc_all():
    """
    全量抓取 sactc456.org.cn 所有体育行业标准。
    返回与 update_standards.py merge() 兼容的标准列表。
    """
    all_results = []
    seen = set()

    slog("开始抓取体育行业标准（全国体育标委会）...")

    # 第一页 — 确定总页数
    try:
        resp = SESSION.get(SACTC_LIST_URL, params={"pageNum": 1}, timeout=20)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        total_pages = min(parse_total_pages(resp.text), MAX_PAGES)
        page_results = parse_list_page(resp.text)
        slog(f"总页数: {total_pages}，第1页解析到 {len(page_results)} 条")
    except Exception as e:
        slog(f"❌ 第1页请求失败: {e}")
        return []

    for item in page_results:
        nc = norm_code(item["code"])
        if nc and nc not in seen:
            seen.add(nc)
            all_results.append(item)

    # 翻页抓取
    for page in range(2, total_pages + 1):
        time.sleep(REQUEST_DELAY)
        try:
            resp = SESSION.get(SACTC_LIST_URL, params={"pageNum": page}, timeout=20)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            page_results = parse_list_page(resp.text)
            for item in page_results:
                nc = norm_code(item["code"])
                if nc and nc not in seen:
                    seen.add(nc)
                    all_results.append(item)
            slog(f"第{page}/{total_pages}页，累计 {len(all_results)} 条")
        except Exception as e:
            slog(f"⚠️  第{page}页失败: {e}")
            continue

    # 补充详情页信息（发布日期、实施日期等）
    if FETCH_DETAIL:
        slog(f"开始补充详情页信息（共 {len(all_results)} 条）...")
        for i, item in enumerate(all_results, 1):
            detail_url = item.pop("_detail_url", "")
            if detail_url:
                detail = fetch_detail(detail_url)
                if detail:
                    item.update({k: v for k, v in detail.items() if v and not item.get(k)})
            if i % 20 == 0:
                slog(f"  详情进度: {i}/{len(all_results)}")
            time.sleep(DETAIL_DELAY)
    else:
        for item in all_results:
            item.pop("_detail_url", None)

    # 清理内部字段，补全 merge() 所需字段
    final = []
    for item in all_results:
        item.pop("_detail_url", None)
        item.pop("_source", None)
        final.append({
            "id":             make_id(item["code"]),
            "code":           item["code"],
            "title":          item.get("title", ""),
            "english":        "",
            "type":           item.get("type", "行业标准"),
            "status":         item.get("status", "现行"),
            "issueDate":      item.get("issueDate"),
            "implementDate":  item.get("implementDate"),
            "abolishDate":    item.get("abolishDate"),
            "replaces":       None,       # 强制清空（与主脚本规则一致）
            "replacedBy":     item.get("replacedBy"),
            "issuedBy":       item.get("issuedBy", "国家体育总局"),
            "category":       "综合",     # 主脚本 merge 后会自动分类
            "tags":           [],
            "summary":        item.get("summary", ""),
            "isMandatory":    is_mandatory(item["code"]),
            "scope":          "",
            "localFile":      None,
            "_sactc":         True,   # 标记来源为体育行业标委会，save_db() 直接放行不过滤
        })

    slog(f"✅ 抓取完成，共 {len(final)} 条体育行业标准")
    return final


# =====================================================================
# 独立运行 / 测试
# =====================================================================
if __name__ == "__main__":
    import sys

    print("=" * 60)
    print("SACTC 体育行业标准抓取 — 独立测试")
    print("=" * 60)

    # 快速测试模式：仅抓第1页，不抓详情
    quick = "--quick" in sys.argv
    if quick:
        FETCH_DETAIL = False
        MAX_PAGES = 1
        print("快速测试模式（仅第1页，不抓详情）\n")

    results = fetch_sactc_all()

    print(f"\n共抓取 {len(results)} 条，示例（前3条）：")
    for r in results[:3]:
        print(json.dumps(r, ensure_ascii=False, indent=2))

    # 保存到文件（可选）
    if "--save" in sys.argv:
        out = "sactc_standards.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n已保存到 {out}")
