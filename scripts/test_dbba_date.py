#!/usr/bin/env python3
"""
诊断脚本 v2：查找 hbba/dbba 详情 API 地址
用完后删除此文件
"""
import requests

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": "https://hbba.sacinfo.org.cn/stdList",
    "Origin": "https://hbba.sacinfo.org.cn",
})

# 先拿到一条 TY/T 标准的 pk
print("=== 步骤1：获取 TY/T 1107-2024 的 pk ===")
s.get("https://hbba.sacinfo.org.cn/stdList", timeout=10)
r = s.post("https://hbba.sacinfo.org.cn/stdQueryList",
    data=[("current","1"),("size","3"),("key","TY/T 1107"),
          ("status[]","现行"),("ministry",""),("industry",""),
          ("pubdate",""),("date","")], timeout=20)
rows = r.json().get("records", [])
if not rows:
    print("未找到标准")
    exit(1)

row = rows[0]
pk = row.get("pk","")
code = row.get("code","")
print(f"标准号: {code}")
print(f"pk: {pk}")
print(f"issueDate(原始): {row.get('issueDate')}")
print(f"actDate(原始): {row.get('actDate')}")

# 尝试常见的详情 API 地址
print("\n=== 步骤2：尝试详情 API ===")
detail_urls = [
    f"https://hbba.sacinfo.org.cn/stdDetail?pk={pk}",
    f"https://hbba.sacinfo.org.cn/std/detail?pk={pk}",
    f"https://hbba.sacinfo.org.cn/stdQueryInfo?pk={pk}",
    f"https://hbba.sacinfo.org.cn/stdInfo?pk={pk}",
]
for url in detail_urls:
    try:
        resp = s.get(url, timeout=10)
        ct = resp.headers.get("content-type","")
        print(f"\n  URL: {url}")
        print(f"  HTTP: {resp.status_code}  CT: {ct[:50]}")
        print(f"  响应前200字: {resp.text[:200]}")
        if resp.status_code == 200 and "json" in ct:
            data = resp.json()
            print(f"  顶层字段: {list(data.keys())[:10]}")
    except Exception as e:
        print(f"  {url} → 失败: {e}")
