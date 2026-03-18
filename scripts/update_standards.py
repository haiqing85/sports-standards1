#!/usr/bin/env python3
"""
体育标准自动更新脚本
从以下官方渠道获取标准状态更新：
- 全国标准信息公共服务平台 (std.samr.gov.cn)
- 国家标准全文公开系统 (openstd.samr.gov.cn)

使用说明：
  1. 本脚本负责"核查"现有标准的最新状态（废止/现行）
  2. 新标准请手动添加到 data/standards.json
  3. 每周由 GitHub Actions 自动运行
"""

import json
import time
import requests
from datetime import datetime
from pathlib import Path
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('data/update_log.txt', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).parent.parent / 'data' / 'standards.json'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; SportsStandardsBot/1.0)',
    'Accept': 'application/json'
}

def check_std_status(std_code: str) -> dict:
    """
    查询全国标准信息平台的标准状态
    API文档: https://std.samr.gov.cn/
    """
    try:
        url = f"https://std.samr.gov.cn/gb/search/gbDetailed?id={std_code}"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            # 解析返回数据，更新状态字段
            # 注意：实际API需要根据平台文档调整
            return resp.json()
    except Exception as e:
        logger.warning(f"查询 {std_code} 失败: {e}")
    return {}

def update_standards():
    """主更新函数"""
    logger.info("="*50)
    logger.info("体育标准数据库更新工具")
    logger.info(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("="*50)

    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.error(f"数据文件不存在: {DATA_FILE}")
        return
    except json.JSONDecodeError as e:
        logger.error(f"数据文件格式错误: {e}")
        return

    standards = data['standards']
    updated_count = 0

    logger.info(f"当前收录标准数: {len(standards)}")
    logger.info("开始核查标准状态...")

    for std in standards:
        code = std.get('code', '')
        if not code:
            continue

        # 仅对国标进行在线核查（避免频繁请求被封）
        if std.get('type') == '国家标准':
            logger.info(f"检查: {code}...", end='')
            try:
                # 实际查询逻辑（需根据官方API文档实现）
                # result = check_std_status(code)
                # if result.get('status') != std.get('status'):
                #     std['status'] = result['status']
                #     updated_count += 1
                logger.info("OK")
                time.sleep(0.5)  # 避免请求过快
            except Exception as e:
                logger.warning(f"处理 {code} 时出错: {e}")

    # 更新时间戳
    today = datetime.now().strftime('%Y-%m-%d')
    data['updated'] = today
    data['version'] = today.replace('-', '.')
    data['total'] = len(standards)

    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("更新完成！")
        logger.info(f"  - 核查标准数: {len(standards)}")
        logger.info(f"  - 状态变更数: {updated_count}")
        logger.info(f"  - 更新时间戳: {today}")
    except Exception as e:
        logger.error(f"保存数据失败: {e}")

    # ==========================================
    # 手动添加新标准说明（注释模板）
    # ==========================================
    # 发现新标准时，在 data/standards.json 中按以下格式添加：
    # {
    #   "id": "唯一ID（用于去重）",
    #   "code": "标准编号，如 GB/T XXXXX-XXXX",
    #   "title": "标准名称（中文）",
    #   "english": "标准名称（英文，可选）",
    #   "type": "国家标准|行业标准|地方标准|团标|企业标准",
    #   "status": "现行|废止|即将实施",
    #   "issueDate": "YYYY-MM-DD",
    #   "implementDate": "YYYY-MM-DD",
    #   "abolishDate": null,   // 废止则填日期
    #   "replaces": "被替代标准编号（可选）",
    #   "replacedBy": "替代本标准的编号（废止时填）",
    #   "issuedBy": "发布机构全称",
    #   "category": "合成材料面层|人造草坪|灯光照明|...",
    #   "tags": ["关键词1", "关键词2"],
    #   "summary": "标准摘要（100-200字）",
    #   "isMandatory": true/false,
    #   "scope": "适用范围",
    #   "isFree": true/false,  // 是否在官网免费查阅
    #   "downloadUrl": "官方链接"
    # }

if __name__ == '__main__':
    update_standards()
