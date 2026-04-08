// 路径：edge-functions/api/github-proxy.js
// URL：/api/github-proxy?url=https://api.github.com/...
// Token 从 context.env.GITHUB_TOKEN 读取，前端不暴露

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
    return new Response(
      JSON.stringify({ error: 'GITHUB_TOKEN 未配置' }),
      { status: 500, headers: { ...cors, 'Content-Type': 'application/json' } }
    );
  }

  const reqUrl = new URL(request.url);
  const targetUrl = reqUrl.searchParams.get('url');
  if (!targetUrl) {
    return new Response(
      JSON.stringify({ error: '缺少 url 参数' }),
      { status: 400, headers: { ...cors, 'Content-Type': 'application/json' } }
    );
  }

  const body = request.method !== 'GET' ? await request.text() : undefined;

  const resp = await fetch(targetUrl, {
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
