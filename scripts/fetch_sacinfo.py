#!/usr/bin/env python3
"""
行业标准 + 地方标准抓取模块 v4（参数已通过 DevTools 精确确认）
数据源：全国标准信息公共服务平台（sacinfo.org.cn）
  - 行业标准：https://hbba.sacinfo.org.cn/stdQueryList  POST
  - 地方标准：https://dbba.sacinfo.org.cn/stdQueryList  POST

已确认 POST 参数（hbba DevTools 载荷截图）：
  current:   页码（从1开始）
  size:      每页数量
  key:       搜索关键词
  status[]:  现行
  status[]:  即将实施
  ministry:  部委（留空=全部）
  industry:  行业（留空=全部）

⚠️  仅限境内访问，需在 CNB 运行
"""
import re, time, json, hashlib, requests
from datetime import datetime

HBBA_API = "https://hbba.sacinfo.org.cn/stdQueryList"
DBBA_API = "https://dbba.sacinfo.org.cn/stdQueryList"

HBBA_KEYWORDS = [
    "体育", "运动场地", "健身器材", "游泳", "体育场馆照明", "人造草", "塑胶跑道",
    "田径", "足球", "篮球", "网球", "体育建筑", "体育木地板",
]
DBBA_KEYWORDS = [
    "体育", "运动场", "健身", "游泳", "足球", "篮球", "田径",
    "体育设施", "全民健身", "体育公园", "人造草", "塑胶跑道", "木地板",
    "体育场馆", "运动场地",
    # 新增
    "悬浮拼装", "拼装运动地板", "悬浮地板", "拼装地板", "运动地板",
    "橡胶地板", "冰场", "攀岩", "泳池", "体育地板", "气膜",
]

PAGE_SIZE     = 15
REQUEST_DELAY = 0.8
MAX_PAGES     = 100
DETAIL_DELAY  = 0.3   # 详情页请求间隔（秒）

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
    "Accept":           "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type":     "application/x-www-form-urlencoded; charset=UTF-8",
})

def _init_session(base_url):
    """访问首页获取 Cookie，否则 API 可能返回空响应"""
    try:
        SESSION.headers.update({"Referer": base_url, "Origin": base_url})
        SESSION.get(base_url.replace("/stdQueryList", "/stdList"), timeout=10)
    except Exception:
        pass

def fetch_detail_dates(pk, base_url):
    """
    抓取详情页获取精确日期。
    详情页 URL：{base_url}/stdDetail/{pk}
    页面为 SSR HTML，解析 <td>发布日期</td><td>YYYY-MM-DD</td> 格式
    返回 {'issueDate': ..., 'implementDate': ..., 'issuedBy': ...}
    """
    if not pk:
        return {}
    detail_base = base_url.replace("sacinfo.org.cn/stdQueryList",
                                   "sacinfo.org.cn/stdDetail")
    url = f"{detail_base}/{pk}"
    try:
        headers = {"Accept": "text/html,application/xhtml+xml,*/*",
                   "X-Requested-With": ""}
        resp = SESSION.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return {}
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")
        info = {}
        # 解析所有 <tr><td>字段名</td><td>值</td></tr> 结构
        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True)
                val = cells[1].get_text(strip=True)
                info[key] = val
        result = {}
        if "发布日期" in info:
            result["issueDate"] = norm_date(info["发布日期"])
        if "实施日期" in info:
            result["implementDate"] = norm_date(info["实施日期"])
        if "批准发布部门" in info:
            result["issuedBy"] = _clean_issuer(info["批准发布部门"])
        elif "归口单位" in info:
            result["issuedBy"] = _clean_issuer(info["归口单位"])
        return result
    except Exception as e:
        return {}

def slog(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}][SACINFO] {msg}", flush=True)

def norm_status(raw):
    r = str(raw or "").strip()
    if any(x in r for x in ["现行","有效","发布"]): return "现行"
    if any(x in r for x in ["废止","作废"]):         return "废止"
    if any(x in r for x in ["即将","待实施"]):        return "即将实施"
    return "现行"

def norm_date(raw):
    """
    日期格式化，统一为 YYYY-MM-DD，支持两种格式：
    1. 毫秒时间戳（13位，如1739462400000）→ 北京时间UTC+8转换
    2. 普通日期字符串（如20250214、2025-02-14）
    """
    if raw is None or str(raw).strip() == "":
        return None
    # 毫秒时间戳：13位整数
    try:
        val = int(str(raw).strip())
        if val > 10000000000:
            from datetime import datetime, timezone, timedelta
            dt = datetime.fromtimestamp(val / 1000, tz=timezone(timedelta(hours=8)))
            return dt.strftime('%Y-%m-%d')
    except (ValueError, TypeError):
        pass
    # 普通日期字符串
    d = re.sub(r"[^\d]", "", str(raw))
    if len(d) >= 8:
        year, month, day = int(d[:4]), int(d[4:6]), int(d[6:8])
        if 1950 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None

def year_from_code(code):
    """从标准号末尾提取年份作为日期兜底，如 DB37/T 4831-2025 → 2025-01-01"""
    m = re.search(r'[-—]\s*(\d{4})\s*$', str(code or ''))
    if m:
        y = int(m.group(1))
        if 1950 <= y <= 2100:
            return f"{y}-01-01"
    return None

def make_id(code):
    c = re.sub(r"[^A-Za-z0-9]", "", code.strip())[:30]
    return c if c else hashlib.md5(code.encode()).hexdigest()[:12]

# 主机构名称列表：归口部门截断到主机构
_MAIN_BODIES = [
    "国家体育总局", "住房和城乡建设部", "国家市场监督管理总局",
    "国家标准化管理委员会", "工业和信息化部", "交通运输部",
    "农业农村部", "国家林业和草原局", "生态环境部", "教育部",
    "国家发展和改革委员会", "商务部", "文化和旅游部",
]

def _clean_issuer(raw):
    """截断归口部门到主机构，如 '国家体育总局体育经济司' → '国家体育总局'"""
    if not raw:
        return raw
    for body in _MAIN_BODIES:
        if raw.startswith(body) and len(raw) > len(body):
            return body
    return raw

def fetch_sacinfo_page(api_url, keyword, page=1, std_type="行业标准"):
    """
    POST 参数已确认：current, size, key, status[]
    """
    try:
        # requests 用 list of tuples 发送同名多值参数（status[]）
        payload = [
            ("current",   page),
            ("size",      PAGE_SIZE),
            ("key",       keyword),
            ("status[]",  "现行"),
            ("status[]",  "即将实施"),
            ("ministry",  ""),
            ("industry",  ""),
            ("pubdate",   ""),
            ("date",      ""),
        ]
        resp = SESSION.post(api_url, data=payload, timeout=20)
        resp.raise_for_status()

        ct = resp.headers.get("content-type", "")
        if "html" in ct.lower():
            slog(f"  ⚠️  返回 HTML，非 JSON：{resp.text[:100]}")
            return [], 0

        data  = resp.json()

        # 日志确认响应结构：列表在 records 字段（不是 rows）
        if page == 1:
            slog(f"  响应顶层字段: {list(data.keys())}")
            slog(f"  total={data.get('total','无')}  records={len(data.get('records',[]))}")

        rows  = data.get("records", [])   # ← 修复：records 不是 rows
        total = int(data.get("total", 0))

        items = []
        for row in rows:
            if page == 1 and not items:
                slog(f"  第一行字段: {sorted(row.keys())}")  # 诊断字段名

            # CNB 日志已确认的实际字段名（hbba/dbba）：
            # code=标准号, chName=中文名, chargeDept=主管部门
            # issueDate=发布日期(毫秒时间戳), actDate=实施日期, fzDate=废止日期, status=状态
            code  = str(row.get("code")   or row.get("C_STD_CODE") or "").strip()
            title = str(row.get("chName") or row.get("C_C_NAME")   or "").strip()
            if not code or not title:
                continue

            # 发布机构：取批准发布部门（多个字段尝试），截断到主机构名
            issued_by_raw = str(
                row.get("approveDept") or       # 批准发布部门（优先）
                row.get("publishDept") or
                row.get("chargeDept") or         # 归口部门（备用）
                row.get("ISSUE_DEPT") or ""
            ).strip()
            issued_by = _clean_issuer(issued_by_raw)

            status_raw = str(row.get("status") or row.get("STATE") or "").strip()

            # 摘要：尝试多个可能字段
            summary = str(
                row.get("scope") or row.get("range") or
                row.get("summary") or row.get("remark") or
                row.get("applicableScope") or ""
            ).strip()

            items.append({
                "id":            make_id(code),
                "code":          code,
                "title":         title,
                "type":          std_type,
                "status":        norm_status(status_raw),
                # issueDate/actDate 均为毫秒时间戳，norm_date 已支持转换
                "issueDate":     (
                    norm_date(row.get("issueDate"))    or
                    norm_date(row.get("recordDate"))   or
                    norm_date(row.get("approveDate"))  or
                    norm_date(row.get("ISSUE_DATE"))
                    # 不用 year_from_code 兜底，留给详情页抓精确日期
                ),
                "implementDate": (
                    norm_date(row.get("actDate"))      or
                    norm_date(row.get("executeDate"))  or
                    norm_date(row.get("IMPL_DATE"))
                ),
                "abolishDate":   norm_date(row.get("fzDate")    or row.get("ABOL_DATE")),
                "issuedBy":      issued_by,
                "replaces":      None,
                "replacedBy":    None,
                "isMandatory":   bool(
                    re.match(r"^(GB|TY|DB\d+)\d", re.sub(r"\s+", "", code).upper())
                    and "/T" not in code
                ),
                "summary":       summary,
                "tags":          [],
                "category":      "综合",
                "scope":         "",
                "localFile":     None,
                "_pk":           str(row.get("pk") or ""),   # 详情页 key，用完后删除
            })
        return items, total

    except Exception as e:
        slog(f"  请求失败 {api_url} kw={keyword} p={page}: {e}")
        return [], 0


def _fetch_all(api_url, keywords, std_type, label):
    all_results, seen = [], set()
    slog(f"开始抓取{label}...")

    # 初始化 Session Cookie
    _init_session(api_url)

    for kw in keywords:
        slog(f"  关键词: {kw}")
        items, total = fetch_sacinfo_page(api_url, kw, 1, std_type)
        if total == 0 and not items:
            continue
        total_pages = min(MAX_PAGES, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        slog(f"    共 {total} 条，{total_pages} 页")

        for item in items:
            nc = re.sub(r"\s+", "", item["code"]).upper()
            if nc not in seen:
                seen.add(nc)
                all_results.append(item)

        for page in range(2, total_pages + 1):
            time.sleep(REQUEST_DELAY)
            items, _ = fetch_sacinfo_page(api_url, kw, page, std_type)
            if not items:
                break
            for item in items:
                nc = re.sub(r"\s+", "", item["code"]).upper()
                if nc not in seen:
                    seen.add(nc)
                    all_results.append(item)
            if page % 10 == 0:
                slog(f"    {page}/{total_pages} 累计 {len(all_results)} 条")

    # 补充详情页：对 issueDate 缺失的条目抓精确日期
    missing = [s for s in all_results if not s.get("issueDate") and s.get("_pk")]
    if missing:
        slog(f"  补充详情页日期（{len(missing)} 条缺失日期）...")
        for i, item in enumerate(missing):
            pk = item.pop("_pk", "")
            detail = fetch_detail_dates(pk, api_url)
            if detail:
                if detail.get("issueDate"):     item["issueDate"]     = detail["issueDate"]
                if detail.get("implementDate"): item["implementDate"] = detail["implementDate"]
                if detail.get("issuedBy"):      item["issuedBy"]      = detail["issuedBy"]
            else:
                # 详情页也拿不到，最后用年份兜底
                item["issueDate"] = year_from_code(item.get("code",""))
            time.sleep(DETAIL_DELAY)
            if (i+1) % 20 == 0:
                slog(f"    详情进度: {i+1}/{len(missing)}")

    # 清理剩余 _pk 字段
    for item in all_results:
        item.pop("_pk", None)

    slog(f"✅ {label}抓取完成，共 {len(all_results)} 条")
    return all_results

    slog(f"✅ {label}抓取完成，共 {len(all_results)} 条")
    return all_results


def fetch_hbba_all():
    return _fetch_all(HBBA_API, HBBA_KEYWORDS, "行业标准", "行业标准 hbba.sacinfo.org.cn")

def fetch_dbba_all():
    return _fetch_all(DBBA_API, DBBA_KEYWORDS, "地方标准", "地方标准 dbba.sacinfo.org.cn")


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "hbba"
    r = fetch_hbba_all() if t == "hbba" else fetch_dbba_all()
    print(f"共 {len(r)} 条，前2条：")
    for x in r[:2]:
        print(json.dumps(x, ensure_ascii=False))
