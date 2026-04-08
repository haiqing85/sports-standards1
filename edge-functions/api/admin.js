// 路径：edge-functions/api/admin.js
// URL：POST /api/admin
// 用 context.env 读取 ADMIN_PASSWORD，前端不暴露密码

export async function onRequest(context) {
  const { request, env } = context;

  const cors = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  };

  if (request.method === 'OPTIONS') {
    return new Response(null, { headers: cors });
  }

  if (request.method !== 'POST') {
    return new Response('Method Not Allowed', { status: 405, headers: cors });
  }

  const adminPassword = env.ADMIN_PASSWORD;
  if (!adminPassword) {
    return new Response(
      JSON.stringify({ ok: false, error: 'ADMIN_PASSWORD 未配置' }),
      { status: 500, headers: { ...cors, 'Content-Type': 'application/json' } }
    );
  }

  const { username, password } = await request.json();

  if (username === 'admin' && password === adminPassword) {
    return new Response(
      JSON.stringify({ ok: true }),
      { headers: { ...cors, 'Content-Type': 'application/json' } }
    );
  }

  return new Response(
    JSON.stringify({ ok: false }),
    { status: 401, headers: { ...cors, 'Content-Type': 'application/json' } }
  );
}
