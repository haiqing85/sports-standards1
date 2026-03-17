# 体育建设标准查询平台

> 全国最全体育行业标准数据库，覆盖国标、行标、地标、团标，完全免费部署。

---

## 🌐 技术架构（零成本方案）

```
GitHub 仓库（数据存储 + 自动更新）
      ↓  GitHub Actions 每周抓取最新状态
data/standards.json（标准数据）
      ↓  腾讯 EdgeOne Pages 托管
用户浏览器（前端渲染 + Fuse.js 搜索）
```

**完全免费清单：**
| 服务 | 用途 | 免费额度 |
|------|------|---------|
| GitHub Pages / EdgeOne Pages | 静态网站托管 | 永久免费 |
| GitHub Actions | 自动更新脚本 | 2000分钟/月 |
| GitHub 仓库 | 数据存储 | 1GB 免费 |
| Fuse.js CDN | 全文模糊搜索 | 完全免费 |
| Google AdSense | 广告变现 | 按点击收费 |

---

## 🚀 部署到腾讯 EdgeOne Pages（推荐）

### 步骤一：Fork 本仓库
点击右上角 **Fork** 按钮，创建你自己的仓库副本。

### 步骤二：开通 EdgeOne Pages
1. 登录 [腾讯云控制台](https://console.cloud.tencent.com/)
2. 搜索 **EdgeOne** → 进入 **Pages** 功能
3. 点击 **创建项目** → 选择 **连接 GitHub**
4. 授权 GitHub 后，选择你 Fork 的仓库
5. 构建配置：
   - 构建命令：`（留空，纯静态无需构建）`
   - 输出目录：`/`（根目录）
   - 框架：`其他`
6. 点击 **部署** → 等待 1~2 分钟完成

### 步骤三：配置自定义域名（可选）
在 EdgeOne Pages 控制台绑定你的域名，自动开启 HTTPS。

---

## 📊 数据管理

### 手动添加新标准
编辑 `data/standards.json`，按模板格式添加：
```json
{
  "id": "GBT12345-2024",
  "code": "GB/T 12345-2024",
  "title": "标准名称",
  "type": "国家标准",
  "status": "现行",
  "issueDate": "2024-01-01",
  "implementDate": "2024-07-01",
  "abolishDate": null,
  "issuedBy": "发布机构",
  "category": "合成材料面层",
  "tags": ["关键词"],
  "summary": "标准简介",
  "isMandatory": false,
  "scope": "适用范围",
  "isFree": true,
  "downloadUrl": "https://openstd.samr.gov.cn/"
}
```

### 标记废止标准
将对应标准的 `status` 改为 `"废止"`，并填写：
```json
"abolishDate": "2025-01-01",
"replacedBy": "GB/T XXXXX-XXXX"
```

### 数据来源（官方渠道）
- 🇨🇳 国家标准：[openstd.samr.gov.cn](https://openstd.samr.gov.cn/)
- 📋 全品类检索：[std.samr.gov.cn](https://std.samr.gov.cn/)
- 🤝 团体标准：[ttbz.org.cn](https://www.ttbz.org.cn/)
- 🏗️ 建工行标：[mohurd.gov.cn](https://www.mohurd.gov.cn/)

---

## 💰 接入 Google AdSense

1. 申请 [Google AdSense](https://adsense.google.com/) 账号
2. 验证域名所有权（在 `index.html` 的 `<head>` 中插入验证代码）
3. 等待审核通过（通常 1-4 周）
4. 替换 `index.html` 中的广告占位符：

```html
<!-- 将 hero 下方的占位符替换为 -->
<ins class="adsbygoogle"
     style="display:block"
     data-ad-client="ca-pub-你的ID"
     data-ad-slot="你的广告单元ID"
     data-ad-format="auto"
     data-full-width-responsive="true"></ins>
<script>(adsbygoogle = window.adsbygoogle || []).push({});</script>
```

5. 取消 `index.html` 头部 AdSense 脚本的注释

---

## 📁 项目结构

```
sports-standards/
├── index.html              # 主页面（单文件，包含所有逻辑）
├── data/
│   └── standards.json      # 标准数据库（JSON格式）
├── scripts/
│   └── update_standards.py # 自动更新脚本
├── .github/
│   └── workflows/
│       └── update-standards.yml  # GitHub Actions 配置
└── README.md
```

---

## 📞 数据贡献

发现标准数据有误？新标准未收录？欢迎提交 Issue 或 Pull Request。
