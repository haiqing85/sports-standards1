// 路径：middleware.js（仓库根目录）
// 用 HTTP Basic Auth 保护 /admin.html，密码从 EdgeOne 环境变量读取

export const config = {
  matcher: ['/admin.html'],
};

export function middleware(context) {
  const { request, env } = context;

  const adminPassword = env.ADMIN_PASSWORD;
  if (!adminPassword) {
    // 环境变量未配置时拒绝访问，避免裸奔
    return new Response('Server misconfigured: ADMIN_PASSWORD not set', { status: 500 });
  }

  const authHeader = request.headers.get('Authorization');

  if (authHeader && authHeader.startsWith('Basic ')) {
    const base64 = authHeader.slice(6);
    const decoded = atob(base64);               // "admin:password"
    const [user, pass] = decoded.split(':');
    if (user === 'admin' && pass === adminPassword) {
      return context.next();                    // 验证通过，放行
    }
  }

  // 未认证：触发浏览器弹出登录框
  return new Response('Unauthorized', {
    status: 401,
    headers: {
      'WWW-Authenticate': 'Basic realm="管理后台"',
    },
  });
}
