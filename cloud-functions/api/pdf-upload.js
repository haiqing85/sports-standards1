/**
 * cloud-functions/api/pdf-upload.js
 * URL: /api/pdf-upload
 * 大文件PDF上传专用 Cloud Function（Node.js 运行时）
 *
 * 流程：
 *   1. 浏览器把PDF分成小块（每块 ≤1MB），逐块 POST action=chunk
 *   2. 所有块上传完毕后，浏览器 POST action=finalize
 *   3. Cloud Function 从 Blob Store 拼装完整文件，推送至 GitHub
 *   4. 推送成功后自动清理 Blob 临时数据
 *
 * 所需配置：
 *   - EdgeOne 环境变量 GITHUB_TOKEN（已有）
 *   - 在 EdgeOne Pages 控制台创建名为 "sports-pdf-store" 的 Blob 存储，
 *     并绑定到本项目（绑定后环境变量名即为 "sports-pdf-store"）
 */

import { getStore } from "@edgeone/pages-blob";

const STORE_NAME   = "sports-pdf-store";
const REPO_OWNER   = "haiqing85";
const REPO_NAME    = "sports-standards1";
const CHUNK_EXPIRY = 60 * 60; // 临时分块最长保留 1 小时（秒）

const cors = {
  "Access-Control-Allow-Origin":  "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { ...cors, "Content-Type": "application/json" },
  });

// ── GitHub API 辅助：带统一认证头的 fetch ──
function ghFetch(url, options, token) {
  return fetch(url, {
    ...options,
    headers: {
      Authorization: `token ${token}`,
      Accept: "application/vnd.github.v3+json",
      "Content-Type": "application/json",
      "User-Agent": "EdgeOne-Pages-PDF-Upload",
      ...(options?.headers || {}),
    },
  });
}

async function safeJson(resp) {
  const text = await resp.text();
  try   { return { ok: resp.ok, data: JSON.parse(text) }; }
  catch { return { ok: false, data: null, raw: text.slice(0, 200) }; }
}

// ── 清理超过 CHUNK_EXPIRY 仍未完成 finalize 的孤儿上传分块 ──
// ⚠️ 不使用 store.list()（未在官方文档中确认 Node SDK 的具体签名，
//    盲目假设签名可能引入新的运行时错误）。改为维护一个固定 key
//    "pdf-uploads-index" 存储所有进行中上传的 { uploadId: startedAt } 映射，
//    只依赖已验证可用的 get/set/delete 三个基础方法。
const UPLOAD_INDEX_KEY = "pdf-uploads-index";

async function cleanupStaleUploads(store) {
  let index;
  try {
    const raw = await store.get(UPLOAD_INDEX_KEY);
    index = raw ? JSON.parse(raw) : {};
  } catch { index = {}; }

  const now = Date.now();
  const staleIds = Object.keys(index).filter(id => now - index[id] > CHUNK_EXPIRY * 1000);
  if (!staleIds.length) return;

  for (const uploadId of staleIds) {
    // 逐个探测该 uploadId 下的分块（连续3次不存在即视为已清空，停止探测）
    let missCount = 0;
    for (let i = 0; missCount < 3; i++) {
      try {
        const exists = await store.get(`pdf-chunks/${uploadId}/${i}`);
        if (!exists) { missCount++; continue; }
        missCount = 0;
        await store.delete(`pdf-chunks/${uploadId}/${i}`).catch(() => {});
      } catch { missCount++; }
    }
    delete index[uploadId];
  }
  await store.set(UPLOAD_INDEX_KEY, JSON.stringify(index)).catch(() => {});
}

async function registerUploadStart(store, uploadId) {
  try {
    const raw = await store.get(UPLOAD_INDEX_KEY);
    const index = raw ? JSON.parse(raw) : {};
    index[uploadId] = Date.now();
    await store.set(UPLOAD_INDEX_KEY, JSON.stringify(index));
  } catch { /* 索引更新失败不影响主流程，最坏情况只是该次上传不会被自动清理 */ }
}

async function unregisterUpload(store, uploadId) {
  try {
    const raw = await store.get(UPLOAD_INDEX_KEY);
    const index = raw ? JSON.parse(raw) : {};
    delete index[uploadId];
    await store.set(UPLOAD_INDEX_KEY, JSON.stringify(index));
  } catch { /* 索引清理失败不影响主流程 */ }
}

export default async function onRequest(context) {
  const { request, env } = context;

  // CORS 预检
  if (request.method === "OPTIONS") {
    return new Response(null, { headers: cors });
  }

  if (request.method !== "POST") {
    return json({ error: "Method Not Allowed" }, 405);
  }

  const token = env.GITHUB_TOKEN;
  if (!token) return json({ error: "GITHUB_TOKEN 未配置" }, 500);

  let body;
  try   { body = await request.json(); }
  catch { return json({ error: "无效的 JSON 请求体" }, 400); }

  const { action } = body;
  const store = getStore(STORE_NAME);

  // ══════════════════════════════════════════════
  //  Action: chunk — 接收并存储一个分块
  // ══════════════════════════════════════════════
  if (action === "chunk") {
    const { uploadId, chunkIndex, totalChunks, data } = body;
    if (!uploadId || chunkIndex == null || !totalChunks || !data) {
      return json({ error: "缺少必要字段: uploadId, chunkIndex, totalChunks, data" }, 400);
    }
    const key = `pdf-chunks/${uploadId}/${chunkIndex}`;
    await store.set(key, data);   // data 是本块的 base64 字符串

    // 记录本次上传的起始时间戳，供后续过期清理判断使用
    if (chunkIndex === 0) {
      await registerUploadStart(store, uploadId);
    }

    // ⚠️ 兜底清理：EdgeOne Blob 无内置定时任务能力，这里用"每次上传分块时顺手扫一眼"的
    //    方式做兼职清理——概率性触发（约1/20次请求），扫描所有超过 CHUNK_EXPIRY 仍未
    //    finalize 的"孤儿上传"（例如用户中途关闭浏览器留下的残留分块），删除干净。
    //    概率触发是为了避免每次上传请求都承担扫描开销，同时保证长期运行下孤儿数据不会累积。
    if (Math.random() < 0.05) {
      cleanupStaleUploads(store).catch(() => {}); // 不阻塞当前请求响应
    }

    return json({ ok: true, chunkIndex, uploadId });
  }

  // ══════════════════════════════════════════════
  //  Action: finalize — 拼装分块 → 推送 GitHub
  // ══════════════════════════════════════════════
  if (action === "finalize") {
    const { uploadId, totalChunks, stdId } = body;
    if (!uploadId || !totalChunks || !stdId) {
      return json({ error: "缺少必要字段: uploadId, totalChunks, stdId" }, 400);
    }

    // 1. 从 Blob 拼装完整 base64
    let fullBase64 = "";
    for (let i = 0; i < totalChunks; i++) {
      const chunk = await store.get(`pdf-chunks/${uploadId}/${i}`);
      if (!chunk) return json({ error: `分块 ${i} 不存在，请重新上传` }, 400);
      fullBase64 += chunk;
    }

    try {
      // 2. 获取仓库默认分支
      const repoR  = await safeJson(await ghFetch(
        `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}`, {}, token
      ));
      const branch = repoR.data?.default_branch || "main";

      // 3. 创建 Git blob（这里可以接受大内容，服务器到服务器没有 EdgeOne 边缘的请求体限制）
      const blobR = await safeJson(await ghFetch(
        `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/git/blobs`,
        { method: "POST", body: JSON.stringify({ content: fullBase64, encoding: "base64" }) },
        token
      ));
      if (!blobR.ok) {
        throw new Error("创建 blob 失败: " + (blobR.data?.message || blobR.raw));
      }

      // 4. 获取分支当前 commit → tree
      const refR = await safeJson(await ghFetch(
        `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/git/refs/heads/${branch}`,
        {}, token
      ));
      if (!refR.ok) throw new Error("获取分支引用失败: " + (refR.data?.message || refR.raw));
      const parentSha = refR.data.object.sha;

      const commitR = await safeJson(await ghFetch(
        `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/git/commits/${parentSha}`,
        {}, token
      ));
      if (!commitR.ok) throw new Error("获取父提交失败");
      const baseTreeSha = commitR.data.tree.sha;

      // 5. 新建 tree，只替换这一个文件
      const treeR = await safeJson(await ghFetch(
        `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/git/trees`,
        {
          method: "POST",
          body: JSON.stringify({
            base_tree: baseTreeSha,
            tree: [{ path: `downloads/${stdId}.pdf`, mode: "100644", type: "blob", sha: blobR.data.sha }],
          }),
        },
        token
      ));
      if (!treeR.ok) throw new Error("创建 tree 失败: " + (treeR.data?.message || treeR.raw));

      // 6. 新建 commit
      const newCommitR = await safeJson(await ghFetch(
        `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/git/commits`,
        {
          method: "POST",
          body: JSON.stringify({
            message: `📄 上传PDF: ${stdId}.pdf`,
            tree: treeR.data.sha,
            parents: [parentSha],
          }),
        },
        token
      ));
      if (!newCommitR.ok) throw new Error("创建 commit 失败");

      // 7. 更新分支指针
      const updateR = await safeJson(await ghFetch(
        `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/git/refs/heads/${branch}`,
        { method: "PATCH", body: JSON.stringify({ sha: newCommitR.data.sha }) },
        token
      ));
      if (!updateR.ok) throw new Error("更新分支指针失败");

      // 8. 清理 Blob 临时数据（异步，不阻塞响应），含索引记录
      Promise.all(
        Array.from({ length: totalChunks }, (_, i) =>
          store.delete(`pdf-chunks/${uploadId}/${i}`).catch(() => {})
        ).concat([unregisterUpload(store, uploadId)])
      );

      return json({ ok: true, path: `downloads/${stdId}.pdf` });

    } catch (e) {
      // 上传失败时也清理 Blob（尽力），含索引记录
      Promise.all(
        Array.from({ length: totalChunks }, (_, i) =>
          store.delete(`pdf-chunks/${uploadId}/${i}`).catch(() => {})
        ).concat([unregisterUpload(store, uploadId)])
      );
      return json({ error: "GitHub 推送失败: " + e.message }, 500);
    }
  }

  return json({ error: "未知 action，支持 chunk / finalize" }, 400);
}
