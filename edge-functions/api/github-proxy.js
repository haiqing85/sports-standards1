export async function onRequest(context) {
  const { request, env } = context;
  // 跨域处理：补充允许自定义校验头，解决前端跨域报错
  const corsHeaders = {
    'Access-Control-Allow-Origin': '*', // 生产环境建议换成你自己的域名，安全性更高
    'Access-Control-Allow-Methods': 'GET, PUT, POST, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, X-Admin-Password',
  };

  // 预检OPTIONS请求直接放行
  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  // 【必选安全校验】防止接口被恶意滥用，和前端admin.html的请求头对应
  const inputPassword = request.headers.get('X-Admin-Password');
  if (!inputPassword || inputPassword !== env.ADMIN_PASSWORD) {
    return new Response(JSON.stringify({ error: 'Forbidden: 无权访问' }), {
      status: 403,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' }
    });
  }

  // 从EdgeOne Pages加密环境变量读取GitHub Token，永远不会暴露到前端
  const githubToken = env.GITHUB_TOKEN;
  if (!githubToken) {
    return new Response(JSON.stringify({ error: '服务器未配置GitHub Token' }), {
      status: 500,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' }
    });
  }

  // 解析并拼接GitHub API地址，适配当前文件路径
  const requestUrl = new URL(request.url);
  const githubApiPath = requestUrl.pathname.replace('/github-proxy', '');
  const targetGithubUrl = `https://api.github.com${githubApiPath}${requestUrl.search}`;

  // 处理请求体
  let requestBody;
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    requestBody = await request.text();
  }

  // 转发请求到GitHub API，Token仅在服务端注入，前端无感知
  try {
    const githubResponse = await fetch(targetGithubUrl, {
      method: request.method,
      headers: {
        'Authorization': `token ${githubToken}`,
        'Content-Type': 'application/json',
        'User-Agent': 'EdgeOne-Pages-GitHub-Proxy',
      },
      body: requestBody
    });

    // 透传GitHub返回结果
    const responseData = await githubResponse.text();
    return new Response(responseData, {
      status: githubResponse.status,
      headers: {
        ...corsHeaders,
        'Content-Type': githubResponse.headers.get('Content-Type') || 'application/json'
      }
    });
  } catch (error) {
    return new Response(JSON.stringify({ error: '代理请求失败', detail: error.message }), {
      status: 500,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' }
    });
  }
}
