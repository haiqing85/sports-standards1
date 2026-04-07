export async function onRequest(context) {
  const { request, env } = context;

  // 跨域处理
  const cors = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, PUT, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  };
  if (request.method === 'OPTIONS') {
    return new Response(null, { headers: cors });
  }

  // Token 从环境变量读取
  const token = env.GITHUB_TOKEN;
  if (!token) {
    return new Response(JSON.stringify({ error: 'Token not configured' }), {
      status: 500, headers: cors
    });
  }

  // 解析目标 GitHub API 路径
  const url = new URL(request.url);
  const githubPath = url.pathname.replace('/github-proxy', '');
  const githubUrl = `https://api.github.com${githubPath}${url.search}`;

  const body = request.method !== 'GET' ? await request.text() : undefined;

  const resp = await fetch(githubUrl, {
    method: request.method,
    headers: {
      'Authorization': `token ${token}`,
      'Content-Type': 'application/json',
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
