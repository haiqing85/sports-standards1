#!/usr/bin/env python3
"""
一次性清理 standards.json 中的乱码日期
运行方式：python scripts/fix_dates.py
运行位置：仓库根目录
"""
import json, re, os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       '..', 'data', 'standards.json')

def valid_date(raw):
    """有效日期：年份1950-2100，月1-12，日1-31"""
    if not raw: return None
    d = re.sub(r'[^\d]', '', str(raw))
    if len(d) >= 8:
        y, m, dd = int(d[:4]), int(d[4:6]), int(d[6:8])
        if 1950 <= y <= 2100 and 1 <= m <= 12 and 1 <= dd <= 31:
            return f"{y:04d}-{m:02d}-{dd:02d}"
    return None

def main():
    print(f"读取数据库: {DB_PATH}")
    with open(DB_PATH, encoding='utf-8') as f:
        db = json.load(f)

    standards = db.get('standards', db) if isinstance(db, dict) else db
    total = len(standards)
    fixed = 0
    date_fields = ['issueDate', 'implementDate', 'abolishDate']

    for s in standards:
        for field in date_fields:
            raw = s.get(field)
            if raw is None:
                continue
            cleaned = valid_date(raw)
            if cleaned != raw:
                s[field] = cleaned
                fixed += 1

    # 写回
    if isinstance(db, dict):
        db['standards'] = standards
        db['total'] = len(standards)
        db['updatedAt'] = datetime.now().strftime('%Y-%m-%d')
    else:
        db = standards

    with open(DB_PATH, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, separators=(',', ':'))

    print(f"✅ 清理完成：共 {total} 条，修正 {fixed} 个乱码日期字段")

if __name__ == '__main__':
    main()
