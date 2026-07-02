/**
 * scripts/generate-static-pages.js
 *
 * 在 GitHub Actions 里运行（见 .github/workflows/generate-pages.yml）。
 * 直接读取 data/standards.json，为每条标准生成静态详情页 s/{id}.html，
 * 并生成/覆盖 sitemap.xml 和 robots.txt。
 *
 * 因为是在 CI 服务器本地直接读写文件系统，不经过任何网络 API 调用，
 * 1291 条数据全量生成只需几秒钟，远快于浏览器端逐条调 GitHub API 的方式。
 *
 * 触发时机：
 *   ① data/standards.json 发生变化时自动触发（推送至GitHub即生效）
 *   ② 在 admin.html 后台点击"生成静态详情页"按钮时，通过 workflow_dispatch 手动触发
 */

import fs from 'fs';
import path from 'path';

const ROOT       = process.cwd();
const DATA_PATH  = path.join(ROOT, 'data', 'standards.json');
const PAGES_DIR  = path.join(ROOT, 's');
const SITE_BASE  = 'https://www.sportstd.cn';

// ── HTML 转义 ──
function eh(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function ehAttr(s) {
  return eh(s).replace(/"/g, '&quot;');
}

// ── 生成单条标准的静态详情页 HTML（与 admin.html 里的模板保持一致）──
function buildStaticPageHtml(s) {
  const catText = Array.isArray(s.category) ? s.category.join('、') : (s.category || '');
  const summary = s.summary && s.summary.trim()
    ? s.summary.trim()
    : `${s.code} ${s.title}，由${s.issuedBy || '相关主管部门'}发布${catText ? '，适用于' + catText + '相关领域' : ''}。`;
  const metaDesc = `${s.code} ${s.title} - ${summary}`.slice(0, 150);
  const statusColor = s.status === '废止' ? '#dc2626' : (s.status === '即将实施' ? '#d97706' : '#16a34a');
  const statusIcon  = s.status === '废止' ? '🚫' : (s.status === '即将实施' ? '🔔' : '✅');
  const mainSiteLink = `${SITE_BASE}/?id=${encodeURIComponent(s.id)}`;
  const pdfExists = !!s.localFile;

  const infoRows = [
    ['标准类型', eh(s.type || '—')],
    ['强制程度', s.isMandatory ? '⚡ 强制性标准' : '推荐性标准'],
    ['当前状态', `<span style="color:${statusColor};font-weight:700">${statusIcon} ${eh(s.status || '现行')}</span>`],
    ['发布日期', eh(s.issueDate || '—')],
    ['实施日期', eh(s.implementDate || '—')],
    ['发布机构', eh(s.issuedBy || '—')],
    ['标准分类', eh(catText || '—')],
  ];
  if (s.replaces)   infoRows.push(['替代旧标准', eh(s.replaces)]);
  if (s.replacedBy) infoRows.push(['已被替代为', `<span style="color:#dc2626">${eh(s.replacedBy)}</span>`]);

  const infoHtml = infoRows.map(r => `<tr><th>${r[0]}</th><td>${r[1]}</td></tr>`).join('');

  const abolishNotice = s.status === '废止'
    ? `<div class="notice">⚠️ 此标准已废止${s.replacedBy ? `，已被 <strong>${eh(s.replacedBy)}</strong> 代替` : ''}，请使用最新有效版本。</div>`
    : '';

  const jsonLd = JSON.stringify({
    '@context': 'https://schema.org',
    '@type': 'TechArticle',
    headline: `${s.code} ${s.title}`,
    description: summary,
    datePublished: s.issueDate || undefined,
    publisher: { '@type': 'Organization', name: s.issuedBy || '体育建设标准查询平台' },
  });

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${eh(s.code)} ${eh(s.title)} - 体育建设标准查询平台</title>
<meta name="description" content="${ehAttr(metaDesc)}">
<link rel="canonical" href="${SITE_BASE}/s/${encodeURIComponent(s.id)}.html">
<meta property="og:title" content="${ehAttr(s.code + ' ' + s.title)}">
<meta property="og:description" content="${ehAttr(metaDesc)}">
<meta property="og:type" content="article">
<meta property="og:url" content="${SITE_BASE}/s/${encodeURIComponent(s.id)}.html">
<script type="application/ld+json">${jsonLd}<\/script>
<style>
  body{font-family:"PingFang SC",system-ui,-apple-system,sans-serif;line-height:1.8;color:#334155;max-width:800px;margin:40px auto;padding:20px;background:#f4f6f9}
  .card{background:#fff;padding:32px 36px;border-radius:12px;box-shadow:0 4px 6px -1px rgba(0,0,0,.1)}
  .back{display:inline-block;margin-bottom:16px;color:#2563eb;text-decoration:none;font-size:14px}
  h1{color:#1e293b;font-size:22px;border-bottom:2px solid #2563eb;padding-bottom:12px;margin:0 0 6px}
  .code{display:inline-block;font-family:monospace;font-size:14px;color:#2563eb;background:#eff6ff;padding:3px 10px;border-radius:6px;margin-bottom:14px}
  table{width:100%;border-collapse:collapse;margin:18px 0;font-size:14px}
  th{text-align:left;color:#64748b;width:110px;padding:8px 10px;font-weight:500;vertical-align:top}
  td{padding:8px 10px;color:#1e293b}
  tr{border-bottom:1px solid #f1f5f9}
  .notice{background:#fef2f2;border:1px solid #fecaca;color:#dc2626;border-radius:8px;padding:12px 14px;font-size:13px;margin:16px 0}
  .summary{background:#f8fafc;border-radius:8px;padding:14px 16px;font-size:14px;color:#475569;margin:16px 0}
  .cta{display:inline-block;margin-top:10px;background:#2563eb;color:#fff;text-decoration:none;padding:11px 22px;border-radius:8px;font-size:14px;font-weight:600}
  .cta:hover{background:#1d4ed8}
  .nopdf{color:#ea580c;font-size:13px;margin-top:10px}
</style>
</head>
<body>
<div class="card">
  <a href="${SITE_BASE}/" class="back">← 返回首页查询更多标准</a>
  <div class="code">${eh(s.code)}</div>
  <h1>${eh(s.title)}</h1>
  ${abolishNotice}
  <table>${infoHtml}</table>
  <div class="summary">${eh(summary)}</div>
  <a href="${mainSiteLink}" class="cta">🔍 在线查询页查看完整详情${pdfExists ? '与下载PDF' : ''}</a>
  ${!pdfExists ? '<div class="nopdf">📭 该标准PDF文件暂未收录，如需获取请联系平台</div>' : ''}
</div>
</body>
</html>`;
}

// ── 生成 sitemap.xml ──
function buildSitemap(stds) {
  const today = new Date().toISOString().slice(0, 10);
  const urls = [
    { loc: `${SITE_BASE}/`, priority: '1.0', freq: 'daily' },
    { loc: `${SITE_BASE}/about.html`, priority: '0.5', freq: 'monthly' },
    { loc: `${SITE_BASE}/contact.html`, priority: '0.5', freq: 'monthly' },
    ...stds.map(s => ({ loc: `${SITE_BASE}/s/${encodeURIComponent(s.id)}.html`, priority: '0.8', freq: 'weekly' })),
  ];
  let xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n';
  for (const u of urls) {
    xml += `  <url>\n    <loc>${u.loc}</loc>\n    <lastmod>${today}</lastmod>\n    <changefreq>${u.freq}</changefreq>\n    <priority>${u.priority}</priority>\n  </url>\n`;
  }
  xml += '</urlset>';
  return xml;
}

function buildRobots() {
  return `User-agent: *\nAllow: /\nSitemap: ${SITE_BASE}/sitemap.xml\n`;
}

// ── 主流程 ──
function main() {
  if (!fs.existsSync(DATA_PATH)) {
    console.error('❌ 未找到 data/standards.json，终止');
    process.exit(1);
  }
  const raw = fs.readFileSync(DATA_PATH, 'utf-8');
  const data = JSON.parse(raw);
  const stds = data.standards || [];
  console.log(`📦 读取到 ${stds.length} 条标准数据`);

  // 清理旧的静态页目录，避免标准被删除后遗留孤儿页面
  if (fs.existsSync(PAGES_DIR)) {
    fs.rmSync(PAGES_DIR, { recursive: true, force: true });
  }
  fs.mkdirSync(PAGES_DIR, { recursive: true });

  let ok = 0, fail = 0;
  for (const s of stds) {
    if (!s.id) { fail++; continue; }
    // ⚠️ 防御性校验：id 正常应由 admin.html 自动生成（纯字母数字下划线），但此脚本
    //    直接用 id 拼接文件路径写盘，若 standards.json 未来被其它工具或手工编辑，
    //    混入 "../" 之类的字符可能导致写出到仓库预期目录之外。此处做白名单过滤兜底。
    const safeId = String(s.id).replace(/[^a-zA-Z0-9_\-]/g, '');
    if (!safeId || safeId !== s.id) {
      console.error(`⚠️ 跳过不安全的 id: ${JSON.stringify(s.id)}（标准: ${s.code || '未知'}）`);
      fail++;
      continue;
    }
    try {
      const html = buildStaticPageHtml(s);
      fs.writeFileSync(path.join(PAGES_DIR, `${safeId}.html`), html, 'utf-8');
      ok++;
    } catch (e) {
      console.error(`⚠️ 生成失败: ${s.code || s.id} — ${e.message}`);
      fail++;
    }
  }
  console.log(`✅ 静态详情页生成完成：成功 ${ok} 条，失败 ${fail} 条`);

  fs.writeFileSync(path.join(ROOT, 'sitemap.xml'), buildSitemap(stds), 'utf-8');
  fs.writeFileSync(path.join(ROOT, 'robots.txt'), buildRobots(), 'utf-8');
  console.log('✅ sitemap.xml / robots.txt 已更新');
}

main();
