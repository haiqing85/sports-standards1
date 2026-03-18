#!/usr/bin/env python3
"""
体育标准 PDF 自动下载脚本
======================================
自动从各官方平台下载标准 PDF 文件，保存到 downloads/ 目录。

覆盖来源：
  ① 国家标准（免费）  → openstd.samr.gov.cn  (免费公开，可直接下载)
  ② 国家标准（全部）  → 全国标准信息平台 PDF 接口
  ③ 团体标准          → ttbz.org.cn
  ④ 地方标准          → dbba.sacinfo.org.cn

运行方式：
  python scripts/download_pdfs.py              # 下载所有缺失 PDF
  python scripts/download_pdfs.py --all        # 强制重新下载全部
  python scripts/download_pdfs.py --id GB36246-2018  # 下载指定标准
  python scripts/download_pdfs.py --dry        # 预览，不实际下载
"""

import json
import time
import argparse
import hashlib
import re
import os
from pathlib import Path
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    import requests
except ImportError:
    print("请先安装依赖: pip install requests")
    raise

# ============================================================
#  路径配置
# ============================================================
ROOT         = Path(__file__).parent.parent
DATA_FILE    = ROOT / 'data' / 'standards.json'
DOWNLOAD_DIR = ROOT / 'downloads'
LOG_FILE     = ROOT / 'data' / 'download_log.txt'

DOWNLOAD_DIR.mkdir(exist_ok=True)

# ============================================================
#  HTTP 会话
# ============================================================
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/pdf,application/octet-stream,*/*',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Referer': 'https://openstd.samr.gov.cn/',
}

def make_session() -> requests.Session:
    """创建带重试机制的会话"""
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    s.mount('https://', HTTPAdapter(max_retries=retry))
    s.mount('http://',  HTTPAdapter(max_retries=retry))
    s.headers.update(HEADERS)
    return s

SESSION = make_session()

# ============================================================
#  工具
# ============================================================
def log(msg: str):
    """记录日志"""
    ts = datetime.now().strftime('%H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass

def is_valid_pdf(path: Path) -> bool:
    """验证文件是否为有效 PDF（检查魔数）"""
    if not path.exists() or path.stat().st_size < 1024:
        return False
    try:
        with open(path, 'rb') as f:
            return f.read(4) == b'%PDF'
    except Exception:
        return False

def save_pdf(path: Path, content: bytes) -> bool:
    """写入 PDF 并验证"""
    try:
        path.write_bytes(content)
        if is_valid_pdf(path):
            return True
        path.unlink(missing_ok=True)
        return False
    except Exception:
        return False

# ============================================================
#  来源一：国家标准全文公开系统（免费 GB/GB/T）
#  接口：openstd.samr.gov.cn
# ============================================================
def fetch_openstd_hcno(code: str) -> str:
    """
    通过标准编号查询 openstd 的 hcno 标识符
    """
    try:
        resp = SESSION.post(
            'https://openstd.samr.gov.cn/bzgk/gb/gbQuery',
            json={"searchText": code, "pageSize": 5, "pageIndex": 1},
            timeout=15
        )
        rows = resp.json().get('rows') or []
        for row in rows:
            rc = (row.get('STD_CODE') or '').strip().upper().replace(' ', '')
            qc = code.upper().replace(' ', '')
            if rc == qc:
                return row.get('PLAN_CODE') or row.get('hcno') or ''
    except Exception as e:
        log(f"    ⚠️  openstd 查询 hcno 失败 [{code}]: {e}")
    return ''

def download_openstd_pdf(code: str, hcno: str = '') -> bytes:
    """
    从 openstd 下载 PDF
    尝试多种接口路径
    """
    if not hcno:
        hcno = fetch_openstd_hcno(code)
    if not hcno:
        return b''

    urls_to_try = [
        # 接口一：PDF 直链
        f"https://openstd.samr.gov.cn/bzgk/gb/viewGbInfo?id={hcno}&type=2",
        # 接口二：在线预览 PDF
        f"https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno={hcno}",
        # 接口三：直接下载
        f"https://openstd.samr.gov.cn/bzgk/gb/downloadGB?hcno={hcno}",
    ]

    for url in urls_to_try:
        try:
            resp = SESSION.get(url, timeout=30, stream=True)
            if resp.status_code == 200:
                content = b''.join(resp.iter_content(chunk_size=8192))
                if content[:4] == b'%PDF':
                    return content
                # 可能是跳转页面，尝试提取真实 PDF URL
                if b'href' in content or b'src' in content:
                    text = content.decode('utf-8', errors='ignore')
                    pdf_link = re.search(r'href="([^"]*\.pdf[^"]*)"', text)
                    if pdf_link:
                        pdf_resp = SESSION.get(pdf_link.group(1), timeout=30)
                        if pdf_resp.content[:4] == b'%PDF':
                            return pdf_resp.content
        except Exception as e:
            log(f"    ⚠️  openstd 下载失败 [{url[:60]}]: {e}")
        time.sleep(0.5)

    return b''

# ============================================================
#  来源二：全国标准信息公共服务平台（samr.gov.cn）
# ============================================================
def download_samr_pdf(code: str) -> bytes:
    """
    通过全国标准信息平台下载 PDF
    """
    try:
        # 先搜索获取 ID
        resp = SESSION.post(
            'https://std.samr.gov.cn/gb/search/gbQueryPage',
            json={"searchText": code, "pageSize": 5, "pageIndex": 1},
            timeout=15
        )
        rows = resp.json().get('rows') or []
        for row in rows:
            rc = (row.get('STD_CODE') or '').strip().upper().replace(' ', '')
            if rc == code.upper().replace(' ', ''):
                std_id = row.get('ID') or row.get('id') or ''
                if std_id:
                    # 尝试下载 PDF
                    pdf_url = f"https://std.samr.gov.cn/gb/download/{std_id}"
                    pdf_resp = SESSION.get(pdf_url, timeout=30)
                    if pdf_resp.content[:4] == b'%PDF':
                        return pdf_resp.content
    except Exception as e:
        log(f"    ⚠️  samr 下载失败 [{code}]: {e}")
    return b''

# ============================================================
#  来源三：全国团体标准信息平台（ttbz.org.cn）
# ============================================================
def download_ttbz_pdf(code: str) -> bytes:
    """
    从团标平台下载 PDF
    """
    try:
        resp = SESSION.post(
            'https://www.ttbz.org.cn/api/search/standard',
            json={"keyword": code, "pageIndex": 1, "pageSize": 5},
            timeout=15
        )
        items = resp.json().get('Data') or []
        for item in items:
            sc = (item.get('StdCode') or '').strip().upper().replace(' ', '')
            if sc == code.upper().replace(' ', ''):
                std_id = item.get('Id') or item.get('id') or ''
                if std_id:
                    pdf_url = f"https://www.ttbz.org.cn/api/download/standard/{std_id}"
                    pdf_resp = SESSION.get(pdf_url, timeout=30)
                    if pdf_resp.content[:4] == b'%PDF':
                        return pdf_resp.content
    except Exception as e:
        log(f"    ⚠️  ttbz 下载失败 [{code}]: {e}")
    return b''

# ============================================================
#  来源四：地方标准数据库（dbba.sacinfo.org.cn）
# ============================================================
def download_dbba_pdf(code: str) -> bytes:
    """
    从地方标准数据库下载 PDF
    """
    try:
        resp = SESSION.get(
            'https://dbba.sacinfo.org.cn/api/standard/list',
            params={"searchText": code, "pageSize": 5, "pageNum": 1},
            timeout=15
        )
        items = (resp.json().get('data') or {}).get('list') or []
        for item in items:
            sc = (item.get('stdCode') or '').strip().upper().replace(' ', '')
            if sc == code.upper().replace(' ', ''):
                std_id = item.get('id') or ''
                if std_id:
                    pdf_url = f"https://dbba.sacinfo.org.cn/api/standard/download/{std_id}"
                    pdf_resp = SESSION.get(pdf_url, timeout=30)
                    if pdf_resp.content[:4] == b'%PDF':
                        return pdf_resp.content
    except Exception as e:
        log(f"    ⚠️  dbba 下载失败 [{code}]: {e}")
    return b''

# ============================================================
#  智能下载路由：根据标准类型选择来源
# ============================================================
def download_standard_pdf(std: dict) -> bytes:
    """
    根据标准类型依次尝试各下载来源，返回 PDF 字节数据
    """
    code  = std.get('code', '')
    stype = std.get('type', '')

    log(f"  → 尝试下载: {code} [{stype}]")

    if stype == '国家标准':
        # 优先从 openstd（免费公开系统）下载
        data = download_openstd_pdf(code)
        if data:
            log(f"    ✅ openstd 成功 ({len(data):,} bytes)")
            return data
        time.sleep(1)
        # 备用：samr 平台
        data = download_samr_pdf(code)
        if data:
            log(f"    ✅ samr 备用成功 ({len(data):,} bytes)")
            return data

    elif stype == '团标':
        data = download_ttbz_pdf(code)
        if data:
            log(f"    ✅ ttbz 成功 ({len(data):,} bytes)")
            return data

    elif stype == '地方标准':
        data = download_dbba_pdf(code)
        if data:
            log(f"    ✅ dbba 成功 ({len(data):,} bytes)")
            return data

    elif stype == '行业标准':
        # 行业标准大部分需购买，先尝试 samr
        data = download_samr_pdf(code)
        if data:
            log(f"    ✅ samr 成功 ({len(data):,} bytes)")
            return data

    log(f"    ⚠️  所有来源均未能获取 PDF")
    return b''

# ============================================================
#  主流程
# ============================================================
def run(force_all: bool = False, target_id: str = '', dry_run: bool = False):
    log("=" * 55)
    log("体育标准 PDF 自动下载工具")
    mode = '强制全部' if force_all else f'目标:{target_id}' if target_id else '补充缺失'
    log(f"模式: {mode} {'[预览]' if dry_run else ''}")
    log("=" * 55)

    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            db = json.load(f)
    except FileNotFoundError:
        log(f"❌ 数据文件不存在: {DATA_FILE}")
        return
    except json.JSONDecodeError as e:
        log(f"❌ 数据文件格式错误: {e}")
        return

    standards = db.get('standards', [])
    stats = {'success': 0, 'skip': 0, 'fail': 0}

    for std in standards:
        std_id   = std.get('id', '')
        code     = std.get('code', '')
        pdf_path = DOWNLOAD_DIR / f"{std_id}.pdf"

        # 筛选目标
        if target_id and std_id != target_id and code != target_id:
            continue

        # 跳过已有有效文件
        if not force_all and is_valid_pdf(pdf_path):
            log(f"  ⏭  跳过（已存在）: {code}")
            stats['skip'] += 1
            continue

        if dry_run:
            log(f"  [预览] 将下载: {code} → {pdf_path.name}")
            continue

        # 已废止标准可选择跳过（节省带宽）
        if std.get('status') == '废止':
            log(f"  ⏭  跳过废止标准: {code}")
            stats['skip'] += 1
            continue

        # 下载
        content = download_standard_pdf(std)
        if content:
            if save_pdf(pdf_path, content):
                log(f"  💾 保存成功: {pdf_path.name} ({len(content):,} bytes)")
                stats['success'] += 1
            else:
                log(f"  ❌ 保存失败（非有效PDF）: {code}")
                stats['fail'] += 1
        else:
            stats['fail'] += 1

        # 每次下载间隔，避免被封
        time.sleep(1.5)

    # 汇总
    log("\n" + "=" * 55)
    log(f"✅ 下载完成：成功 {stats['success']} | 跳过 {stats['skip']} | 失败 {stats['fail']}")

    # 输出失败列表（方便手动补充）
    if not dry_run:
        missing = [s['code'] for s in standards
                   if not is_valid_pdf(DOWNLOAD_DIR / f"{s['id']}.pdf")
                   and s.get('status') != '废止']
        if missing:
            log(f"\n📋 仍缺少 PDF 的标准（共 {len(missing)} 个）：")
            for c in missing:
                log(f"   • {c}")
            # 写入缺失清单
            missing_file = ROOT / 'data' / 'missing_pdfs.txt'
            missing_file.write_text('\n'.join(missing), encoding='utf-8')
            log(f"\n   缺失清单已写入: {missing_file}")

# ============================================================
#  入口
# ============================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='体育标准 PDF 自动下载工具')
    parser.add_argument('--all',  action='store_true', help='强制重新下载全部（覆盖已有文件）')
    parser.add_argument('--id',   type=str, default='',  help='只下载指定标准（填 id 或 code）')
    parser.add_argument('--dry',  action='store_true', help='预览模式，不实际下载')
    args = parser.parse_args()
    run(force_all=args.all, target_id=args.id, dry_run=args.dry)
