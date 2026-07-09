"""Web Chat 路由模块 — 页面 + API 端点。

所有路由通过 FastMCP custom_route 注册，和 MCP Server 共用一个 uvicorn 进程。
依赖：Starlette（FastMCP 内建），不引入 Flask/FastAPI。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from .auth import AuthManager, cleanup_expired_mfa_states, get_mfa_state
from .coach import chat_stream
from .config import Config, UserConfig
from .memory import MemoryStore
from .storage import Storage
from .users import UserManager

logger = logging.getLogger(__name__)

_COOKIE_NAME = "rundown_key"
_COOKIE_MAX_AGE = 365 * 24 * 3600  # 1 年
_TEMPLATE_DIR = Path(__file__).parent.parent / "web" / "templates"


def _get_api_key(request: Request) -> str | None:
    """从 Cookie 提取 API Key。"""
    return request.cookies.get(_COOKIE_NAME)


def _read_template(name: str) -> str:
    """读取 HTML 模板文件。"""
    path = _TEMPLATE_DIR / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _user_context(memory_store: MemoryStore, storage: Storage) -> str:
    """构建用户上下文文本（注入 AI 对话）。"""
    parts = []

    # 最新日报
    try:
        latest = memory_store.get_latest("daily_report")
        if latest:
            fm = latest.front_matter or {}
            ya = fm.get("yesterday_activities", {})
            sl = fm.get("last_night_sleep", {})
            mo = fm.get("this_morning", {})
            ld = fm.get("training_load", {})
            rec = fm.get("recovery", {})
            ai = fm.get("ai_insight", {})

            lines = ["## 今日数据"]
            if ya.get("is_rest_day"):
                lines.append("- 昨天: 休息日")
            else:
                lines.append(
                    f"- 昨天训练: {ya.get('total_duration_min', 0)}min "
                    f"{ya.get('total_distance_km', 0):.1f}km "
                    f"负荷 {ya.get('total_training_load', 0)}"
                )
            lines.append(
                f"- 昨晚睡眠: {sl.get('total_hours', '?')}h "
                f"评分 {sl.get('sleep_score', '?')}"
            )
            lines.append(
                f"- 今晨: RHR {mo.get('resting_hr', '?')} | "
                f"HRV {mo.get('hrv_ms', '?')}ms ({mo.get('hrv_status', '?')}) | "
                f"电量 {mo.get('body_battery_morning', '?')}"
            )
            lines.append(
                f"- 负荷: ACWR {ld.get('acwr', '?')} "
                f"({ld.get('acwr_status', '?')}) | "
                f"恢复 {rec.get('overall_score', '?')}/100"
            )
            if ai.get("conclusion"):
                lines.append(f"- AI 评估: {ai['conclusion']}")
            parts.append("\n".join(lines))
    except Exception:
        pass

    # 活跃目标
    try:
        goals = memory_store.list_by_type("goal", status="active")
        if goals:
            goal_lines = ["## 活跃目标"]
            for g in goals[:3]:
                fm = g.front_matter or {}
                goal_lines.append(
                    f"- {fm.get('title', g.id)}"
                    f"（目标日期: {fm.get('target_date', '?')}）"
                )
            parts.append("\n".join(goal_lines))
    except Exception:
        pass

    # 竞技档案
    try:
        profile = memory_store.get("fitness-assessment")
        if profile and profile.body:
            parts.append(f"## 运动员档案\n{profile.body[:800]}")
    except Exception:
        pass

    return "\n\n".join(parts) if parts else "暂无数据，请先同步。"


# ── 路由注册入口 ────────────────────────────────


def register_web_routes(server, user_manager: UserManager, config: Config):
    """在 FastMCP server 上注册所有 Web 路由。"""

    # ═══ 页面路由 ═══

    @server.custom_route("/", methods=["GET"])
    async def index(request: Request) -> Response:
        """首页 — 已绑定用户进聊天，未绑定跳转设置页。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None

        if user and user.token_status == "active":
            html = _read_template("chat.html")
            return HTMLResponse(html or _chat_fallback())

        return _redirect("/setup")

    @server.custom_route("/setup", methods=["GET"])
    async def setup_page(request: Request) -> Response:
        """Garmin 绑定页面。已绑定用户跳回首页。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None

        if user and user.token_status == "active":
            return _redirect("/")

        html = _read_template("setup.html")
        return HTMLResponse(html or _setup_fallback())

    # ═══ API 路由 ═══

    @server.custom_route("/api/setup", methods=["POST"])
    async def api_setup(request: Request) -> Response:
        """启动 Garmin 绑定流程。"""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"status": "error", "message": "无效请求"}, status_code=400)

        email = body.get("email", "").strip()
        password = body.get("password", "").strip()
        domain = body.get("domain", "garmin.com").strip()

        if not email or not password:
            return JSONResponse({"status": "error", "message": "请输入邮箱和密码"}, status_code=400)

        # 获取或创建用户
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if user is None:
            user = user_manager.register(garmin_email=email, garmin_domain=domain)
            api_key = user.api_key

        # 创建用户专属配置和 AuthManager
        user_cfg = config.for_user(api_key)
        user_manager.ensure_dirs(api_key)

        auth = AuthManager(user_cfg)

        try:
            session_key = secrets.token_hex(16)
            result = auth.start_login(email, password, session_key)

            if result == "needs_mfa":
                resp = JSONResponse({"status": "needs_mfa", "session": session_key})
            else:
                # 登录成功（无需 MFA）
                user_manager.update(api_key, garmin_email=email,
                                    garmin_domain=domain, token_status="active")
                resp = JSONResponse({"status": "ok"})

            resp.set_cookie(_COOKIE_NAME, api_key, max_age=_COOKIE_MAX_AGE,
                           httponly=True, samesite="lax")
            return resp

        except Exception as exc:
            logger.error("Garmin 登录失败: %s", exc)
            return JSONResponse({"status": "error", "message": f"登录失败: {exc}"}, status_code=400)

    @server.custom_route("/api/mfa", methods=["POST"])
    async def api_mfa(request: Request) -> Response:
        """提交 MFA 验证码完成登录。"""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"status": "error", "message": "无效请求"}, status_code=400)

        mfa_code = body.get("code", "").strip()
        session_key = body.get("session", "").strip()

        if not mfa_code or not session_key:
            return JSONResponse({"status": "error", "message": "缺少验证码或会话"}, status_code=400)

        api_key = _get_api_key(request)
        if not api_key:
            return JSONResponse({"status": "error", "message": "请先发起绑定"}, status_code=400)

        user_cfg = config.for_user(api_key)
        auth = AuthManager(user_cfg)

        try:
            auth.complete_mfa(session_key, mfa_code)
            user_manager.update(api_key, token_status="active")
            return JSONResponse({"status": "ok"})
        except ValueError as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)
        except Exception as exc:
            logger.error("MFA 验证失败: %s", exc)
            return JSONResponse({"status": "error", "message": f"验证失败: {exc}"}, status_code=400)

    @server.custom_route("/api/chat/stream", methods=["POST"])
    async def api_chat_stream(request: Request) -> Response:
        """流式 AI 对话（SSE）。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user or user.token_status != "active":
            return JSONResponse({"status": "error", "message": "请先绑定 Garmin"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"status": "error", "message": "无效请求"}, status_code=400)

        messages = body.get("messages", [])

        # 构建用户上下文
        user_cfg = config.for_user(api_key)
        user_manager.ensure_dirs(api_key)
        storage = Storage(user_cfg)
        memory_store = MemoryStore(user_cfg.memory_dir,
                                   db_getter=lambda: storage.db)
        context = _user_context(memory_store, storage)

        async def generate():
            async for token in chat_stream(messages, context=context):
                yield f"data: {json.dumps({'token': token})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    @server.custom_route("/api/sync", methods=["POST"])
    async def api_sync(request: Request) -> Response:
        """触发数据同步 + 生成日报。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user or user.token_status != "active":
            return JSONResponse({"status": "error", "message": "请先绑定 Garmin"}, status_code=401)

        user_cfg = config.for_user(api_key)
        user_manager.ensure_dirs(api_key)

        try:
            # 认证
            auth = AuthManager(user_cfg)
            _ = auth.client  # 触发登录/Token 检查

            # 同步
            storage = Storage(user_cfg)
            user_id = auth.client.user_id if hasattr(auth.client, 'user_id') else 0
            today = date.today()
            start = today - timedelta(days=config.sync_days)

            # 初始化并同步
            storage.sync_manager.initialize(email=user.garmin_email, password="")
            storage.reset_pending_metrics(user_id, start, today)
            storage.sync_range(user_id, start, today)

            # 生成日报
            memory_store = MemoryStore(user_cfg.memory_dir,
                                       db_getter=lambda: storage.db)
            mem = memory_store.generate_daily_report(str(user_id), today)

            # 备份
            storage.backup_to(user_manager.get_backup_path(api_key))

            # 更新最后同步时间
            user_manager.update(api_key, last_sync=str(today))

            return JSONResponse({
                "status": "ok",
                "message": f"同步完成，已生成 {today} 日报",
            })

        except Exception as exc:
            logger.error("同步失败: %s", exc)
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)

    @server.custom_route("/api/status", methods=["GET"])
    async def api_status(request: Request) -> Response:
        """查询用户状态。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None

        if not user:
            return JSONResponse({"bound": False})

        return JSONResponse({
            "bound": True,
            "token_status": user.token_status,
            "garmin_email": user.garmin_email,
            "last_sync": user.last_sync,
        })

    @server.custom_route("/api/logout", methods=["POST"])
    async def api_logout(request: Request) -> Response:
        """退出登录。"""
        resp = JSONResponse({"status": "ok"})
        resp.delete_cookie(_COOKIE_NAME)
        return resp


# ── 辅助 ────────────────────────────────────────


def _redirect(url: str) -> Response:
    return Response(status_code=302, headers={"Location": url})


# ── 内联 HTML 兜底（模板文件不存在时使用）─────────


def _setup_fallback() -> str:
    return """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rundown — 绑定 Garmin</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0f0f0f;color:#e0e0e0;
  display:flex;justify-content:center;align-items:center;min-height:100vh;padding:20px}
.card{background:#1a1a1a;border-radius:16px;padding:32px 24px;max-width:400px;width:100%}
h1{font-size:20px;margin-bottom:8px;color:#fff}
p.sub{font-size:14px;color:#888;margin-bottom:24px}
label{display:block;font-size:13px;color:#aaa;margin-bottom:4px;margin-top:16px}
input{width:100%;padding:12px;border-radius:10px;border:1px solid #333;background:#0f0f0f;
  color:#fff;font-size:16px;outline:none}
input:focus{border-color:#4caf50}
button{width:100%;padding:14px;border-radius:10px;border:none;background:#4caf50;color:#fff;
  font-size:16px;font-weight:600;margin-top:24px;cursor:pointer}
button:disabled{opacity:.5}
#mfa-section{display:none}
#error{color:#f44336;font-size:14px;margin-top:12px;display:none}
#success{color:#4caf50;font-size:14px;margin-top:12px;display:none}
</style>
</head>
<body>
<div class="card">
<h1>🔗 绑定 Garmin</h1>
<p class="sub">连接你的 Garmin 账号，AI 教练需要你的运动数据</p>
<div id="setup-section">
  <label>Garmin 邮箱</label>
  <input id="email" type="email" placeholder="your@email.com" autocomplete="email">
  <label>Garmin 密码</label>
  <input id="password" type="password" placeholder="Garmin 密码" autocomplete="current-password">
  <button id="setup-btn" onclick="startSetup()">开始绑定</button>
</div>
<div id="mfa-section">
  <p>请输入 Garmin 发送的二次验证码</p>
  <label>MFA 验证码</label>
  <input id="mfa-code" type="text" placeholder="6 位数字" inputmode="numeric">
  <button id="mfa-btn" onclick="submitMfa()">验证</button>
</div>
<div id="error"></div>
<div id="success"></div>
</div>
<script>
let mfaSession = '';
function showError(msg){const e=document.getElementById('error');e.textContent=msg;e.style.display='block'}
function hideError(){document.getElementById('error').style.display='none'}
async function startSetup(){
  hideError();
  const email=document.getElementById('email').value.trim();
  const password=document.getElementById('password').value.trim();
  if(!email||!password){showError('请输入邮箱和密码');return}
  const btn=document.getElementById('setup-btn');btn.disabled=true;btn.textContent='登录中...';
  try{
    const r=await fetch('/api/setup',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({email,password})});
    const d=await r.json();
    if(d.status==='needs_mfa'){
      mfaSession=d.session;
      document.getElementById('setup-section').style.display='none';
      document.getElementById('mfa-section').style.display='block';
    }else if(d.status==='ok'){
      document.getElementById('success').textContent='绑定成功！即将跳转...';
      document.getElementById('success').style.display='block';
      setTimeout(()=>location.href='/',1500);
    }else{
      showError(d.message||'绑定失败');
    }
  }catch(e){showError('网络错误: '+e.message)}
  btn.disabled=false;btn.textContent='开始绑定';
}
async function submitMfa(){
  hideError();
  const code=document.getElementById('mfa-code').value.trim();
  if(!code){showError('请输入验证码');return}
  const btn=document.getElementById('mfa-btn');btn.disabled=true;btn.textContent='验证中...';
  try{
    const r=await fetch('/api/mfa',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({code,session:mfaSession})});
    const d=await r.json();
    if(d.status==='ok'){
      document.getElementById('success').textContent='绑定成功！即将跳转...';
      document.getElementById('success').style.display='block';
      setTimeout(()=>location.href='/',1500);
    }else{
      showError(d.message||'MFA 验证失败');
    }
  }catch(e){showError('网络错误: '+e.message)}
  btn.disabled=false;btn.textContent='验证';
}
</script>
</body>
</html>"""


def _chat_fallback() -> str:
    return """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rundown Coach</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0f0f0f;color:#e0e0e0;
  display:flex;flex-direction:column;height:100vh}
header{background:#1a1a1a;padding:12px 16px;display:flex;justify-content:space-between;align-items:center}
h1{font-size:18px;color:#4caf50}
#msg-list{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px}
.msg{padding:12px 16px;border-radius:12px;max-width:85%;line-height:1.6;font-size:15px}
.msg.user{align-self:flex-end;background:#4caf50;color:#fff}
.msg.assistant{align-self:flex-start;background:#1a1a1a}
.msg .time{font-size:11px;opacity:.5;margin-top:4px}
#input-row{display:flex;padding:12px 16px;gap:8px;background:#1a1a1a}
#input-row input{flex:1;padding:12px;border-radius:10px;border:1px solid #333;
  background:#0f0f0f;color:#fff;font-size:16px;outline:none}
#input-row input:focus{border-color:#4caf50}
#input-row button{padding:12px 20px;border-radius:10px;border:none;background:#4caf50;
  color:#fff;font-weight:600;cursor:pointer;font-size:15px}
#input-row button:disabled{opacity:.5}
.spinner{display:inline-block;width:16px;height:16px;border:2px solid #4caf50;
  border-top-color:transparent;border-radius:50%;animation:spin .6s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<header>
  <h1>🏃 Rundown Coach</h1>
  <span style="font-size:12px;color:#888">AI 跑步教练</span>
</header>
<div id="msg-list"></div>
<div id="input-row">
  <input id="user-input" type="text" placeholder="输入消息..." autocomplete="off">
  <button id="send-btn" onclick="send()">发送</button>
</div>
<script>
const msgList=document.getElementById('msg-list');
const input=document.getElementById('user-input');
const messages=[];
function addMsg(role,text){
  const div=document.createElement('div');
  div.className='msg '+role;
  const now=new Date();
  div.innerHTML=text+'<div class="time">'+now.toLocaleTimeString('zh',{hour:'2-digit',minute:'2-digit'})+'</div>';
  msgList.appendChild(div);
  msgList.scrollTop=msgList.scrollHeight;
  return div;
}
async function send(){
  const text=input.value.trim();
  if(!text)return;
  input.value='';input.disabled=true;
  document.getElementById('send-btn').disabled=true;
  messages.push({role:'user',content:text});
  addMsg('user',text);
  const aiDiv=addMsg('assistant','<span class="spinner"></span>');
  try{
    const r=await fetch('/api/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({messages})});
    const reader=r.body.getReader();
    const decoder=new TextDecoder();
    let full='';
    aiDiv.innerHTML='';
    while(true){
      const{done,value}=await reader.read();
      if(done)break;
      const chunk=decoder.decode(value,{stream:true});
      for(const line of chunk.split('\\n')){
        if(line.startsWith('data: ')){
          const d=line.slice(6);
          if(d==='[DONE]')continue;
          try{const{j}=JSON.parse(d);full+=j.token||'';aiDiv.innerHTML=full.replace(/\\n/g,'<br>')}catch(e){}
        }
      }
      msgList.scrollTop=msgList.scrollHeight;
    }
    messages.push({role:'assistant',content:full});
  }catch(e){
    aiDiv.innerHTML='错误: '+e.message;
  }
  input.disabled=false;
  document.getElementById('send-btn').disabled=false;
  input.focus();
}
input.addEventListener('keydown',e=>{if(e.key==='Enter')send()});
</script>
</body>
</html>"""


# ── 启动入口 ────────────────────────────────────


def create_web_server(config: Config, user_manager: UserManager):
    """创建带了 Web 路由的 FastMCP server，由 cmd_serve 调用。"""
    from .mcp_server import create_server

    # 用占位 provider（Web 模式下每个用户独立认证）
    from .providers.garmin import GarminProvider
    provider = GarminProvider(config)

    storage = Storage(config)
    memory_store = MemoryStore(config.memory_dir, db_getter=lambda: storage.db)

    server = create_server(config, provider, storage, memory_store, 0)
    register_web_routes(server, user_manager, config)

    return server
