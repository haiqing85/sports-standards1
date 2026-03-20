# ============================================================
#  来源二：ttbz.org.cn 团标平台 (已增加自动翻页)
# ============================================================
def fetch_ttbz(keyword):
    results = []
    try:
        # 最多向后翻10页(300条)，既能抓全又防止死循环
        for page in range(1, 11): 
            resp = SESSION.post(
                "https://www.ttbz.org.cn/api/search/standard",
                json={"keyword": keyword, "pageIndex": page, "pageSize": 30},
                headers={
                    'Referer':      'https://www.ttbz.org.cn/',
                    'Origin':       'https://www.ttbz.org.cn',
                    'Content-Type': 'application/json',
                },
                timeout=20
            )
            if not resp.ok: break
            
            ct = resp.headers.get('content-type','')
            if 'json' in ct:
                data = resp.json()
                rows = data.get('Data') or data.get('data') or []
                if not rows: break
                
                for row in rows:
                    code  = (row.get('StdCode') or row.get('stdCode') or '').strip()
                    title = (row.get('StdName') or row.get('stdName') or '').strip()
                    if not code or not title: continue
                    # 核心过滤机制：不是工程/器材相关标准，直接丢弃
                    if not is_sports(title): continue 
                    
                    results.append({
                        'code':          code,
                        'title':         title,
                        'type':          '团标',
                        'status':        norm_status(row.get('Status') or '现行'),
                        'issueDate':     norm_date(row.get('IssueDate')),
                        'implementDate': norm_date(row.get('ImplementDate')),
                        'issuedBy':      (row.get('OrgName') or '').strip(),
                        'isMandatory':   False,
                    })
                
                # 如果本页返回的数据少于30条，说明到底了，退出翻页
                if len(rows) < 30:
                    break
                
                time.sleep(0.5) # 翻页停顿，防止被封IP
            else:
                break
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] ttbz异常: {e}")
    return results

# ============================================================
#  来源三：dbba.sacinfo.org.cn 地方标准 (已增加自动翻页)
# ============================================================
def fetch_dbba(keyword):
    results = []
    try:
        for page in range(1, 11): # 最多翻10页
            resp = SESSION.get(
                'https://dbba.sacinfo.org.cn/api/standard/list',
                params={"searchText": keyword, "pageSize": 30, "pageNum": page},
                headers={'Referer':'https://dbba.sacinfo.org.cn/'},
                timeout=20
            )
            if not resp.ok: break
            
            ct = resp.headers.get('content-type','')
            if 'json' in ct:
                data = resp.json()
                items = (data.get('data') or {}).get('list') or []
                if not items: break
                
                for item in items:
                    code  = (item.get('stdCode') or '').strip()
                    title = (item.get('stdName') or '').strip()
                    if not code or not title: continue
                    if not is_sports(title): continue 
                    
                    results.append({
                        'code':          code,
                        'title':         title,
                        'type':          '地方标准',
                        'status':        norm_status(item.get('status') or ''),
                        'issueDate':     norm_date(item.get('publishDate')),
                        'implementDate': norm_date(item.get('implementDate')),
                        'issuedBy':      (item.get('publishDept') or '').strip(),
                        'isMandatory':   False,
                    })
                    
                if len(items) < 30:
                    break
                    
                time.sleep(0.5)
            else:
                break
    except Exception as e:
        if DEBUG_MODE: log(f"    [DEBUG] dbba异常: {e}")
    return results