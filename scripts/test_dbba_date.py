#!/usr/bin/env python3
"""
诊断脚本：查看 dbba.sacinfo.org.cn 返回的日期字段原始值
用法：python scripts/test_dbba_date.py
确认字段名后可删除此文件
"""
import requests

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded",
})

print("=== 诊断 dbba.sacinfo.org.cn 日期字段 ===")
r = s.post("https://dbba.sacinfo.org.cn/stdQueryList",
    data=[("current","1"),("size","3"),("key","DB37/T 4831"),
          ("status[]","现行"),("ministry",""),("industry",""),
          ("pubdate",""),("date","")], timeout=20)

rows = r.json().get("records", [])
print("找到 %d 条记录" % len(rows))

for row in rows[:2]:
    print("\n标准号: %s" % row.get("code", ""))
    print("所有非空字段：")
    for k, v in sorted(row.items()):
        if v is not None and str(v).strip() not in ("", "null", "0"):
            print("  %-20s = %r" % (k, v))
