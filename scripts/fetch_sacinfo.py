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

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
    "Accept":           "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type":     "application/x-www-form-urlencoded; charset=UTF-8",
})

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
    c = re.sub(r"[^A-Za-z0-9]", "", code.strip())[:30]
    return c if c else hashlib.md5(code.encode()).hexdigest()[:12]

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
            # issueDate=发布日期, actDate=实施日期, fzDate=废止日期(dbba), status=状态
            code  = str(row.get("code")   or row.get("C_STD_CODE") or "").strip()
            title = str(row.get("chName") or row.get("C_C_NAME")   or "").strip()
            if not code or not title:
                continue

            issued_by = str(row.get("chargeDept") or row.get("ISSUE_DEPT") or "").strip()
            status_raw = str(row.get("status") or row.get("STATE") or "").strip()

            items.append({
                "id":            make_id(code),
                "code":          code,
                "title":         title,
                "type":          std_type,
                "status":        norm_status(status_raw),
                # 日期字段尝试顺序：
                # issueDate/recordDate/approveDate → 批准日期
                # actDate/executeDate → 实施日期
                # 如均为乱码，用标准号年份兜底
                "issueDate":     (
                    norm_date(row.get("issueDate"))    or
                    norm_date(row.get("recordDate"))   or
                    norm_date(row.get("approveDate"))  or
                    norm_date(row.get("ISSUE_DATE"))   or
                    year_from_code(code)
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
                "summary":       "",
                "tags":          [],
                "category":      "综合",
                "scope":         "",
                "localFile":     None,
            })
        return items, total

    except Exception as e:
        slog(f"  请求失败 {api_url} kw={keyword} p={page}: {e}")
        return [], 0


def _fetch_all(api_url, keywords, std_type, label):
    all_results, seen = [], set()
    slog(f"开始抓取{label}...")

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
