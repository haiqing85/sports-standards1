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

      // 8. 清理 Blob 临时数据（异步，不阻塞响应）
      Promise.all(
        Array.from({ length: totalChunks }, (_, i) =>
          store.delete(`pdf-chunks/${uploadId}/${i}`).catch(() => {})
        )
      );

      return json({ ok: true, path: `downloads/${stdId}.pdf` });

    } catch (e) {
      // 上传失败时也清理 Blob（尽力）
      Promise.all(
        Array.from({ length: totalChunks }, (_, i) =>
          store.delete(`pdf-chunks/${uploadId}/${i}`).catch(() => {})
        )
      );
      return json({ error: "GitHub 推送失败: " + e.message }, 500);
    }
  }

  return json({ error: "未知 action，支持 chunk / finalize" }, 400);
}
