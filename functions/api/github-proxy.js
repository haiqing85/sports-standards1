// 路径：edge-functions/api/github-proxy.js
// 作用：代理所有 GitHub API 请求，Token 从 EdgeOne 环境变量读取，不暴露在前端

export async function onRequest(context) {
  const { request, env } = context;

  const cors = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, PUT, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  };

  if (request.method === 'OPTIONS') {
    return new Response(null, { headers: cors });
  }

  const token = env.GITHUB_TOKEN;
  if (!token) {
    return new Response(JSON.stringify({ error: 'GITHUB_TOKEN 未在 EdgeOne 环境变量中配置' }), {
      status: 500,
      headers: { ...cors, 'Content-Type': 'application/json' },
    });
  }

  const url = new URL(request.url);
  // 将 /edge-functions/api/github-proxy/repos/... 转换为 https://api.github.com/repos/...
  const githubPath = url.pathname.replace('/api/github-proxy', '');
  const githubUrl = `https://api.github.com${githubPath}${url.search}`;

  const body = request.method !== 'GET' ? await request.text() : undefined;

  const resp = await fetch(githubUrl, {
    method: request.method,
    headers: {
      'Authorization': `token ${token}`,
      'Content-Type': 'application/json',
      'Accept': 'application/vnd.github.v3+json',
      'User-Agent': 'EdgeOne-Pages-Proxy',
    },
    body,
  });

  const data = await resp.text();
  return new Response(data, {
    status: resp.status,
    headers: { ...cors, 'Content-Type': 'application/json' },
  });
}
