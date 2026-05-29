#!/usr/bin/env python3
"""
团体标准抓取模块 v2
数据源：全国团体标准信息平台 https://www.ttbz.org.cn
API：POST https://www.ttbz.org.cn/cms-proxy/ms/portal/standardInfo/getPortalStandardList
POST 参数（已通过 DevTools 确认）：
  pageNo:         页码（从1开始）
  pageSize:       每页数量
  searchKey:      搜索关键词（URL编码）
  standardStatus: 1（现行）

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
    "User-Agent":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
    "Accept":           "application/json, text/plain, */*",
    "Content-Type":     "application/x-www-form-urlencoded",
    "Referer":          "https://www.ttbz.org.cn/standard.html",
    "Origin":           "https://www.ttbz.org.cn",
})

def slog(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}][TTBZ] {msg}", flush=True)

def norm_status(raw):
    r = str(raw or "").strip()
    if any(x in r for x in ["现行","有效","发布","1"]): return "现行"
    if any(x in r for x in ["废止","作废"]):             return "废止"
    if any(x in r for x in ["即将","待实施"]):            return "即将实施"
    return "现行"

def norm_date(raw):
    if not raw: return None
    d = re.sub(r"[^\d]", "", str(raw))
    if len(d) >= 8: return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return None

def make_id(code):
    c = re.sub(r"[^A-Za-z0-9]", "", code.strip())[:30]
    return c if c else hashlib.md5(code.encode()).hexdigest()[:12]

def fetch_ttbz_page(keyword, page=1):
    """POST 单页，返回 (items, total)"""
    try:
        resp = SESSION.post(
            TTBZ_API,
            data={
                "pageNo":         page,
                "pageSize":       PAGE_SIZE,
                "searchKey":      keyword,   # 确认字段名
                "standardStatus": 1,         # 1=现行
            },
            timeout=20
        )
        resp.raise_for_status()
        data = resp.json()

        # 尝试多种响应结构
        rows = (
            data.get("data", {}).get("list")    or
            data.get("data", {}).get("rows")    or
            data.get("data", {}).get("records") or
            data.get("rows") or data.get("list") or
            (data if isinstance(data, list) else [])
        )
        total = int(
            data.get("data", {}).get("total") or
            data.get("total") or len(rows)
        )
        items = []
        for row in rows:
            code = str(
                row.get("standardCode") or row.get("standard_code") or
                row.get("bzh") or row.get("stdCode") or ""
            ).strip()
            title = str(
                row.get("standardName") or row.get("standard_name") or
                row.get("bzmc") or row.get("stdName") or ""
            ).strip()
            if not code or not title: continue
            if not re.match(r"^T/", code, re.IGNORECASE): continue  # 只收 T/ 开头团体标准

            org = str(
                row.get("organizeName") or row.get("organize_name") or
                row.get("fbdw") or row.get("issuedBy") or ""
            ).strip()

            items.append({
                "id":            make_id(code),
                "code":          code,
                "title":         title,
                "type":          "团体标准",
                "status":        norm_status(row.get("standardStatus") or row.get("state") or row.get("status") or "1"),
                "issueDate":     norm_date(row.get("publishDate") or row.get("pub_date") or row.get("fbsj")),
                "implementDate": norm_date(row.get("execDate") or row.get("impl_date") or row.get("sssj")),
                "abolishDate":   None,
                "issuedBy":      org,
                "replaces":      None,
                "replacedBy":    None,
                "isMandatory":   False,
                "summary":       str(row.get("scope") or row.get("summary") or ""),
                "tags":          [],
                "category":      "综合",
                "scope":         "",
                "localFile":     None,
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
        if total == 0 and not items: continue
        total_pages = min(MAX_PAGES, (total + PAGE_SIZE - 1) // PAGE_SIZE)

        for item in items:
            nc = re.sub(r"\s+", "", item["code"]).upper()
            if nc not in seen: seen.add(nc); all_results.append(item)

        for page in range(2, total_pages + 1):
            time.sleep(REQUEST_DELAY)
            items, _ = fetch_ttbz_page(kw, page)
            if not items: break
            for item in items:
                nc = re.sub(r"\s+", "", item["code"]).upper()
                if nc not in seen: seen.add(nc); all_results.append(item)
            if page % 10 == 0:
                slog(f"    {page}/{total_pages} 累计 {len(all_results)} 条")

    slog(f"✅ 团体标准抓取完成，共 {len(all_results)} 条")
    return all_results

if __name__ == "__main__":
    results = fetch_ttbz_all()
    print(f"\n共 {len(results)} 条，示例（前3条）：")
    for r in results[:3]:
        print(json.dumps(r, ensure_ascii=False, indent=2))
