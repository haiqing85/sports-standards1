#!/usr/bin/env python3
"""诊断脚本：查看 dbba 日期字段 + 原始响应"""
import requests

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": "https://dbba.sacinfo.org.cn/stdList",
    "Origin": "https://dbba.sacinfo.org.cn",
})

print("=== 诊断 dbba.sacinfo.org.cn ===")

# 先访问首页获取 Cookie
try:
    s.get("https://dbba.sacinfo.org.cn/stdList", timeout=10)
    print("Cookie 获取完成")
except Exception as e:
    print("Cookie 获取失败（继续）:", e)

# 发起查询
r = s.post("https://dbba.sacinfo.org.cn/stdQueryList",
    data=[("current","1"),("size","3"),("key","DB37/T 4831"),
          ("status[]","现行"),("ministry",""),("industry",""),
          ("pubdate",""),("date","")], timeout=20)

print("HTTP状态码:", r.status_code)
print("Content-Type:", r.headers.get("content-type",""))
print("响应前500字符:", r.text[:500])
print()

if r.status_code == 200 and r.text.strip().startswith("{"):
    rows = r.json().get("records", [])
    print("找到 %d 条记录" % len(rows))
    for row in rows[:2]:
        print("\n标准号:", row.get("code",""))
        print("所有非空字段：")
        for k, v in sorted(row.items()):
            if v is not None and str(v).strip() not in ("","null","0","None"):
                print("  %-25s = %r" % (k, v))
else:
    print("响应非 JSON，需要检查 headers 或 Cookie")
