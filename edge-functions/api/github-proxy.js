export async function onRequest(context) {
  const { request, env } = context;
  // 跨域配置，解决前端请求报错
  const corsHeaders = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, PUT, POST, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, X-Admin-Password',
  };

  // 预检OPTIONS请求，必须优先处理，否则前端请求会失败
  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  const requestUrl = new URL(request.url);
  // ========== 登录密码校验接口 ==========
  if (requestUrl.pathname === '/api/github-proxy/login-check') {
    try {
      const body = await request.json();
      const inputPassword = body.password || '';
      // 服务端比对环境变量里的ADMIN_PASSWORD
      if (inputPassword === env.ADMIN_PASSWORD) {
        return new Response(JSON.stringify({ success: true, message: '登录成功' }), {
          status: 200,
          headers: { ...corsHeaders, 'Content-Type': 'application/json' }
        });
      } else {
        return new Response(JSON.stringify({ success: false, message: '管理员密码错误' }), {
          status: 403,
          headers: { ...corsHeaders, 'Content-Type': 'application/json' }
        });
      }
    } catch (error) {
      return new Response(JSON.stringify({ success: false, message: '登录校验失败：' + error.message }), {
        status: 500,
        headers: { ...corsHeaders, 'Content-Type': 'application/json' }
      });
    }
  }

  // ========== GitHub代理接口权限校验 ==========
  const inputPassword = request.headers.get('X-Admin-Password');
  if (!inputPassword || inputPassword !== env.ADMIN_PASSWORD) {
    return new Response(JSON.stringify({ error: '无权访问：密码校验失败' }), {
      status: 403,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' }
    });
  }

  // ========== GitHub代理转发 ==========
  const githubToken = env.GITHUB_TOKEN;
  if (!githubToken) {
    return new Response(JSON.stringify({ error: '服务器未配置GitHub Token' }), {
      status: 500,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' }
    });
  }

  const githubApiPath = requestUrl.pathname.replace('/api/github-proxy', '');
  const targetGithubUrl = `https://api.github.com${githubApiPath}${requestUrl.search}`;

  let requestBody;
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    try {
      requestBody = await request.text();
    } catch (e) {
      requestBody = undefined;
    }
  }

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
