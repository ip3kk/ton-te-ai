#!/usr/bin/env python3
"""
TON TE AI — Admin Panel

Lightweight FastAPI dashboard for monitoring bot users and conversations.
Run: uvicorn admin:app --host 0.0.0.0 --port 8080
"""
import os
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

import db

app = FastAPI(title="TON TE AI Admin")
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("ADMIN_SECRET", "tonpal-admin-2026"))

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "te2026admin")


def _ts_fmt(ts):
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _require_auth(request: Request):
    if not request.session.get("authed"):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


# ── HTML Templates ──

_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TON TE AI Admin</title>
<style>
:root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#c9d1d9;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--dim:#8b949e}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);line-height:1.5}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.container{max-width:1100px;margin:0 auto;padding:20px}
h1{font-size:1.5em;margin-bottom:20px;color:#fff}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:30px}
.stat-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:20px}
.stat-card .label{color:var(--dim);font-size:.85em;text-transform:uppercase;letter-spacing:.05em}
.stat-card .value{font-size:2em;font-weight:700;color:#fff;margin-top:4px}
.stat-card .value.green{color:var(--green)}.stat-card .value.accent{color:var(--accent)}
table{width:100%;border-collapse:collapse;background:var(--card);border-radius:10px;overflow:hidden;border:1px solid var(--border)}
th,td{padding:12px 16px;text-align:left;border-bottom:1px solid var(--border)}
th{background:#1c2128;color:var(--dim);font-size:.8em;text-transform:uppercase;letter-spacing:.05em}
tr:hover{background:#1c2128}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:.75em;font-weight:600}
.badge-en{background:#1f3d5c;color:#58a6ff}.badge-zh{background:#3d2f1f;color:#f0883e}
.badge-yue{background:#3d1f1f;color:#f85149}.badge-ru{background:#1f3d2a;color:#3fb950}
.badge-ja{background:#3d1f3d;color:#bc8cff}.badge-ko{background:#1f333d;color:#79c0ff}
.badge-th{background:#3d3d1f;color:#d2a8ff}
.search-box{margin-bottom:20px}
.search-box input{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:10px 16px;color:var(--text);font-size:1em;width:100%;max-width:400px}
.search-box input:focus{outline:none;border-color:var(--accent)}
.chat-log{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:20px;max-height:70vh;overflow-y:auto}
.chat-msg{margin-bottom:12px;padding:10px 14px;border-radius:8px;max-width:80%;white-space:pre-wrap;word-break:break-word;font-size:.9em}
.chat-in{background:#1c2128;margin-right:auto;border-left:3px solid var(--accent)}
.chat-out{background:#0d2137;margin-left:auto;border-right:3px solid var(--green)}
.chat-ts{color:var(--dim);font-size:.7em;margin-top:4px}
.chat-type{color:var(--dim);font-size:.7em;margin-bottom:2px}
.nav{display:flex;gap:16px;align-items:center;margin-bottom:24px;padding-bottom:16px;border-bottom:1px solid var(--border)}
.nav a{font-weight:600}.nav .title{font-size:1.2em;color:#fff;font-weight:700}
.login-box{max-width:360px;margin:100px auto;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:40px}
.login-box h2{color:#fff;margin-bottom:20px;text-align:center}
.login-box input{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:10px 14px;color:var(--text);font-size:1em;margin-bottom:16px}
.login-box button{width:100%;padding:10px;background:var(--accent);color:#fff;border:none;border-radius:8px;font-size:1em;cursor:pointer;font-weight:600}
.login-box button:hover{opacity:.9}
.err{color:var(--red);text-align:center;margin-bottom:12px;font-size:.9em}
</style>
</head>
<body>"""

_FOOT = "</body></html>"


@app.on_event("startup")
async def _startup():
    await db.get_db()


@app.on_event("shutdown")
async def _shutdown():
    await db.close_db()


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    err_html = f'<p class="err">{error}</p>' if error else ""
    return f"""{_HEAD}
<div class="login-box">
<h2>TON TE AI Admin</h2>
{err_html}
<form method="post" action="/login">
<input type="password" name="password" placeholder="Password" autofocus>
<button type="submit">Login</button>
</form>
</div>{_FOOT}"""


@app.post("/login")
async def login_post(request: Request):
    form = await request.form()
    pw = form.get("password", "")
    if pw == ADMIN_PASSWORD:
        request.session["authed"] = True
        return RedirectResponse("/", status_code=303)
    return RedirectResponse("/login?error=Wrong+password", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    _require_auth(request)
    stats = await db.get_stats()
    users = await db.get_all_users(limit=100)

    user_rows = ""
    for u in users:
        name = u["first_name"] or u["username"] or str(u["tg_id"])
        uname = f"@{u['username']}" if u["username"] else "—"
        lang = u["lang"] or "en"
        badge_cls = f"badge-{lang}" if lang in ("en", "zh", "yue", "ru", "ja", "ko", "th") else "badge-en"
        user_rows += f"""<tr>
<td><a href="/user/{u['tg_id']}">{name}</a></td>
<td>{uname}</td>
<td><span class="badge {badge_cls}">{lang}</span></td>
<td>{u['msg_count']}</td>
<td>{_ts_fmt(u['last_seen'])}</td>
</tr>"""

    return f"""{_HEAD}
<div class="container">
<div class="nav">
<span class="title">TON TE AI Admin</span>
<a href="/">Dashboard</a>
<a href="/logout">Logout</a>
</div>

<div class="stats">
<div class="stat-card"><div class="label">Total Users</div><div class="value accent">{stats['total_users']}</div></div>
<div class="stat-card"><div class="label">Active Today</div><div class="value green">{stats['active_today']}</div></div>
<div class="stat-card"><div class="label">Total Messages</div><div class="value">{stats['total_messages']}</div></div>
<div class="stat-card"><div class="label">Messages Today</div><div class="value green">{stats['messages_today']}</div></div>
</div>

<h1>Users</h1>
<div class="search-box">
<form method="get" action="/search">
<input type="text" name="q" placeholder="Search by username, name, or Telegram ID...">
</form>
</div>

<table>
<thead><tr><th>Name</th><th>Username</th><th>Lang</th><th>Messages</th><th>Last Active</th></tr></thead>
<tbody>{user_rows}</tbody>
</table>
</div>{_FOOT}"""


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, q: str = ""):
    _require_auth(request)
    if not q:
        return RedirectResponse("/", status_code=303)
    users = await db.search_users(q)

    user_rows = ""
    for u in users:
        name = u["first_name"] or u["username"] or str(u["tg_id"])
        uname = f"@{u['username']}" if u["username"] else "—"
        lang = u["lang"] or "en"
        badge_cls = f"badge-{lang}" if lang in ("en", "zh", "yue", "ru", "ja", "ko", "th") else "badge-en"
        user_rows += f"""<tr>
<td><a href="/user/{u['tg_id']}">{name}</a></td>
<td>{uname}</td>
<td><span class="badge {badge_cls}">{lang}</span></td>
<td>{u['msg_count']}</td>
<td>{_ts_fmt(u['last_seen'])}</td>
</tr>"""

    return f"""{_HEAD}
<div class="container">
<div class="nav">
<span class="title">TON TE AI Admin</span>
<a href="/">Dashboard</a>
<a href="/logout">Logout</a>
</div>

<h1>Search: "{q}"</h1>
<div class="search-box">
<form method="get" action="/search">
<input type="text" name="q" value="{q}" placeholder="Search...">
</form>
</div>

<table>
<thead><tr><th>Name</th><th>Username</th><th>Lang</th><th>Messages</th><th>Last Active</th></tr></thead>
<tbody>{user_rows if user_rows else '<tr><td colspan="5" style="text-align:center;color:var(--dim)">No results</td></tr>'}</tbody>
</table>
</div>{_FOOT}"""


@app.get("/user/{tg_id}", response_class=HTMLResponse)
async def user_detail(request: Request, tg_id: int):
    _require_auth(request)
    messages = await db.get_user_messages(tg_id, limit=500)

    all_users = await db.search_users(str(tg_id))
    user = all_users[0] if all_users else {"tg_id": tg_id, "username": None, "first_name": None, "lang": "en"}
    name = user.get("first_name") or user.get("username") or str(tg_id)
    uname = f"@{user['username']}" if user.get("username") else ""

    chat_html = ""
    for m in messages:
        css = "chat-in" if m["direction"] == "in" else "chat-out"
        ts = _ts_fmt(m["ts"])
        mtype = f'<div class="chat-type">{m["msg_type"]}</div>' if m["msg_type"] != "text" else ""
        content = (m["content"] or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        arrow = "→" if m["direction"] == "in" else "←"
        chat_html += f'<div class="chat-msg {css}">{mtype}{content}<div class="chat-ts">{arrow} {ts}</div></div>'

    if not chat_html:
        chat_html = '<p style="color:var(--dim);text-align:center;padding:40px">No messages yet</p>'

    return f"""{_HEAD}
<div class="container">
<div class="nav">
<span class="title">TON TE AI Admin</span>
<a href="/">Dashboard</a>
<a href="/logout">Logout</a>
</div>

<h1>{name} {uname}</h1>
<p style="color:var(--dim);margin-bottom:16px">Telegram ID: {tg_id} &nbsp;|&nbsp; Lang: {user.get('lang','en')} &nbsp;|&nbsp; Messages: {len(messages)}</p>

<div class="chat-log">
{chat_html}
</div>
</div>{_FOOT}"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
