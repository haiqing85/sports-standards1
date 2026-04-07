/**
 * EdgeOne Pages  GitHub API 安全代理
 * 适配路径：/api/github-proxy
 * 所有敏感信息从加密环境变量读取，公开仓库无任何密钥泄露风险
 */
export async function onRequest(context) {
  const { request, env } = context;

  // 跨域配置（生产环境建议把*替换成你的域名，提升安全性）
  const corsHeaders = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, PUT, POST, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, X-Admin-Password',
  };

  // 处理预检OPTIONS请求，解决前端跨域报错
  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  // 【核心安全校验】只有密码匹配才能调用接口，防止恶意滥用
  const requestPassword = request.headers.get('X-Admin-Password');
  if (!requestPassword || requestPassword !== env.ADMIN_PASSWORD) {
    return new Response(
      JSON.stringify({ success: false, error: '无权访问：管理员密码校验失败' }),
      { status: 403, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    );
  }

  // 从EdgeOne加密环境变量读取GitHub Token，永远不会暴露到前端
  const githubToken = env.GITHUB_TOKEN;
  if (!githubToken) {
    return new Response(
      JSON.stringify({ success: false, error: '服务器配置错误：未设置GitHub Token' }),
      { status: 500, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    );
  }

  // 解析目标GitHub API路径，适配当前接口路由
  const requestUrl = new URL(request.url);
  // 移除/api/github-proxy前缀，拼接完整的GitHub API地址
  const githubApiPath = requestUrl.pathname.replace(/^\/api\/github-proxy/, '');
  const targetGithubUrl = `https://api.github.com${githubApiPath}${requestUrl.search}`;

  // 处理请求体
  let requestBody = undefined;
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    try {
      requestBody = await request.text();
    } catch (e) {
      requestBody = undefined;
    }
  }

  // 转发请求到GitHub官方API，Token仅在服务端注入
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

    // 透传GitHub的返回结果
    const responseData = await githubResponse.text();
    return new Response(responseData, {
      status: githubResponse.status,
      headers: {
        ...corsHeaders,
        'Content-Type': githubResponse.headers.get('Content-Type') || 'application/json'
      }
    });
  } catch (error) {
    return new Response(
      JSON.stringify({ success: false, error: '代理请求失败', detail: error.message }),
      { status: 500, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    );
  }
}
