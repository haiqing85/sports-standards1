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

  // ⚠️ 请求体解析必须做异常保护：空 body / 非 JSON 格式都会导致 request.json() 抛出
  //    未捕获异常，EdgeOne 此时会返回非 JSON 的原始错误页，前端 resp.json() 解析时
  //    会崩成 "Unexpected token" 报错（与 PDF 上传曾遇到的问题是同一类根因）。
  let username, password;
  try {
    var body = await request.json();
    username = body.username;
    password = body.password;
  } catch (e) {
    return new Response(
      JSON.stringify({ ok: false, error: '请求体格式错误，需为合法 JSON' }),
      { status: 400, headers: { ...cors, 'Content-Type': 'application/json' } }
    );
  }

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
