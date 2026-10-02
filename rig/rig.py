#!/usr/bin/env python3
"""
ABUZ8 RIG - one board, many agents, zero cloud tokens for the always-on ones.

  python rig.py up                 start every enabled agent + dashboard at http://127.0.0.1:8787
  python rig.py once <agent>       run one agent one time (test)
  python rig.py task "text" [--to seat]   add a task to the board
  python rig.py status             print the board

Agents (rig.json):
  kind "signal"   - scans free public sources for paying work, deals, hackathons, grants; local brain scores each one
  kind "youtube"  - watches YouTube channels, pulls transcripts (yt-dlp), local brain extracts the executable plays
  kind "cli"      - a seat for a coding agent (Claude Code, Codex, Qwen Code...). Takes tasks addressed to it, runs headless, logs output
  kind "openrig"  - bridge: sends a board task to an OpenRig seat (inside WSL on Windows) and closes it when the seat writes its result file
  kind "digest"   - once a day sends the top signals + task results to your own Telegram bot

  python rig.py telegram-setup     find your chat id after you message your bot
  python rig.py digest-now         send the digest right now
Brain: local OpenAI-compatible endpoints only (vLLM/Qwen :8011, Ollama :11434). If none is up, agents fall back to keyword scoring.
Rule: agents report. Nothing is sent, posted, bought or emailed without you. Standard library only - no pip install.
"""
import html, json, os, re, shlex, shutil, sqlite3, subprocess, sys, threading, time, traceback, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "rig.json"), encoding="utf-8"))
DB = os.path.join(HERE, CFG.get("db", "rig.sqlite"))
LOGDIR = os.path.join(HERE, "logs"); os.makedirs(LOGDIR, exist_ok=True)
UA = {"User-Agent": "abuz8-rig/1.0 (+https://abuz8ai.com)"}
LOCK = threading.Lock()
STATE = {}  # agent -> {status, last_run, last_msg}

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def log(agent, msg):
    line = f"{now()} [{agent}] {msg}"
    print(line, flush=True)
    with open(os.path.join(LOGDIR, f"{agent}.log"), "a", encoding="utf-8") as f: f.write(line + "\n")
    STATE.setdefault(agent, {})["last_msg"] = msg[:200]

# ------------------------------------------------------------------ board
def db():
    c = sqlite3.connect(DB, timeout=30); c.row_factory = sqlite3.Row; return c
def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS signals(id INTEGER PRIMARY KEY, agent TEXT, source TEXT, title TEXT, url TEXT UNIQUE, summary TEXT,
            score INTEGER, action TEXT, money TEXT, deadline TEXT, status TEXT DEFAULT 'new', created TEXT);
        CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, text TEXT, seat TEXT, status TEXT DEFAULT 'todo', created TEXT,
            started TEXT, finished TEXT, output TEXT, signal_id INTEGER);
        CREATE TABLE IF NOT EXISTS seen(key TEXT PRIMARY KEY, at TEXT);""")
def seen(key):
    with db() as c:
        if c.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone(): return True
        c.execute("INSERT INTO seen VALUES(?,?)", (key, now())); return False
def add_signal(agent, source, title, url, summary, score, action, money="", deadline=""):
    with db() as c:
        try:
            c.execute("INSERT INTO signals(agent,source,title,url,summary,score,action,money,deadline,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (agent, source, title[:300], url, summary[:2000], int(score), action[:500], money[:120], deadline[:40], now()))
            return True
        except sqlite3.IntegrityError: return False
def add_task(text, seat="", signal_id=None):
    with db() as c:
        cur = c.execute("INSERT INTO tasks(text,seat,created,signal_id) VALUES(?,?,?,?)", (text, seat, now(), signal_id)); return cur.lastrowid

# ------------------------------------------------------------------ http + brain
def get(url, timeout=25, raw=False):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read(); return b if raw else json.loads(b.decode("utf-8", "replace"))

BRAIN = {"base": None, "model": None, "checked": 0}
def brain():
    if BRAIN["base"] and time.time() - BRAIN["checked"] < 300: return BRAIN
    BRAIN.update(base=None, model=None, checked=time.time())
    for base in CFG["brain"]["endpoints"]:
        try:
            ms = get(base.rstrip("/") + "/models", timeout=4)
            ids = [m["id"] for m in ms.get("data", [])]
            want = CFG["brain"].get("model")
            BRAIN.update(base=base.rstrip("/"), model=(want if want in ids else (ids[0] if ids else want))); return BRAIN
        except Exception: continue
    return BRAIN
def ask(system, user, max_tokens=500):
    b = brain()
    if not b["base"]: return None
    body = json.dumps({"model": b["model"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                       "temperature": 0.2, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(b["base"] + "/chat/completions", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=CFG["brain"].get("timeout", 180)) as r:
            txt = json.loads(r.read())["choices"][0]["message"]["content"]
        return re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()
    except Exception as e:
        BRAIN["base"] = None; return None
def parse_json(txt):
    if not txt: return None
    m = re.search(r"\{.*\}", txt, re.S)
    try: return json.loads(m.group(0)) if m else None
    except Exception: return None

# ------------------------------------------------------------------ SIGNAL agent
def kw_score(text, a):
    t = text.lower(); s = 0
    s += 12 * sum(1 for k in a["want"] if k.lower() in t)
    s += 15 * sum(1 for k in a["money_words"] if k.lower() in t)
    s -= 25 * sum(1 for k in a["avoid"] if k.lower() in t)
    return max(0, min(100, s))

def src_hn(q, a):
    since = int(time.time()) - a.get("max_age_days", 10) * 86400
    u = ("https://hn.algolia.com/api/v1/search_by_date?tags=(story,comment)&hitsPerPage=40&numericFilters=" + urllib.parse.quote("created_at_i>%d" % since) + "&query=") + urllib.parse.quote(q)
    for h in get(u).get("hits", []):
        title = h.get("title") or h.get("story_title") or ""; body = re.sub(r"<[^>]+>", " ", html.unescape(h.get("comment_text") or h.get("story_text") or ""))
        yield "Hacker News", title or body[:90], h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}", body[:1500]
def src_reddit(sub, a):
    try: kids = get(f"https://www.reddit.com/r/{sub}/new.json?limit=40").get("data", {}).get("children", [])
    except Exception:
        for x in src_rss(f"https://www.reddit.com/r/{sub}/new/.rss", a): yield (f"r/{sub}",) + x[1:]
        return
    for ch in kids:
        d = ch["data"]
        yield f"r/{sub}", d.get("title", ""), "https://www.reddit.com" + d.get("permalink", ""), (d.get("selftext") or "")[:1500]
def src_devpost(_, a):
    j = get("https://devpost.com/api/hackathons?status[]=upcoming&status[]=open&order_by=deadline")
    for h in j.get("hackathons", []):
        prize = re.sub(r"<[^>]+>", "", h.get("prize_amount") or "")
        themes = ", ".join(t.get("name", "") for t in h.get("themes", []))
        yield "Devpost", h.get("title", ""), h.get("url", ""), f"Prize {prize}. Deadline {h.get('submission_period_dates','')}. Themes: {themes}. {h.get('open_state','')}"
def src_rss(url, a):
    root = ET.fromstring(get(url, raw=True))
    for it in root.iter():
        if it.tag.split('}')[-1] not in ("item", "entry"): continue
        g = {c.tag.split('}')[-1]: c for c in it}
        title = (g.get("title").text if g.get("title") is not None else "") or ""
        link = g.get("link"); link = (link.get("href") or link.text) if link is not None else ""
        desc = g.get("description") if g.get("description") is not None else g.get("summary")
        yield urllib.parse.urlparse(url).netloc, title, link or "", re.sub(r"<[^>]+>", " ", html.unescape((desc.text if desc is not None else "") or ""))[:1500]
SOURCES = {"hn": src_hn, "reddit": src_reddit, "devpost": src_devpost, "rss": src_rss}

SIGNAL_SYS = ("You screen opportunities for ABUZ8 LLC, a one-person AI agent studio. Offers: {offers}. "
              "Score 0-100 how likely this turns into money for ABUZ8 within 30 days. Be harsh: generic news, job ads for full-time employees, "
              "and anything needing a team of 10 score under 30. Reply ONLY with JSON: "
              '{{"score":int,"money":"est. $ or prize","deadline":"date or empty","action":"one concrete next step, under 25 words"}}')
def run_signal(name, a):
    n_new = n_kept = 0
    for src in a["sources"]:
        kind, arg = src["type"], src.get("q") or src.get("sub") or src.get("url") or ""
        try: items = list(SOURCES[kind](arg, a))
        except Exception as e: log(name, f"source {kind}:{arg} failed: {e}"); continue
        for source, title, url, body in items:
            if not url or seen(f"{name}|{url}"): continue
            n_new += 1
            pre = kw_score(title + " " + body, a)
            if kind != "devpost" and pre < a.get("prefilter", 24): continue
            out = parse_json(ask(SIGNAL_SYS.format(offers=a["offers"]), f"SOURCE: {source}\nTITLE: {title}\nURL: {url}\nTEXT: {body[:2500]}", 200))
            score = int(out.get("score", pre)) if out else pre
            if score >= (a.get("keep_at", 55) if out else a.get("keep_at_keyword", 36)):
                if add_signal(name, source, title, url, body[:600], score, (out or {}).get("action", "Open it and decide: pitch, apply, or skip."),
                              (out or {}).get("money", ""), (out or {}).get("deadline", "")): n_kept += 1
    log(name, f"scanned {n_new} new items, kept {n_kept} (brain: {brain()['model'] or 'none - keyword mode'})")

# ------------------------------------------------------------------ YOUTUBE agent
YT_SYS = ("You turn a YouTube video into money-making executions for ABUZ8 LLC (offers: {offers}). Ignore hype. "
          "Reply ONLY with JSON: {{\"score\":0-100 value to ABUZ8,\"plays\":[\"up to 5 concrete steps someone can execute this week, each under 25 words\"],"
          "\"tools\":[\"named tools\"],\"money\":\"how it makes money, one line\"}}")
def transcript(video_id):
    exe = CFG.get("yt_dlp", "yt-dlp")
    tmp = os.path.join(HERE, "state", "yt"); os.makedirs(tmp, exist_ok=True)
    try:
        extra = CFG.get("yt_dlp_args", [])
        subprocess.run([exe] + extra + ["--skip-download", "--write-auto-subs", "--write-subs", "--sub-langs", "en,en-US,en-orig", "--sub-format", "vtt",
                        "-o", os.path.join(tmp, "%(id)s.%(ext)s"), f"https://www.youtube.com/watch?v={video_id}"],
                       capture_output=True, timeout=120)
    except Exception: return ""
    for f in os.listdir(tmp):
        if f.startswith(video_id) and f.endswith(".vtt"):
            raw = open(os.path.join(tmp, f), encoding="utf-8", errors="replace").read(); os.remove(os.path.join(tmp, f))
            lines, last = [], ""
            for ln in raw.splitlines():
                ln = re.sub(r"<[^>]+>", "", ln).strip()
                if not ln or "-->" in ln or ln.startswith(("WEBVTT", "Kind:", "Language:")) or ln == last: continue
                lines.append(ln); last = ln
            return " ".join(lines)
    return ""
def resolve_channel(h):
    """accepts UC... id, @handle, or channel URL; caches handle -> id"""
    if re.fullmatch(r"UC[\w-]{22}", h): return h
    cache_p = os.path.join(HERE, "state", "channels.json"); os.makedirs(os.path.dirname(cache_p), exist_ok=True)
    cache = json.load(open(cache_p)) if os.path.exists(cache_p) else {}
    if h in cache: return cache[h]
    url = h if h.startswith("http") else "https://www.youtube.com/" + (h if h.startswith("@") else "@" + h)
    try:
        page = get(url, raw=True).decode("utf-8", "replace")
        m = re.search(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', page) or re.search(r'channel_id=(UC[\w-]{22})', page)
        if m: cache[h] = m.group(1); json.dump(cache, open(cache_p, "w")); return m.group(1)
    except Exception: pass
    return None
def run_youtube(name, a):
    kept = 0
    for ch in a["channels"]:
        cid = resolve_channel(ch["id"] if isinstance(ch, dict) else ch)
        if not cid: log(name, f"could not resolve channel {ch}"); continue
        try: root = ET.fromstring(get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}", raw=True))
        except Exception as e: log(name, f"channel {cid} failed: {e}"); continue
        ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015", "m": "http://search.yahoo.com/mrss/"}
        author = root.findtext("a:title", "", ns)
        for e in root.findall("a:entry", ns)[: a.get("per_channel", 3)]:
            vid = e.findtext("yt:videoId", "", ns); title = e.findtext("a:title", "", ns)
            desc = e.findtext("m:group/m:description", "", ns) or ""
            if not vid or seen(f"{name}|{vid}"): continue
            text = transcript(vid) or desc
            out = parse_json(ask(YT_SYS.format(offers=a["offers"]), f"CHANNEL: {author}\nTITLE: {title}\nTRANSCRIPT: {text[:12000]}", 700))
            if out:
                plays = "\n".join("- " + p for p in out.get("plays", [])[:5]); score = int(out.get("score", 50))
                summ = plays + ("\nTools: " + ", ".join(out.get("tools", [])) if out.get("tools") else ""); act = (out.get("plays") or ["Watch and extract plays"])[0]
            else:
                score, summ, act = kw_score(title + " " + desc, CFG["agents"]["signal"]) or 40, desc[:600], "Brain offline: watch and extract plays yourself"
            if add_signal(name, f"YouTube: {author}", title, f"https://www.youtube.com/watch?v={vid}", summ, score, act, (out or {}).get("money", "")): kept += 1
    log(name, f"added {kept} videos (transcripts via {CFG.get('yt_dlp','yt-dlp')})")

# ------------------------------------------------------------------ CLI seats (Claude Code, Codex, Qwen Code...)
def run_cli(name, a):
    with LOCK, db() as c:
        t = c.execute("SELECT * FROM tasks WHERE status='todo' AND (seat=? OR (seat='' AND ?)) ORDER BY id LIMIT 1", (name, 1 if a.get("take_unassigned") else 0)).fetchone()
        if not t: return
        c.execute("UPDATE tasks SET status='doing', seat=?, started=? WHERE id=?", (name, now(), t["id"]))
    prompt = a.get("preamble", "") + t["text"]
    cmd = [x.replace("{prompt}", prompt) for x in a["command"]]
    exe = shutil.which(cmd[0])  # finds claude.cmd / codex.cmd on Windows
    if not exe:
        with db() as c: c.execute("UPDATE tasks SET status='failed', finished=?, output=? WHERE id=?", (now(), f"'{cmd[0]}' is not on PATH. Install it or fix the command in rig.json.", t["id"]))
        log(name, f"task #{t['id']} FAILED: {cmd[0]} not on PATH"); return
    cmd[0] = exe
    log(name, f"task #{t['id']} started: {t['text'][:80]}")
    try:
        p = subprocess.run(cmd, cwd=a.get("cwd") or HERE, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=a.get("timeout", 3600))
        out, ok = (p.stdout or "") + ("\n[stderr]\n" + p.stderr if p.stderr else ""), p.returncode == 0
    except Exception as e: out, ok = f"{type(e).__name__}: {e}", False
    with db() as c: c.execute("UPDATE tasks SET status=?, finished=?, output=? WHERE id=?", ("done" if ok else "failed", now(), out[-20000:], t["id"]))
    log(name, f"task #{t['id']} {'done' if ok else 'FAILED'}")


# ------------------------------------------------------------------ OPENRIG bridge (board task -> OpenRig seat, result back via shared folder)
def inbox_dir(a): d = a.get("inbox_win") if os.name == "nt" else a.get("inbox_posix") or a.get("inbox_win"); os.makedirs(d, exist_ok=True); return d
def run_openrig(name, a):
    ib = inbox_dir(a)
    # 1) collect finished results the OpenRig seat wrote into the inbox
    with db() as c:
        for t in c.execute("SELECT id FROM tasks WHERE seat=? AND status='sent'", (name,)).fetchall():
            f = os.path.join(ib, f"task-{t['id']}.md")
            if os.path.exists(f):
                out = open(f, encoding="utf-8", errors="replace").read()
                st = "failed" if re.search(r"^\s*STATUS:\s*(BLOCKED|FAILED)", out, re.I | re.M) else "done"
                c.execute("UPDATE tasks SET status=?, finished=?, output=? WHERE id=?", (st, now(), out[-20000:], t["id"]))
                os.replace(f, f + ".read"); log(name, f"task #{t['id']} {st} (result file from OpenRig)")
    # 2) send the next task
    with LOCK, db() as c:
        t = c.execute("SELECT * FROM tasks WHERE status='todo' AND seat=? ORDER BY id LIMIT 1", (name,)).fetchone()
        if not t: return
        c.execute("UPDATE tasks SET status='doing', started=? WHERE id=?", (now(), t["id"]))
    result_path = a["inbox_posix"].rstrip("/") + f"/task-{t['id']}.md"
    msg = (f"{a.get('preamble','')}{t['text']}\n\nWhen finished, write your result to {result_path} : first line STATUS: DONE or STATUS: BLOCKED, "
           f"then what you did and the proof (command output, URL returning 200, test result). This file is how the ABUZ8 board knows you finished.")
    line = "rig send " + shlex.quote(a["seat"]) + " " + shlex.quote(msg)
    cmd = (["wsl", "-e", "bash", "-lc", line] if os.name == "nt" else ["bash", "-lc", line])
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        ok, out = p.returncode == 0, (p.stdout or "") + (p.stderr or "")
    except Exception as e: ok, out = False, f"{type(e).__name__}: {e}"
    with db() as c:
        c.execute("UPDATE tasks SET status=?, output=? WHERE id=?", ("sent" if ok else "failed",
                  (f"Sent to {a['seat']}. Waiting for {result_path}\n" if ok else "rig send failed. Check `rig ps --nodes` in WSL.\n") + out[-4000:], t["id"]))
    log(name, f"task #{t['id']} {'sent to ' + a['seat'] if ok else 'SEND FAILED'}")

# ------------------------------------------------------------------ TELEGRAM digest (your own bot -> you)
def tg(method, **params):
    t = CFG.get("telegram", {}); token = os.environ.get("TELEGRAM_BOT_TOKEN") or t.get("token")
    if not token: raise RuntimeError("No Telegram token. Put it in rig.json -> telegram.token or set TELEGRAM_BOT_TOKEN.")
    url = f"{t.get('api','https://api.telegram.org')}/bot{token}/{method}"
    req = urllib.request.Request(url, data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read())
def build_digest(a):
    with db() as c:
        last = (c.execute("SELECT at FROM seen WHERE key='digest|last'").fetchone() or {"at": "1970"})["at"]
        sig = c.execute("SELECT * FROM signals WHERE status IN ('new','kept') AND created>? AND score>=? ORDER BY score DESC LIMIT ?",
                        (last, a.get("min_score", 60), a.get("top", 5))).fetchall()
        done = c.execute("SELECT COUNT(*) n FROM tasks WHERE status='done' AND finished>?", (last,)).fetchone()["n"]
        failed = c.execute("SELECT COUNT(*) n FROM tasks WHERE status='failed' AND finished>?", (last,)).fetchone()["n"]
        waiting = c.execute("SELECT COUNT(*) n FROM tasks WHERE status IN ('todo','doing','sent')").fetchone()["n"]
    lines = [f"ABUZ8 RIG daily digest {datetime.now().strftime('%a %b %d')}", ""]
    if sig:
        lines.append(f"Top {len(sig)} signals:")
        for i, r in enumerate(sig, 1):
            lines += [f"{i}. [{r['score']}] {r['title'][:110]}", f"   {r['source']}{' | ' + r['money'] if r['money'] else ''}{' | due ' + r['deadline'] if r['deadline'] else ''}",
                      f"   Next: {r['action'][:160]}", f"   {r['url']}"]
    else: lines.append("No new signals above the bar since the last digest.")
    lines += ["", f"Tasks since last digest: {done} done, {failed} failed. {waiting} still open.", f"Board: http://127.0.0.1:{CFG.get('port', 8787)}"]
    return "\n".join(lines)[:4000]
def send_digest(a):
    chat = str(CFG.get("telegram", {}).get("chat_id") or os.environ.get("TELEGRAM_CHAT_ID") or "")
    if not chat: raise RuntimeError("No chat_id. Run: python rig.py telegram-setup")
    r = tg("sendMessage", chat_id=chat, text=build_digest(a), disable_web_page_preview=True)
    if not r.get("ok"): raise RuntimeError(str(r))
    with db() as c: c.execute("INSERT OR REPLACE INTO seen VALUES('digest|last',?)", (now(),))
def run_digest(name, a, force=False):
    today = datetime.now().strftime("%Y-%m-%d")
    with db() as c: sent_today = c.execute("SELECT 1 FROM seen WHERE key=?", (f"digest|{today}",)).fetchone()
    if not force and (sent_today or datetime.now().hour < a.get("hour", 8)): return
    send_digest(a)
    with db() as c: c.execute("INSERT OR REPLACE INTO seen VALUES(?,?)", (f"digest|{today}", now()))
    log(name, "digest sent to Telegram")
def telegram_setup():
    r = tg("getUpdates")
    chats = {}
    for u in r.get("result", []):
        m = u.get("message") or u.get("channel_post") or {}
        if m.get("chat"): chats[m["chat"]["id"]] = m["chat"].get("username") or m["chat"].get("title") or m["chat"].get("first_name")
    if not chats: print("No messages yet. Open your bot in Telegram, send it 'hi', then run this again."); return
    cid = list(chats)[-1]
    cfg_p = os.path.join(HERE, "rig.json"); cfg = json.load(open(cfg_p, encoding="utf-8"))
    cfg.setdefault("telegram", {})["chat_id"] = str(cid); json.dump(cfg, open(cfg_p, "w", encoding="utf-8"), indent=1)
    print(f"Saved chat_id {cid} ({chats[cid]}) to rig.json. Test it: python rig.py digest-now")

RUNNERS = {"signal": run_signal, "youtube": run_youtube, "cli": run_cli, "openrig": run_openrig, "digest": run_digest}
def loop(name, a):
    STATE[name] = {"status": "idle", "kind": a["kind"], "every": a["every_min"]}
    while True:
        STATE[name]["status"] = "working"
        try: RUNNERS[a["kind"]](name, a)
        except Exception: log(name, "crashed: " + traceback.format_exc().splitlines()[-1])
        STATE[name].update(status="idle", last_run=now())
        time.sleep(a["every_min"] * 60)

# ------------------------------------------------------------------ dashboard
PAGE = open(os.path.join(HERE, "dashboard.html"), encoding="utf-8").read()
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path in ("/", "/index.html"): return self.send(200, PAGE, "text/html; charset=utf-8")
        if self.path.startswith("/api/state"):
            with db() as c:
                sig = [dict(r) for r in c.execute("SELECT * FROM signals WHERE status!='dismissed' ORDER BY status='new' DESC, score DESC, id DESC LIMIT 300")]
                tsk = [dict(r) for r in c.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT 200")]
            seats = [{"name": n, **STATE.get(n, {"status": "off"}), "kind": a["kind"], "enabled": a.get("enabled", True)} for n, a in CFG["agents"].items()]
            return self.send(200, json.dumps({"signals": sig, "tasks": tsk, "seats": seats, "brain": brain()["model"]}))
        self.send(404, "{}")
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); d = json.loads(self.rfile.read(n) or b"{}")
        with db() as c:
            if self.path == "/api/task": add_task(d["text"], d.get("seat", ""), d.get("signal_id"))
            elif self.path == "/api/signal": c.execute("UPDATE signals SET status=? WHERE id=?", (d["status"], d["id"]))
            elif self.path == "/api/retry": c.execute("UPDATE tasks SET status='todo', output=NULL WHERE id=?", (d["id"],))
            else: return self.send(404, "{}")
        self.send(200, '{"ok":true}')

def up():
    init_db()
    for name, a in CFG["agents"].items():
        if a.get("enabled", True): threading.Thread(target=loop, args=(name, a), daemon=True).start()
    port = CFG.get("port", 8787)
    print(f"ABUZ8 RIG up -> http://127.0.0.1:{port}   brain: {brain()['model'] or 'none (keyword mode)'}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()

if __name__ == "__main__":
    a = sys.argv[1:] or ["up"]; init_db()
    if a[0] == "up": up()
    elif a[0] == "once": ag = CFG["agents"][a[1]]; RUNNERS[ag["kind"]](a[1], ag)
    elif a[0] == "task":
        seat = a[a.index("--to") + 1] if "--to" in a else ""; print("task #", add_task(a[1], seat))
    elif a[0] == "telegram-setup": telegram_setup()
    elif a[0] == "digest-now":
        dg = next((v for v in CFG["agents"].values() if v["kind"] == "digest"), {}); send_digest(dg); print("Digest sent.")
    elif a[0] == "status":
        with db() as c:
            for r in c.execute("SELECT score,source,title,action FROM signals WHERE status='new' ORDER BY score DESC LIMIT 20"): print(f"{r[0]:>3} {r[1][:18]:18} {r[2][:70]}  -> {r[3]}")
            for r in c.execute("SELECT id,seat,status,text FROM tasks ORDER BY id DESC LIMIT 20"): print(f"#{r[0]} {r[1]:12} {r[2]:7} {r[3][:80]}")
