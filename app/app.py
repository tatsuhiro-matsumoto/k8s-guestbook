"""シンプルな記録帳（Guestbook）
- 画面に「このリクエストを処理した Pod 名」を表示する
- 投稿ごとに「書き込んだ Pod 名」を PostgreSQL に保存する
"""
import hashlib
import os
import socket
import time

import psycopg
from flask import Flask, redirect, render_template_string, request

app = Flask(__name__)

POD_NAME = os.environ.get("POD_NAME", socket.gethostname())
DSN = (
    f"host={os.environ.get('DB_HOST', 'postgres')} "
    f"port={os.environ.get('DB_PORT', '5432')} "
    f"dbname={os.environ.get('DB_NAME', 'guestbook')} "
    f"user={os.environ.get('DB_USER', 'guestbook')} "
    f"password={os.environ.get('DB_PASSWORD', 'guestbook')}"
)


def connect():
    return psycopg.connect(DSN, connect_timeout=3)


def init_db():
    """DB が起動するまで待ってからテーブルを作る（最大約60秒）"""
    for i in range(30):
        try:
            with connect() as conn:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS entries (
                         id SERIAL PRIMARY KEY,
                         message TEXT NOT NULL,
                         pod TEXT NOT NULL,
                         created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
                )
            print("DB ready", flush=True)
            return
        except Exception as e:  # noqa: BLE001
            print(f"waiting for DB ({i + 1}/30): {e}", flush=True)
            time.sleep(2)
    raise SystemExit("DB に接続できませんでした")


def pod_color(name: str) -> str:
    """Pod 名から色を決める（Pod が変わると色も変わって分かりやすい）"""
    hue = int(hashlib.md5(name.encode()).hexdigest(), 16) % 360
    return f"hsl({hue}, 65%, 45%)"


PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Guestbook</title>
<style>
 body{font-family:sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem;color:#222}
 .pod{padding:1rem;border-radius:10px;color:#fff;background:{{ color }};margin-bottom:1.5rem}
 .pod small{opacity:.85}
 form{display:flex;gap:.5rem;margin-bottom:1.5rem}
 input{flex:1;padding:.6rem;font-size:1rem}
 button{padding:.6rem 1rem;font-size:1rem}
 li{padding:.5rem 0;border-bottom:1px solid #ddd;list-style:none}
 ul{padding:0} .meta{font-size:.8rem;color:#777}
 .tag{display:inline-block;padding:0 .4rem;border-radius:4px;color:#fff;font-size:.75rem}
</style></head><body>
<h1>Guestbook</h1>
<div class="pod"><small>このページを返した Pod</small><br><strong>{{ pod }}</strong></div>
<form method="post">
  <input name="message" maxlength="200" placeholder="メッセージを書く" required>
  <button>書き込む</button>
</form>
<ul>
{% for e in entries %}
  <li>{{ e[0] }}<br>
    <span class="meta">{{ e[2].strftime('%Y-%m-%d %H:%M:%S') }}
    <span class="tag" style="background:{{ colorof(e[1]) }}">{{ e[1] }}</span></span></li>
{% else %}
  <li class="meta">まだ書き込みはありません</li>
{% endfor %}
</ul>
</body></html>"""


@app.get("/")
def index():
    with connect() as conn:
        rows = conn.execute(
            "SELECT message, pod, created_at FROM entries ORDER BY id DESC LIMIT 50"
        ).fetchall()
    return render_template_string(
        PAGE, pod=POD_NAME, color=pod_color(POD_NAME), entries=rows, colorof=pod_color
    )


@app.post("/")
def post():
    msg = request.form.get("message", "").strip()[:200]
    if msg:
        with connect() as conn:
            conn.execute(
                "INSERT INTO entries (message, pod) VALUES (%s, %s)", (msg, POD_NAME)
            )
    return redirect("/", code=303)


@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/readyz")
def readyz():
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
        return "ok"
    except Exception:  # noqa: BLE001
        return "db unavailable", 503


init_db()
