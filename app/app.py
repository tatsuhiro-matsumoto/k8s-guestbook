"""Nutanix Guestbook（シンプルな記録帳）
- 画面に「このリクエストを処理した Pod」を「X番Pod（起動順）」と正式名で表示する
- Pod は起動時に DB へ自分を登録し、起動順の番号をもらう
- 投稿ごとに「書き込んだ Pod」を PostgreSQL に保存する
- Kubernetes API から同じ namespace の Pod 一覧を取り、状態をカードで表示する（2秒ごとに更新）
"""
import hashlib
import json
import os
import socket
import ssl
import time
import urllib.request
from datetime import datetime, timezone

import psycopg
from flask import Flask, jsonify, redirect, render_template_string, request

app = Flask(__name__)

POD_NAME = os.environ.get("POD_NAME", socket.gethostname())
POD_NO = None  # 起動時に DB から割り当てる
NAMESPACE = os.environ.get("POD_NAMESPACE", "default")
SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
DSN = (
    f"host={os.environ.get('DB_HOST', 'postgres')} "
    f"port={os.environ.get('DB_PORT', '5432')} "
    f"dbname={os.environ.get('DB_NAME', 'guestbook')} "
    f"user={os.environ.get('DB_USER', 'guestbook')} "
    f"password={os.environ.get('DB_PASSWORD', 'guestbook')}"
)

# ロゴ画像：app/static/ に nutanix-logo.svg（または .png）を置くとタイトル横に表示
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
LOGO = next(
    (f for f in ("nutanix-logo.svg", "nutanix-logo.png")
     if os.path.exists(os.path.join(STATIC_DIR, f))),
    None,
)


def connect():
    return psycopg.connect(DSN, connect_timeout=3)


def init_db():
    """DB が起動するまで待ってからテーブルを作り、自分の起動順番号をもらう（最大約60秒）"""
    global POD_NO
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
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS pods (
                         no SERIAL PRIMARY KEY,
                         pod TEXT UNIQUE NOT NULL,
                         started_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
                )
                # 同じ Pod 名（コンテナ再起動）なら同じ番号のまま
                POD_NO = conn.execute(
                    """INSERT INTO pods (pod) VALUES (%s)
                       ON CONFLICT (pod) DO UPDATE SET pod = EXCLUDED.pod
                       RETURNING no""",
                    (POD_NAME,),
                ).fetchone()[0]
            print(f"DB ready: {POD_NAME} is Pod #{POD_NO}", flush=True)
            return
        except Exception as e:  # noqa: BLE001
            print(f"waiting for DB ({i + 1}/30): {e}", flush=True)
            time.sleep(2)
    raise SystemExit("DB に接続できませんでした")


def pod_color(name: str) -> str:
    """Pod 名から色を決める（Pod が変わると色も変わって分かりやすい）"""
    hue = int(hashlib.md5(name.encode()).hexdigest(), 16) % 360
    return f"hsl({hue}, 65%, 45%)"


def k8s_get(path):
    """Kubernetes API を呼ぶ（ServiceAccount の権限を使用）"""
    with open(f"{SA_DIR}/token") as f:
        token = f.read()
    ctx = ssl.create_default_context(cafile=f"{SA_DIR}/ca.crt")
    req = urllib.request.Request(
        f"https://kubernetes.default.svc{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, context=ctx, timeout=3) as r:
        return json.load(r)["items"]


def k8s_pods():
    """同じ namespace の Pod 一覧"""
    return k8s_get(f"/api/v1/namespaces/{NAMESPACE}/pods")


def k8s_nodes():
    """ノードごとの Ready 状態（取れなければ空）"""
    try:
        items = k8s_get("/api/v1/nodes")
    except Exception:  # noqa: BLE001
        return {}
    res = {}
    for n in items:
        conds = {c["type"]: c["status"] for c in n.get("status", {}).get("conditions", [])}
        res[n["metadata"]["name"]] = conds.get("Ready") == "True"
    return res


def pod_ready(p):
    """Pod の Ready 条件（ノード障害時は K8s がここを False にする）"""
    for c in p.get("status", {}).get("conditions") or []:
        if c.get("type") == "Ready":
            return c.get("status") == "True"
    return False


def pod_state(p, node_ready=None):
    """kubectl get pods の STATUS に近い表示と、色分け用の区分を返す"""
    if p["metadata"].get("deletionTimestamp"):
        return "Terminating", "gone"
    if node_ready is False:
        return "Node NotReady", "bad"
    statuses = p.get("status", {}).get("containerStatuses") or []
    for c in statuses:
        waiting = c.get("state", {}).get("waiting")
        if waiting:
            reason = waiting.get("reason", "Waiting")
            bad = reason in ("CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "Error")
            return reason, "bad" if bad else "wait"
    phase = p.get("status", {}).get("phase", "Unknown")
    if phase == "Running":
        return ("Running", "ok") if pod_ready(p) else ("Running (not ready)", "wait")
    if phase == "Pending":
        return "Pending", "wait"
    return phase, "bad" if phase == "Failed" else "wait"


def age(ts):
    if not ts:
        return ""
    sec = int((datetime.now(timezone.utc) - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds())
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    return f"{sec // 3600}h{sec % 3600 // 60}m"


PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nutanix Guestbook</title>
<style>
 body{font-family:sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem;color:#222}
 h1{display:flex;align-items:center;gap:.6rem}
 h1 img{height:1.6em}
 .pod{padding:1rem;border-radius:10px;color:#fff;background:{{ color }};margin-bottom:1.5rem}
 .pod small{opacity:.85}
 .pod .no{font-size:2rem;font-weight:bold;line-height:1.3}
 .pod .no span{font-size:1rem;font-weight:normal;opacity:.85}
 .pod .name{font-family:monospace;opacity:.95}
 form{display:flex;gap:.5rem;margin-bottom:1.5rem}
 input{flex:1;padding:.6rem;font-size:1rem}
 button,.btn{padding:.6rem 1rem;font-size:1rem;cursor:pointer}
 .tools{display:flex;gap:.5rem;justify-content:flex-end;margin-bottom:1rem}
 .tools form{margin:0}
 .btn{border:1px solid #888;border-radius:4px;background:#f4f4f4;color:#222;text-decoration:none}
 .danger{border:1px solid #c33;border-radius:4px;background:#fff;color:#c33}
 li{padding:.5rem 0;border-bottom:1px solid #ddd;list-style:none}
 ul{padding:0} .meta{font-size:.8rem;color:#777}
 .tag{display:inline-block;padding:0 .4rem;border-radius:4px;color:#fff;font-size:.75rem}
 h2{font-size:1.1rem;margin:2rem 0 .3rem}
 .summary{font-size:.9rem;color:#555;margin-bottom:.6rem}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:.6rem}
 .card{border:2px solid #ccc;border-radius:8px;padding:.6rem;font-size:.8rem;background:#fafafa}
 .card .t{font-weight:bold;font-size:1rem}
 .card .n{font-family:monospace;word-break:break-all;color:#555}
 .card .s{display:inline-block;margin:.3rem 0;padding:0 .4rem;border-radius:4px;color:#fff}
 .card.me{box-shadow:0 0 0 3px #222}
 .ok .s{background:#2e8b57} .ok{border-color:#2e8b57}
 .wait .s{background:#d08a00} .wait{border-color:#d08a00}
 .bad .s{background:#c33} .bad{border-color:#c33}
 .gone{opacity:.5} .gone .s{background:#888}
</style></head><body>
<h1>{% if logo %}<img src="/static/{{ logo }}" alt="">{% endif %}Nutanix Guestbook</h1>
<div class="pod">
  <small>このページを返した Pod</small>
  <div class="no">{{ pod_no }}番Pod <span>（起動順）</span></div>
  <div class="name">{{ pod }}</div>
</div>
<form method="post">
  <input name="message" maxlength="200" placeholder="メッセージを書く" required>
  <button>書き込む</button>
</form>
<div class="tools">
  <a class="btn" href="/">最新化</a>
  <form method="post" action="/clear" onsubmit="return confirm('書き込みを全部削除します。よろしいですか？')">
    <button class="danger">データ削除</button>
  </form>
</div>
<ul>
{% for e in entries %}
  <li>{{ e[0] }}<br>
    <span class="meta">{{ e[2].strftime('%Y-%m-%d %H:%M:%S') }}
    <span class="tag" style="background:{{ colorof(e[1]) }}">{% if e[3] %}{{ e[3] }}番Pod / {% endif %}{{ e[1] }}</span></span></li>
{% else %}
  <li class="meta">まだ書き込みはありません</li>
{% endfor %}
</ul>
<h2>Pod の状態 <small style="font-weight:normal;color:#777">（namespace: {{ ns }}・2秒ごとに更新）</small></h2>
<div class="summary" id="summary">読み込み中…</div>
<div class="grid" id="pods"></div>
<script>
const ME = {{ pod|tojson }};
function esc(t){return String(t).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
async function load(){
  try{
    const r = await fetch("/api/pods",{cache:"no-store"});
    const d = await r.json();
    if(d.error){document.getElementById("summary").textContent=d.error;return;}
    const c = {};
    d.pods.forEach(p=>c[p.group]=(c[p.group]||0)+1);
    document.getElementById("summary").textContent =
      "Pod 合計 " + d.pods.length + " 個（" + Object.entries(c).map(([k,v])=>k+": "+v).join("、") + "）";
    document.getElementById("pods").innerHTML = d.pods.map(p =>
      `<div class="card ${p.cls}${p.name===ME?' me':''}">
        <div class="t">${p.no?p.no+"番Pod":esc(p.group)}${p.name===ME?" ← このページ":""}</div>
        <div class="n">${esc(p.name)}</div>
        <span class="s">${esc(p.status)}</span>
        <div>Ready: ${esc(p.ready)}・再起動 ${p.restarts}・${esc(p.age)}</div>
        <div>Node: ${esc(p.node||"-")}${p.node_ready===false?" (NotReady)":""}</div>
      </div>`).join("");
  }catch(e){document.getElementById("summary").textContent="Pod 情報を取得できません";}
}
load(); setInterval(load,2000);
</script>
</body></html>"""


@app.get("/")
def index():
    with connect() as conn:
        rows = conn.execute(
            """SELECT e.message, e.pod, e.created_at, p.no
               FROM entries e LEFT JOIN pods p ON p.pod = e.pod
               ORDER BY e.id DESC LIMIT 50"""
        ).fetchall()
    return render_template_string(
        PAGE, pod=POD_NAME, pod_no=POD_NO, color=pod_color(POD_NAME),
        entries=rows, colorof=pod_color, logo=LOGO, ns=NAMESPACE,
    )


@app.get("/api/pods")
def api_pods():
    try:
        items = k8s_pods()
    except Exception as e:  # noqa: BLE001
        return jsonify(error=f"Pod 情報を取得できません（ServiceAccount の権限を確認）: {e}")
    try:
        with connect() as conn:
            nos = dict(conn.execute("SELECT pod, no FROM pods").fetchall())
    except Exception:  # noqa: BLE001
        nos = {}
    nodes = k8s_nodes()
    pods = []
    for p in items:
        md = p["metadata"]
        st = p.get("status", {})
        cs = st.get("containerStatuses") or []
        node = p["spec"].get("nodeName")
        status, cls = pod_state(p, nodes.get(node))
        pods.append({
            "name": md["name"],
            "group": md.get("labels", {}).get("app", "other"),
            "no": nos.get(md["name"]),
            "status": status,
            "cls": cls,
            "ready": "はい" if pod_ready(p) else "いいえ",
            "restarts": sum(c.get("restartCount", 0) for c in cs),
            "age": age(md.get("creationTimestamp")),
            "node": node,
            "node_ready": nodes.get(node),
            "_order": (md.get("labels", {}).get("app", ""), nos.get(md["name"]) or 10**9, md["name"]),
        })
    pods.sort(key=lambda x: x.pop("_order"))
    return jsonify(pods=pods)


@app.post("/")
def post():
    msg = request.form.get("message", "").strip()[:200]
    if msg:
        with connect() as conn:
            conn.execute(
                "INSERT INTO entries (message, pod) VALUES (%s, %s)", (msg, POD_NAME)
            )
    return redirect("/", code=303)


@app.post("/clear")
def clear():
    with connect() as conn:
        conn.execute("TRUNCATE entries RESTART IDENTITY")
    return redirect("/", code=303)


@app.after_request
def no_keepalive(resp):
    # 毎回接続を切る → ブラウザのリロードでも別の Pod に振り分けられる
    resp.headers["Connection"] = "close"
    resp.headers["Cache-Control"] = "no-store"
    return resp


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
