#!/usr/bin/env python3
"""
团体标准抓取模块 v4（响应结构已通过 DevTools 精确确认）
数据源：全国团体标准信息平台 https://www.ttbz.org.cn

已确认 POST 参数：
  pageNo, pageSize, searchKey, standardStatus=1

已确认响应结构：
  data.total          → 总条数
  data.rows[]         → 数据列表
    .standardNo       → 标准号（如 T/SSIASD 5-2022）
    .standardTitleCn  → 中文标题
    .organName        → 发布机构名称
    .standardStatus   → 状态码（1=现行）
    .standardStatusName → 状态名称（现行/废止）

⚠️  仅限境内访问，需在 CNB 运行
"""
import re, time, json, hashlib, requests
from datetime import datetime

TTBZ_API = "https://www.ttbz.org.cn/cms-proxy/ms/portal/standardInfo/getPortalStandardList"

SPORTS_KEYWORDS = [
    "体育", "运动", "健身", "游泳", "足球", "篮球", "田径",
    "体育场馆", "体育设施", "全民健身", "体育公园",
    "人造草", "塑胶跑道", "木地板", "体育照明",
    "武术", "跆拳道", "散打", "柔道", "摔跤", "拳击",
    "滑雪", "滑冰", "赛艇", "皮划艇",
]

PAGE_SIZE     = 10
REQUEST_DELAY = 0.8
MAX_PAGES     = 100

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
    "Accept":     "application/json, text/plain, */*",
    "Referer":    "https://www.ttbz.org.cn/standard.html",
    "Origin":     "https://www.ttbz.org.cn",
})

def slog(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}][TTBZ] {msg}", flush=True)

def norm_date(raw):
    if not raw: return None
    d = re.sub(r"[^\d]", "", str(raw))
    if len(d) >= 8:
        year, month, day = int(d[:4]), int(d[4:6]), int(d[6:8])
        if 1950 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None

def make_id(code):
    c = re.sub(r"[^A-Za-z0-9]", "", code.strip())[:30]
    return c if c else hashlib.md5(code.encode()).hexdigest()[:12]

def fetch_ttbz_page(keyword, page=1):
    try:
        resp = SESSION.post(TTBZ_API, data={
            "pageNo":         page,
            "pageSize":       PAGE_SIZE,
            "searchKey":      keyword,
            "standardStatus": 1,        # 1=现行
        }, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        # 已确认结构：data.total 和 data.rows
        inner = data.get("data", {})
        rows  = inner.get("rows", [])
        total = int(inner.get("total", 0))

        items = []
        for row in rows:
            # 已确认字段名
            code  = str(row.get("standardNo", "")).strip()
            title = str(row.get("standardTitleCn", "")).strip()
            if not code or not title:
                continue
            if not re.match(r"^T/", code, re.IGNORECASE):
                continue  # 只收团体标准

            status_name = str(row.get("standardStatusName", "现行")).strip()
            status = "现行" if "现行" in status_name else \
                     "废止" if "废止" in status_name else "现行"

            items.append({
                "id":            make_id(code),
                "code":          code,
                "title":         title,
                "type":          "团体标准",
                "status":        status,
                "issueDate":     norm_date(row.get("publishDate") or row.get("pub_date")),
                "implementDate": norm_date(row.get("execDate")    or row.get("impl_date")),
                "abolishDate":   None,
                "issuedBy":      str(row.get("organName", "")).strip(),
                "replaces":      None,
                "replacedBy":    None,
                "isMandatory":   False,
                "summary":       str(row.get("scope") or row.get("summary") or ""),
                "tags":          [],
                "category":      "综合",
                "scope":         "",
                "localFile":     None,
                "_ttbz":         True,  # 来源标记，save_db 直接放行
            })
        return items, total

    except Exception as e:
        slog(f"  请求失败 kw={keyword} p={page}: {e}")
        return [], 0


def fetch_ttbz_all():
    slog("开始抓取团体标准 ttbz.org.cn ...")
    all_results, seen = [], set()

    for kw in SPORTS_KEYWORDS:
        slog(f"  关键词: {kw}")
        items, total = fetch_ttbz_page(kw, 1)
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
            items, _ = fetch_ttbz_page(kw, page)
            if not items:
                break
            for item in items:
                nc = re.sub(r"\s+", "", item["code"]).upper()
                if nc not in seen:
                    seen.add(nc)
                    all_results.append(item)
            if page % 10 == 0:
                slog(f"    {page}/{total_pages} 累计 {len(all_results)} 条")

    slog(f"✅ 团体标准抓取完成，共 {len(all_results)} 条")
    return all_results


if __name__ == "__main__":
    results = fetch_ttbz_all()
    print(f"\n共 {len(results)} 条，示例（前3条）：")
    for r in results[:3]:
        print(json.dumps(r, ensure_ascii=False, indent=2))
