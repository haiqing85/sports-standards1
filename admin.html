// 边缘函数：处理admin后台所有服务端逻辑，安全读取环境变量
const GITHUB_OWNER = 'haiqing85'; // 你的GitHub用户名
const GITHUB_REPO = 'sports-standards1'; // 你的仓库名
const GITHUB_DATA_PATH = 'standards.json'; // 数据文件在仓库里的路径
const GITHUB_BRANCH = 'main'; // 你的仓库主分支（如果是master请修改）

// 路由分发
export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);
  const pathname = url.pathname.replace(/\/$/, '');

  // CORS 跨域处理
  const headers = {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type'
  };

  // 预检请求处理
  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers });
  }

  // 登录接口
  if (pathname === '/api/admin/login' && request.method === 'POST') {
    try {
      const { password } = await request.json();
      // 从EdgeOne环境变量读取正确的管理员密码
      const RIGHT_PASSWORD = env.ADMIN_PASSWORD;

      if (!RIGHT_PASSWORD) {
        return new Response(JSON.stringify({ code: 500, msg: '管理员密码未配置' }), { status: 500, headers });
      }

      if (password === RIGHT_PASSWORD) {
        return new Response(JSON.stringify({ code: 0, msg: '登录成功' }), { status: 200, headers });
      } else {
        return new Response(JSON.stringify({ code: 401, msg: '密码错误，请重试' }), { status: 401, headers });
      }
    } catch (err) {
      return new Response(JSON.stringify({ code: 500, msg: '请求解析失败' }), { status: 500, headers });
    }
  }

  // 获取标准数据接口
  if (pathname === '/api/admin/get-data' && request.method === 'GET') {
    try {
      const githubToken = env.GITHUB_TOKEN;
      if (!githubToken) {
        return new Response(JSON.stringify({ code: 500, msg: 'GitHub Token未配置' }), { status: 500, headers });
      }

      // 调用GitHub API获取数据文件
      const githubRes = await fetch(`https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/${GITHUB_DATA_PATH}?ref=${GITHUB_BRANCH}`, {
        method: 'GET',
        headers: {
          'Authorization': `token ${githubToken}`,
          'Accept': 'application/vnd.github.v3+json',
          'User-Agent': 'EdgeOne-Pages-Admin'
        }
      });

      if (!githubRes.ok) {
        // 如果文件不存在，返回空数组
        if (githubRes.status === 404) {
          return new Response(JSON.stringify({ code: 0, data: [] }), { status: 200, headers });
        }
        throw new Error(`GitHub API请求失败：${githubRes.statusText}`);
      }

      const githubData = await githubRes.json();
      // Base64解码内容
      const content = JSON.parse(atob(githubData.content));
      return new Response(JSON.stringify({ code: 0, data: content }), { status: 200, headers });
    } catch (err) {
      console.error('获取数据失败：', err);
      return new Response(JSON.stringify({ code: 500, msg: `获取数据失败：${err.message}` }), { status: 500, headers });
    }
  }

  // 保存标准数据接口
  if (pathname === '/api/admin/save-data' && request.method === 'POST') {
    try {
      const { data } = await request.json();
      const githubToken = env.GITHUB_TOKEN;
      if (!githubToken) {
        return new Response(JSON.stringify({ code: 500, msg: 'GitHub Token未配置' }), { status: 500, headers });
      }

      // 先获取现有文件的sha（用于更新）
      let fileSha = '';
      const getRes = await fetch(`https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/${GITHUB_DATA_PATH}?ref=${GITHUB_BRANCH}`, {
        method: 'GET',
        headers: {
          'Authorization': `token ${githubToken}`,
          'Accept': 'application/vnd.github.v3+json',
          'User-Agent': 'EdgeOne-Pages-Admin'
        }
      });

      if (getRes.ok) {
        const getResult = await getRes.json();
        fileSha = getResult.sha;
      }

      // 提交新内容到GitHub
      const githubRes = await fetch(`https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/${GITHUB_DATA_PATH}`, {
        method: 'PUT',
        headers: {
          'Authorization': `token ${githubToken}`,
          'Accept': 'application/vnd.github.v3+json',
          'User-Agent': 'EdgeOne-Pages-Admin'
        },
        body: JSON.stringify({
          message: 'admin后台更新标准数据',
          content: btoa(JSON.stringify(data, null, 2)),
          sha: fileSha,
          branch: GITHUB_BRANCH
        })
      });

      if (!githubRes.ok) throw new Error(`GitHub提交失败：${githubRes.statusText}`);
      return new Response(JSON.stringify({ code: 0, msg: '保存成功' }), { status: 200, headers });
    } catch (err) {
      console.error('保存数据失败：', err);
      return new Response(JSON.stringify({ code: 500, msg: `保存数据失败：${err.message}` }), { status: 500, headers });
    }
  }

  // 同步到GitHub接口
  if (pathname === '/api/admin/sync-github' && request.method === 'POST') {
    return new Response(JSON.stringify({ code: 0, msg: '同步成功' }), { status: 200, headers });
  }

  // 404
  return new Response(JSON.stringify({ code: 404, msg: '接口不存在' }), { status: 404, headers });
}
