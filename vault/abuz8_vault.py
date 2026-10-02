"""
ABUZ8 VAULT — your permanent, compounding memory of every Claude session.

Reads:   claude.ai data export (data-*.zip or conversations.json)  +  Claude Code sessions (~/.claude/projects/*.jsonl)
Writes:  one permanent Markdown file per session (full transcript + report card)
         every code file / code block ever produced, saved as real files + CODE_INDEX.csv (sha256, dedupe)
         SPEND.md  (real token usage for Claude Code, estimates for web chats, plus your own subscription log)
         BRAIN.md + brain.json   (the compounding brain: projects, decisions, rules, corrections, lessons, loops)
         OPEN_LOOPS.md (open vs. closed — a later session can close an earlier loop)
         HANDOFF.md, INVENTORY.csv, index.html dashboard, vault.sqlite (full-text search)

Compounding: sessions are processed OLDEST -> NEWEST. Each session is graded with the brain built from every
session before it, and returns a delta (new facts, closed loops, new rules) that is merged back in.
So session #500 is read by a brain that already knows sessions #1-#499.

Brains (auto, first alive wins — local first, sovereign by default):
    local vLLM/Qwen :8011  ->  Ollama :11434  ->  Anthropic API (ANTHROPIC_API_KEY, Haiku)  ->  heuristic (no AI)
Resumable: stop any time; rerun continues. New export later -> only new sessions are processed.

Usage:
    python abuz8_vault.py                      # auto-find export in Downloads, Claude Code in ~/.claude/projects
    python abuz8_vault.py --export D:\\data.zip --out E:\\ABU\\ABUZ8_VAULT --since 2026-01-17
    python abuz8_vault.py --brain haiku        # force a brain: auto|local|ollama|haiku|heuristic
    python abuz8_vault.py --render-only        # rebuild reports from what's already processed
    python abuz8_vault.py search "stripe live" # full-text search across every session
"""
import argparse, csv, glob, hashlib, html, json, os, random, re, sqlite3, sys, time, urllib.error, urllib.request, zipfile
from collections import Counter, defaultdict
from datetime import datetime

# ----------------------------------------------------------------------------- config
HOME = os.path.expanduser("~")
DEFAULT_OUT = r"E:\ABU\ABUZ8_VAULT" if os.name == "nt" and os.path.isdir("E:\\") else os.path.join(HOME, "ABUZ8_VAULT")
EXPORT_SEARCH = [os.path.join(HOME, "Downloads"), os.path.join(HOME, "Desktop"), os.getcwd()]
CODE_DIR = os.path.join(HOME, ".claude", "projects")
MAX_CHARS = 16000          # transcript text sent to the brain per session (head + tail)
DIGEST_CHARS = 7000        # brain digest sent with every session
# USD per million tokens — ESTIMATES, edit to your real rates. (input, output). cache read = 0.1x in, cache write = 1.25x in
PRICES = {"opus": (15.0, 75.0), "sonnet": (3.0, 15.0), "haiku": (1.0, 5.0), "fable": (15.0, 75.0), "mythos": (15.0, 75.0), "default": (3.0, 15.0)}
GPA = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
CODE_EXT = {"python": "py", "py": "py", "javascript": "js", "js": "js", "jsx": "jsx", "typescript": "ts", "ts": "ts", "tsx": "tsx",
            "html": "html", "css": "css", "json": "json", "bash": "sh", "sh": "sh", "shell": "sh", "powershell": "ps1", "ps1": "ps1",
            "bat": "bat", "cmd": "bat", "sql": "sql", "yaml": "yaml", "yml": "yaml", "go": "go", "rust": "rs", "rs": "rs", "c": "c",
            "cpp": "cpp", "cuda": "cu", "java": "java", "kotlin": "kt", "swift": "swift", "markdown": "md", "md": "md", "toml": "toml",
            "xml": "xml", "svg": "svg", "dockerfile": "Dockerfile", "vcl": "vcl", "": "txt"}

# ----------------------------------------------------------------------------- helpers
def log(*a): print(*a, flush=True)
def slug(s, n=48): return (re.sub(r"[^A-Za-z0-9]+", "-", s or "untitled").strip("-").lower() or "untitled")[:n]
def sha(s): return hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()
def wtext(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f: f.write(text)
def jload(path, default):
    try:
        with open(path, encoding="utf-8") as f: return json.load(f)
    except Exception: return default
def jsave(path, obj):
    tmp = path + ".tmp"; wtext(tmp, json.dumps(obj, ensure_ascii=False, indent=1)); os.replace(tmp, path)

def block_text(content):
    """Flatten a message content (str or list of blocks) into (text, tool_calls)."""
    if isinstance(content, str): return content, []
    texts, tools = [], []
    for b in content or []:
        if not isinstance(b, dict): continue
        t = b.get("type")
        if t == "text" and b.get("text"): texts.append(b["text"])
        elif t == "thinking": continue
        elif t == "tool_use":
            tools.append({"name": b.get("name", ""), "input": b.get("input") or {}})
            inp = b.get("input") or {}
            brief = inp.get("command") or inp.get("path") or inp.get("file_path") or inp.get("query") or inp.get("url") or ""
            texts.append(f"[tool:{b.get('name','')}] {str(brief)[:200]}")
        elif t == "tool_result":
            c = b.get("content")
            s = c if isinstance(c, str) else " ".join(x.get("text", "") for x in (c or []) if isinstance(x, dict))
            if s: texts.append("[result] " + s[:600])
    return "\n".join(texts), tools

# ----------------------------------------------------------------------------- ingest
def find_export(explicit):
    cands = [explicit] if explicit else []
    for base in EXPORT_SEARCH:
        cands += sorted(glob.glob(os.path.join(base, "data-*.zip")), key=os.path.getmtime, reverse=True)
        cands += glob.glob(os.path.join(base, "**", "conversations.json"), recursive=True)[:3]
    for p in cands:
        if p and os.path.exists(p): return p
    return None

def load_web(path):
    if not path: return []
    if os.path.isdir(path): path = os.path.join(path, "conversations.json")
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.endswith("conversations.json")), None)
            if not name: log("! no conversations.json in", path); return []
            raw = z.read(name).decode("utf-8-sig")
    else:
        with open(path, encoding="utf-8-sig") as f: raw = f.read()
    out = []
    for c in json.loads(raw):
        msgs = []
        for m in c.get("chat_messages") or c.get("messages") or []:
            text, tools = block_text(m.get("content") if m.get("content") else m.get("text", ""))
            if not text and m.get("text"): text = m["text"]
            for a in (m.get("attachments") or []) + (m.get("files") or []):
                nm = a.get("file_name") or a.get("name") or "file"
                text += f"\n[attachment: {nm}]"
            role = "user" if m.get("sender") in ("human", "user") else "assistant"
            msgs.append({"role": role, "text": text or "", "tools": tools, "ts": m.get("created_at", "")})
        out.append({"id": c.get("uuid"), "source": "web", "title": c.get("name") or "(untitled)",
                    "created": c.get("created_at", ""), "updated": c.get("updated_at", ""),
                    "url": f"https://claude.ai/chat/{c.get('uuid')}", "messages": msgs, "usage": None, "cwd": ""})
    return out

def load_code(root):
    out = []
    if not root or not os.path.isdir(root): return out
    for fp in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        msgs, usage, models, first_ts, last_ts, cwd = [], Counter(), Counter(), "", "", ""
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try: ev = json.loads(line)
                    except Exception: continue
                    if not isinstance(ev, dict): continue
                    ts = ev.get("timestamp", ""); cwd = cwd or ev.get("cwd", "")
                    if ts: first_ts = first_ts or ts; last_ts = ts
                    m = ev.get("message") or {}
                    if ev.get("type") not in ("user", "assistant") or not isinstance(m, dict): continue
                    text, tools = block_text(m.get("content"))
                    u = m.get("usage") or {}
                    if u:
                        mdl = m.get("model", "unknown"); models[mdl] += 1
                        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                            usage[f"{mdl}|{k}"] += int(u.get(k) or 0)
                    if text or tools: msgs.append({"role": ev["type"], "text": text, "tools": tools, "ts": ts})
        except Exception as e:
            log("! skip", fp, e); continue
        if not msgs: continue
        first_user = next((m["text"] for m in msgs if m["role"] == "user" and m["text"].strip()), "Claude Code session")
        out.append({"id": "cc-" + os.path.splitext(os.path.basename(fp))[0], "source": "code",
                    "title": re.sub(r"\s+", " ", first_user)[:90], "created": first_ts, "updated": last_ts,
                    "url": fp, "messages": msgs, "usage": dict(usage), "cwd": cwd})
    return out

# ----------------------------------------------------------------------------- code extraction
FENCE = re.compile(r"```([A-Za-z0-9_+.#-]*)[^\n]*\n(.*?)```", re.S)

def extract_code(sess):
    """Return list of (filename, content, origin). Real files the AI wrote come first, then fenced blocks."""
    files, n = [], 0
    for m in sess["messages"]:
        for t in m.get("tools", []):
            inp = t.get("input") or {}
            body = inp.get("file_text") or inp.get("content") if isinstance(inp, dict) else None
            path = (inp.get("file_path") or inp.get("path")) if isinstance(inp, dict) else None
            if isinstance(body, str) and path and len(body) > 20:
                files.append((os.path.basename(str(path).replace("\\", "/")) or "file.txt", body, f"{t['name']} -> {path}"))
            elif t.get("name") in ("Edit", "MultiEdit", "str_replace", "edit_block") and isinstance(inp, dict):
                p = inp.get("file_path") or inp.get("path") or "file"
                new = inp.get("new_string") or inp.get("new_str") or ""
                if new: files.append((os.path.basename(str(p).replace("\\", "/")) + ".edit.txt",
                                      f"# EDIT of {p}\n--- old\n{inp.get('old_string') or inp.get('old_str') or ''}\n+++ new\n{new}\n", f"{t['name']} -> {p}"))
        if m["role"] == "assistant":
            for lang, body in FENCE.findall(m["text"]):
                if len(body.strip().splitlines()) < 3: continue
                n += 1; ext = CODE_EXT.get(lang.lower(), "txt")
                files.append((f"block_{n:03d}.{ext}", body, f"fenced {lang or 'text'}"))
    return files

# ----------------------------------------------------------------------------- spend
def price_for(model):
    ml = (model or "").lower()
    for k, v in PRICES.items():
        if k in ml: return v
    return PRICES["default"]

def spend_for(sess):
    if sess["usage"]:
        tok = Counter(); usd = 0.0
        for key, n in sess["usage"].items():
            model, kind = key.split("|"); pin, pout = price_for(model)
            rate = {"input_tokens": pin, "output_tokens": pout, "cache_read_input_tokens": pin * 0.1,
                    "cache_creation_input_tokens": pin * 1.25}[kind]
            usd += n / 1e6 * rate; tok[kind] += n
        return {"tokens": sum(tok.values()), "detail": dict(tok), "usd_equiv": round(usd, 4), "basis": "real usage from Claude Code log"}
    chars = sum(len(m["text"]) for m in sess["messages"])
    tokens = chars // 4
    return {"tokens": tokens, "detail": {"est_chars": chars}, "usd_equiv": round(tokens / 1e6 * 9.0, 4),
            "basis": "ESTIMATE (chars/4; claude.ai export has no token counts; covered by your subscription)"}

# ----------------------------------------------------------------------------- brains
def post(url, payload, headers=None, timeout=240):
    req = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read())

def openai_style(base, model):
    def ask(prompt):
        r = post(f"{base}/v1/chat/completions", {"model": model, "temperature": 0.1, "max_tokens": 2500,
                 "messages": [{"role": "user", "content": prompt}]})
        return r["choices"][0]["message"]["content"]
    return ask

def anthropic_ask(prompt):
    key = os.environ["ANTHROPIC_API_KEY"]
    for attempt in range(6):
        try:
            r = post("https://api.anthropic.com/v1/messages",
                     {"model": os.environ.get("VAULT_MODEL", "claude-haiku-4-5-20251001"), "max_tokens": 2500,
                      "messages": [{"role": "user", "content": prompt}]},
                     {"x-api-key": key, "anthropic-version": "2023-06-01"})
            return "".join(b.get("text", "") for b in r.get("content", []))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 529): time.sleep(min(60, 2 ** attempt) + random.random()); continue
            raise
    raise RuntimeError("anthropic retries exhausted")

def discover(base):
    try:
        with urllib.request.urlopen(f"{base}/v1/models", timeout=5) as r:
            ids = [m["id"] for m in json.loads(r.read()).get("data", [])]
        return ids[0] if ids else None
    except Exception: return None

def pick_brain(choice):
    tries = []
    if choice in ("auto", "local"): tries.append(("local vLLM :8011", "http://127.0.0.1:8011", "QWEN_MODEL"))
    if choice in ("auto", "ollama"): tries.append(("ollama :11434", "http://127.0.0.1:11434", "OLLAMA_MODEL"))
    for label, base, env in tries:
        model = os.environ.get(env) or discover(base)
        if not model: continue
        ask = openai_style(base, model)
        try: ask("Reply with {\"ok\":true}"); return f"{label} ({model})", ask
        except Exception: pass
    if choice in ("auto", "haiku") and os.environ.get("ANTHROPIC_API_KEY"): return "anthropic haiku", anthropic_ask
    if choice == "haiku": sys.exit("--brain haiku needs ANTHROPIC_API_KEY set.")
    return "heuristic (no AI — rough grades, no loop closing)", None

def parse_json(s):
    s = re.sub(r"^```(?:json)?|```$", "", (s or "").strip(), flags=re.M)
    m = re.search(r"\{.*\}", s, re.S)
    return json.loads(m.group(0)) if m else None

# ----------------------------------------------------------------------------- the compounding brain
def new_brain(): return {"sessions": 0, "projects": {}, "loops": [], "rules": [], "decisions": [], "corrections": [],
                         "lessons": [], "people": {}, "next_loop": 1}

def digest(b, title=""):
    words = set(re.findall(r"[a-z0-9]{4,}", title.lower()))
    projs = sorted(b["projects"].items(), key=lambda kv: kv[1].get("last", ""), reverse=True)
    lines = ["KNOWN PROJECTS (name [status] last-touched — latest facts):"]
    for name, p in projs[:30]:
        lines.append(f"- {name} [{p.get('status','?')}] {p.get('last','')[:10]} — " + " | ".join(p.get("facts", [])[-3:]))
    open_loops = [l for l in b["loops"] if not l.get("closed")]
    rel = [l for l in open_loops if words & set(re.findall(r"[a-z0-9]{4,}", (l["text"] + " " + l.get("project", "")).lower()))]
    shown = (rel + [l for l in reversed(open_loops) if l not in rel])[:45]
    lines.append("OPEN LOOPS (id: project — text):")
    lines += [f"- {l['id']}: {l.get('project','')} — {l['text']}" for l in shown]
    if b["rules"]: lines.append("OWNER RULES: " + " | ".join(b["rules"][-15:]))
    if b["corrections"]: lines.append("PAST CORRECTIONS: " + " | ".join(b["corrections"][-10:]))
    if b["people"]: lines.append("PEOPLE: " + " | ".join(f"{k} ({v})" for k, v in list(b["people"].items())[-20:]))
    return "\n".join(lines)[:DIGEST_CHARS]

PROMPT = """You are the memory keeper for a solo founder (Ahmad, ABUZ8 LLC). His rule: DONE = shipped, verified, usable by a real person. Claims are not proof.
You receive (1) the BRAIN built from all his earlier sessions and (2) the next SESSION, in date order.
Grade the session and update the brain. Return ONLY one JSON object with these keys:
"project": main project name (reuse a KNOWN PROJECTS name when it is the same thing),
"summary": 2-3 sentences in your own words,
"built": concrete things produced (files, deploys, scripts),
"decisions": decisions made,
"open_loops": things started or promised but NOT closed in this session (each under 15 words),
"closed_loop_ids": ids from OPEN LOOPS that this session clearly finished (only with evidence in the session),
"proof": "verified" | "claimed" | "none",
"revenue_link": true if it moves directly toward a paying customer,
"grade": "A" | "B" | "C" | "D" | "F",
"grade_reason": one sentence,
"how_to_use": one or two sentences on how to run/use what was built (empty if nothing),
"brain_delta": {
  "project_updates": [{"project": name, "status": "idea|building|built|shipped|killed|paused", "fact": short durable fact}],
  "rules": owner rules or preferences he stated,
  "corrections": things he corrected the AI on,
  "lessons": durable lessons learned (what worked / what failed and why),
  "people": [{"name": name, "relation": relation}]
}
Rubric: A = shipped AND verified (URL 200, test passed, file confirmed on disk); B = real artifact not shipped; C = useful plan/research only; D = mostly talk, loops left open; F = nothing usable or went in circles.
Also note if this session rebuilds something the BRAIN says already exists — put that in "lessons".

BRAIN:
{brain}

SESSION ({source}, {date}, "{title}"):
{text}
"""

LOOP_RX = re.compile(r"(?i)\b(next step|todo|still need|not yet|blocked|blocker|pending|will build|i'll build|left open|not started)\b[^.\n]{0,120}")

def heuristic_card(text):
    loops = list(dict.fromkeys(m.group(0).strip() for m in LOOP_RX.finditer(text)))[:6]
    verified = bool(re.search(r"(?i)(200 OK|tests? pass(ed)?|all green|sha256 verified|live at https?://)", text))
    built = bool(re.search(r"(?i)(created|wrote|saved) .{0,40}\.(py|js|html|md|json|bat|ps1|exe|zip)", text))
    grade = "A" if verified and built else "B" if built else ("D" if loops else "C")
    return {"project": "", "summary": re.sub(r"\s+", " ", text[:300]), "built": [], "decisions": [], "open_loops": loops,
            "closed_loop_ids": [], "proof": "verified" if verified else ("claimed" if built else "none"),
            "revenue_link": bool(re.search(r"(?i)stripe|customer|invoice|pricing|revenue|sale", text)),
            "grade": grade, "grade_reason": "heuristic grade (no AI brain available)", "how_to_use": "", "brain_delta": {}}

def dedupe_add(lst, items, cap=80):
    seen = {x.lower() for x in lst}
    for it in items or []:
        it = str(it).strip()
        if it and it.lower() not in seen: lst.append(it); seen.add(it.lower())
    del lst[:-cap]

def merge(b, card, sess):
    date = (sess["created"] or "")[:10]
    d = card.get("brain_delta") or {}
    for u in d.get("project_updates") or []:
        if not isinstance(u, dict) or not u.get("project"): continue
        p = b["projects"].setdefault(u["project"], {"status": "", "first": date, "last": date, "sessions": 0, "facts": []})
        p["status"] = u.get("status") or p["status"]; p["last"] = max(p["last"], date); p["sessions"] += 1
        dedupe_add(p["facts"], [u.get("fact")], cap=40)
    proj = card.get("project") or ""
    if proj and proj not in b["projects"]:
        b["projects"][proj] = {"status": "", "first": date, "last": date, "sessions": 1, "facts": []}
    closed = set(map(str, card.get("closed_loop_ids") or []))
    for l in b["loops"]:
        if l["id"] in closed and not l.get("closed"): l["closed"] = date; l["closed_by"] = sess["id"]
    for t in card.get("open_loops") or []:
        b["loops"].append({"id": f"L{b['next_loop']}", "text": str(t), "project": proj, "opened": date, "session": sess["id"]})
        b["next_loop"] += 1
    dedupe_add(b["rules"], d.get("rules")); dedupe_add(b["corrections"], d.get("corrections"))
    dedupe_add(b["lessons"], d.get("lessons"), cap=150)
    dedupe_add(b["decisions"], [f"{date} {x}" for x in card.get("decisions") or []], cap=400)
    for p in d.get("people") or []:
        if isinstance(p, dict) and p.get("name"): b["people"][p["name"]] = p.get("relation", "")
    b["sessions"] += 1

# ----------------------------------------------------------------------------- per-session permanent file
def transcript_text(sess, limit=None):
    lines = [f"{'H' if m['role']=='user' else 'A'}: {m['text'].strip()}" for m in sess["messages"] if m["text"].strip()]
    full = "\n".join(lines)
    if limit and len(full) > limit: full = full[:limit // 2] + "\n...[middle trimmed]...\n" + full[-limit // 2:]
    return full

def session_path(out, sess):
    d = (sess["created"] or "0000-00-00")[:10]
    return os.path.join(out, "sessions", d[:4], d[5:7], f"{d}_{sess['source']}_{slug(sess['title'])}_{sess['id'][-8:]}.md")

def write_session(out, sess, card, spend, codefiles):
    L = lambda xs: "\n".join(f"- {x}" for x in xs) if xs else "- none"
    body = [f"---\nid: {sess['id']}\nsource: {sess['source']}\ntitle: {json.dumps(sess['title'], ensure_ascii=False)}\n"
            f"created: {sess['created']}\nupdated: {sess['updated']}\nurl: {sess['url']}\ngrade: {card['grade']}\nproof: {card.get('proof')}\n"
            f"project: {json.dumps(card.get('project',''), ensure_ascii=False)}\ntokens: {spend['tokens']}\n---\n",
            f"# {sess['title']}\n", f"**Grade {card['grade']}** · proof: {card.get('proof')} · {card.get('grade_reason','')}\n",
            f"## Summary\n{card.get('summary','')}\n", f"## Built\n{L(card.get('built'))}\n", f"## How to use\n{card.get('how_to_use') or '-'}\n",
            f"## Decisions\n{L(card.get('decisions'))}\n", f"## Open loops\n{L(card.get('open_loops'))}\n",
            f"## Code saved ({len(codefiles)})\n{L(codefiles)}\n",
            f"## Spend\n{spend['tokens']:,} tokens · ~${spend['usd_equiv']} API-equivalent · {spend['basis']}\n",
            "## Full transcript\n"]
    for m in sess["messages"]:
        if m["text"].strip(): body.append(f"\n### {'Ahmad' if m['role']=='user' else 'Claude'}  {m.get('ts','')[:19]}\n{m['text'].strip()}\n")
    p = session_path(out, sess); wtext(p, "\n".join(body)); return p

def save_code(out, sess, files, index, seen_hash):
    saved = []
    d = (sess["created"] or "0000-00-00")[:10]
    folder = os.path.join(out, "code", f"{d}_{slug(sess['title'], 36)}_{sess['id'][-8:]}")
    for i, (name, content, origin) in enumerate(files, 1):
        h = sha(content)
        name = re.sub(r'[<>:"/\\|?*]', "_", name)[:80]
        if h in seen_hash:
            index.append([d, sess["id"], name, origin, h, len(content.splitlines()), f"DUPLICATE of {seen_hash[h]}"]); continue
        p = os.path.join(folder, f"{i:03d}_{name}")
        wtext(p, content); rel = os.path.relpath(p, out); seen_hash[h] = rel
        index.append([d, sess["id"], name, origin, h, len(content.splitlines()), rel]); saved.append(rel)
    return saved

# ----------------------------------------------------------------------------- reports
def render(out, cards, brain, spend_rows, code_index, ask):
    cards = sorted(cards, key=lambda c: c.get("created", ""))
    # INVENTORY.csv
    with open(os.path.join(out, "INVENTORY.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["date", "source", "grade", "proof", "project", "title", "tokens", "usd_equiv", "open_loops", "code_files", "file", "url"])
        for c in cards: w.writerow([c["created"][:10], c["source"], c["grade"], c.get("proof"), c.get("project"), c["title"], c["tokens"],
                                    c["usd_equiv"], len(c.get("open_loops") or []), c["code_files"], c["file"], c["url"]])
    with open(os.path.join(out, "CODE_INDEX.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["date", "session", "name", "origin", "sha256", "lines", "saved_as"]); w.writerows(code_index)
    # OPEN_LOOPS.md
    open_l = [l for l in brain["loops"] if not l.get("closed")]; closed_l = [l for l in brain["loops"] if l.get("closed")]
    o = [f"# OPEN LOOPS — {len(open_l)} open · {len(closed_l)} closed by later sessions", f"_Generated {datetime.now():%Y-%m-%d %H:%M}_\n"]
    byp = defaultdict(list)
    for l in open_l: byp[l.get("project") or "(no project)"].append(l)
    for p in sorted(byp, key=lambda k: -len(byp[k])):
        o.append(f"\n## {p} ({len(byp[p])})"); o += [f"- [ ] {l['opened']} {l['id']}: {l['text']}" for l in byp[p]]
    o.append("\n## Closed later"); o += [f"- [x] {l['opened']} → {l['closed']} {l['id']}: {l['text']}" for l in closed_l]
    wtext(os.path.join(out, "OPEN_LOOPS.md"), "\n".join(o))
    # BRAIN.md
    B = [f"# ABUZ8 BRAIN — compounded from {brain['sessions']} sessions", f"_Generated {datetime.now():%Y-%m-%d %H:%M}_\n", "## Projects"]
    for name, p in sorted(brain["projects"].items(), key=lambda kv: kv[1].get("last", ""), reverse=True):
        B.append(f"\n### {name}  [{p.get('status') or '?'}]  {p.get('first','')} → {p.get('last','')} · {p.get('sessions',0)} sessions")
        B += [f"- {x}" for x in p.get("facts", [])]
    for key, title in [("rules", "Owner rules"), ("corrections", "Corrections (AI got it wrong)"), ("lessons", "Lessons"), ("decisions", "Decision log")]:
        B.append(f"\n## {title}"); B += [f"- {x}" for x in brain[key]]
    B.append("\n## People"); B += [f"- {k}: {v}" for k, v in brain["people"].items()]
    wtext(os.path.join(out, "BRAIN.md"), "\n".join(B))
    # SPEND.md
    tot_tok = sum(c["tokens"] for c in cards); real = [c for c in cards if c["source"] == "code"]
    months = defaultdict(lambda: [0, 0, 0.0])
    for c in cards: mm = months[c["created"][:7]]; mm[0] += 1; mm[1] += c["tokens"]; mm[2] += c["usd_equiv"]
    S = ["# SPEND", f"Sessions: {len(cards)} · tokens: {tot_tok:,} · API-equivalent: ${sum(c['usd_equiv'] for c in cards):,.2f}",
         f"Claude Code (real usage): {len(real)} sessions · ${sum(c['usd_equiv'] for c in real):,.2f} API-equivalent",
         "Web chats are estimates (chars/4) — the export has no token counts. Prices in PRICES at top of the script are estimates; edit them.\n",
         "| Month | Sessions | Tokens | API-equivalent $ |", "|---|---|---|---|"]
    S += [f"| {m} | {v[0]} | {v[1]:,} | {v[2]:,.2f} |" for m, v in sorted(months.items())]
    manual = os.path.join(out, "spend_manual.csv")
    if not os.path.exists(manual):
        wtext(manual, "date,item,usd,note\n2026-01-17,Claude subscription,0,edit me: add every subscription / API / hardware payment\n")
    rows = list(csv.DictReader(open(manual, encoding="utf-8")))
    S += ["\n## What you actually paid (from spend_manual.csv)", "| Date | Item | USD | Note |", "|---|---|---|---|"]
    S += [f"| {r.get('date')} | {r.get('item')} | {r.get('usd')} | {r.get('note')} |" for r in rows]
    try: S.append(f"\n**Total paid: ${sum(float(r.get('usd') or 0) for r in rows):,.2f}**")
    except ValueError: pass
    wtext(os.path.join(out, "SPEND.md"), "\n".join(S))
    # HANDOFF.md
    gp = sum(GPA.get(c["grade"], 0) for c in cards) / max(len(cards), 1)
    base = [f"# HANDOFF — {len(cards)} sessions · GPA {gp:.2f} · {sum(1 for c in cards if c['grade']=='A')} verified ships · {len(open_l)} open loops",
            f"_Generated {datetime.now():%Y-%m-%d %H:%M}. Full detail: BRAIN.md, OPEN_LOOPS.md, sessions/._\n"]
    handoff = ""
    if ask:
        try:
            handoff = ask("Write a blunt HANDOFF in Markdown for Ahmad (solo founder, ABUZ8 LLC) from this compounded brain of all his AI sessions. "
                          "Sections: 1 Where each project really stands (real vs claimed) 2 Shipped + verified 3 Loops that keep recurring "
                          "4 Things rebuilt more than once 5 Five next actions ranked by distance to a paying customer 6 Kill list. "
                          "Use only facts given. No praise, no hedging.\n\n" + digest(brain)[:6000] + "\n\nLESSONS:\n" + "\n".join(brain["lessons"][-40:]))
        except Exception as e: handoff = f"(AI handoff failed: {e})"
    if not handoff:
        handoff = "## Most active projects\n" + "\n".join(f"- {n} [{p.get('status') or '?'}] last {p.get('last')}" for n, p in
                  sorted(brain["projects"].items(), key=lambda kv: -kv[1].get("sessions", 0))[:20])
        handoff += "\n\n## Oldest open loops\n" + "\n".join(f"- {l['opened']} {l['text']}" for l in open_l[:25])
    wtext(os.path.join(out, "HANDOFF.md"), "\n".join(base) + "\n" + handoff)
    # index.html
    write_html(out, cards, brain, open_l, gp)

def write_html(out, cards, brain, open_l, gp):
    e = lambda s: html.escape(str(s or ""))
    data = [{"d": c["created"][:10], "s": c["source"], "g": c["grade"], "p": c.get("project", ""), "t": c["title"], "sum": c.get("summary", ""),
             "f": c["file"].replace("\\", "/"), "u": c["url"] if c["source"] == "web" else "", "l": c.get("open_loops") or [], "pr": c.get("proof")} for c in cards]
    months = defaultdict(list)
    for c in cards: months[c["created"][:7]].append(c)
    rows = "".join(f"<tr><td>{m}</td><td>{len(v)}</td><td>{sum(GPA.get(c['grade'],0) for c in v)/len(v):.2f}</td>"
                   f"<td>{' · '.join(str(sum(1 for c in v if c['grade']==g)) for g in 'ABCDF')}</td><td>{sum(c['tokens'] for c in v):,}</td></tr>"
                   for m, v in sorted(months.items(), reverse=True))
    projs = "".join(f"<tr><td>{e(n)}</td><td>{e(p.get('status'))}</td><td>{e(p.get('first'))} → {e(p.get('last'))}</td><td>{p.get('sessions',0)}</td></tr>"
                    for n, p in sorted(brain["projects"].items(), key=lambda kv: kv[1].get("last", ""), reverse=True)[:60])
    page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ABUZ8 Vault</title><link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600&family=DM+Sans:wght@400;600&display=swap" rel="stylesheet">
<style>:root{{--card:#161B22;--line:rgba(200,165,92,.12);--gold:#C8A55C;--tx:#E2DDD3;--mut:#8A8478}}
body{{margin:0;color:var(--tx);font:15px/1.5 'DM Sans',system-ui,sans-serif;background:linear-gradient(165deg,#0F1318 0,#10161d 1500px,#121424 3000px,#0F1318 4500px);background-color:#0F1318}}
main{{max-width:1120px;margin:auto;padding:24px 16px}}h1,h2{{font-family:'Playfair Display',Georgia,serif;color:var(--gold);font-weight:600}}
.k{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}.k div{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}}
.k b{{display:block;font:600 26px 'Playfair Display',serif;color:var(--gold)}}.w{{overflow-x:auto}}table{{border-collapse:collapse;width:100%}}
td,th{{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;font-size:14px}}th{{color:var(--gold)}}
input,button{{font:inherit;color:var(--tx);background:#1b212a;border:1px solid var(--line);border-radius:8px;padding:8px 12px}}
.c{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:8px 0}}.m{{color:var(--mut);font-size:13px}}
.g{{display:inline-block;width:26px;text-align:center;border-radius:6px;font-weight:700;margin-right:8px}}.gA{{background:#3D6B52}}.gB{{background:#2A4A6B}}.gC{{background:#3a3a28}}.gD{{background:#2E2550}}.gF{{background:#5a2a2a}}
a{{color:var(--tx)}}</style></head><body><main>
<h1>ABUZ8 Vault</h1><div class="m">Every session you and Claude ever had · generated {datetime.now():%Y-%m-%d %H:%M}</div>
<div class="k" style="margin-top:14px"><div><b>{len(cards)}</b>sessions</div><div><b>{gp:.2f}</b>GPA</div><div><b>{sum(1 for c in cards if c['grade']=='A')}</b>verified ships</div>
<div><b>{len(open_l)}</b>open loops</div><div><b>{len(brain['projects'])}</b>projects</div><div><b>{sum(c['code_files'] for c in cards)}</b>code files</div></div>
<h2>By month</h2><div class="w"><table><tr><th>Month</th><th>Sessions</th><th>GPA</th><th>A · B · C · D · F</th><th>Tokens</th></tr>{rows}</table></div>
<h2>Projects (from the brain)</h2><div class="w"><table><tr><th>Project</th><th>Status</th><th>Span</th><th>Sessions</th></tr>{projs}</table></div>
<h2>Sessions</h2><input id="q" placeholder="Search…" style="width:100%;max-width:520px"><div id="L"></div>
<script>const D={json.dumps(data, ensure_ascii=False)};const esc=s=>String(s||"").replace(/[&<>"]/g,c=>({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}}[c]));
function r(){{const q=document.getElementById('q').value.toLowerCase();document.getElementById('L').innerHTML=D.filter(x=>!q||(x.t+' '+x.p+' '+x.sum).toLowerCase().includes(q)).reverse().slice(0,500).map(x=>
`<div class="c"><span class="g g${{x.g}}">${{x.g}}</span><a href="${{esc(x.f)}}">${{esc(x.t)}}</a> ${{x.u?`<a class="m" href="${{esc(x.u)}}">open chat</a>`:""}}<div class="m">${{x.d}} · ${{x.s}} · ${{esc(x.p)}} · proof: ${{x.pr}}</div><div>${{esc(x.sum)}}</div>${{x.l.length?`<div class="m">Open: ${{x.l.map(esc).join(" · ")}}</div>`:""}}</div>`).join("")}}
document.getElementById('q').oninput=r;r();</script></main></body></html>"""
    wtext(os.path.join(out, "index.html"), page)

# ----------------------------------------------------------------------------- search db
def index_fts(out, sess, card):
    con = sqlite3.connect(os.path.join(out, "vault.sqlite"))
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS s USING fts5(id UNINDEXED, date UNINDEXED, title, project, summary, body)")
    con.execute("DELETE FROM s WHERE id=?", (sess["id"],))
    con.execute("INSERT INTO s VALUES (?,?,?,?,?,?)", (sess["id"], sess["created"][:10], sess["title"], card.get("project", ""),
                                                      card.get("summary", ""), transcript_text(sess)))
    con.commit(); con.close()

def search(out, q):
    con = sqlite3.connect(os.path.join(out, "vault.sqlite"))
    for r in con.execute("SELECT date,title,project,snippet(s,5,'[',']','…',14) FROM s WHERE s MATCH ? ORDER BY rank LIMIT 25", (q,)):
        print(f"{r[0]}  {r[1][:60]}  ({r[2]})\n    {r[3]}\n")

# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="ABUZ8 Vault — permanent compounding memory of every Claude session")
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "search"]); ap.add_argument("query", nargs="?")
    ap.add_argument("--export"); ap.add_argument("--code-dir", default=CODE_DIR); ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--since", default="2000-01-01"); ap.add_argument("--limit", type=int)
    ap.add_argument("--brain", default="auto", choices=["auto", "local", "ollama", "haiku", "heuristic"])
    ap.add_argument("--no-code", action="store_true", help="skip Claude Code sessions"); ap.add_argument("--render-only", action="store_true")
    a = ap.parse_args(); out = a.out; os.makedirs(out, exist_ok=True)
    if a.cmd == "search": return search(out, a.query or "")
    state_p, brain_p, cards_p, codeidx_p = (os.path.join(out, n) for n in ("state.json", "brain.json", "cards.jsonl", "code_index.json"))
    state = jload(state_p, {"done": {}}); brain = jload(brain_p, new_brain()); code_index = jload(codeidx_p, [])
    seen_hash = {r[4]: r[6] for r in code_index if not str(r[6]).startswith("DUPLICATE")}
    cards = {}
    if os.path.exists(cards_p):
        for line in open(cards_p, encoding="utf-8"):
            try: c = json.loads(line); cards[c["id"]] = c
            except Exception: pass
    label, ask = pick_brain("heuristic" if a.render_only else a.brain)
    if not a.render_only:
        exp = find_export(a.export)
        sessions = load_web(exp) + ([] if a.no_code else load_code(a.code_dir))
        log(f"Export: {exp or 'none found'} | Claude Code dir: {'skipped' if a.no_code else a.code_dir}")
        sessions = [s for s in sessions if (s["created"] or "")[:10] >= a.since and s["id"] not in state["done"]]
        sessions.sort(key=lambda s: s["created"] or "")
        if a.limit: sessions = sessions[:a.limit]
        log(f"Brain: {label}\nOut: {out}\nAlready in vault: {len(state['done'])} | to process now: {len(sessions)}  (oldest -> newest, compounding)")
        with open(cards_p, "a", encoding="utf-8") as cf:
            for i, s in enumerate(sessions, 1):
                text = transcript_text(s, MAX_CHARS); card = None
                if ask and text.strip():
                    prompt = PROMPT.replace("{brain}", digest(brain, s["title"])).replace("{source}", s["source"]) \
                                   .replace("{date}", (s["created"] or "")[:10]).replace("{title}", s["title"]).replace("{text}", text)
                    for _ in range(2):
                        try: card = parse_json(ask(prompt)); break
                        except Exception: time.sleep(2)
                if not card or card.get("grade") not in GPA: card = heuristic_card(text)
                for k in ("built", "decisions", "open_loops", "closed_loop_ids"):
                    card[k] = [str(x) for x in (card.get(k) or [])][:15]
                merge(brain, card, s)
                spend = spend_for(s)
                saved = save_code(out, s, extract_code(s), code_index, seen_hash)
                fpath = write_session(out, s, card, spend, saved)
                index_fts(out, s, card)
                rec = {**{k: card.get(k) for k in ("project", "summary", "built", "decisions", "open_loops", "proof", "revenue_link",
                                                    "grade", "grade_reason", "how_to_use")},
                       "id": s["id"], "source": s["source"], "title": s["title"], "created": s["created"] or "", "url": s["url"],
                       "tokens": spend["tokens"], "usd_equiv": spend["usd_equiv"], "code_files": len(saved),
                       "file": os.path.relpath(fpath, out), "brain": label}
                cf.write(json.dumps(rec, ensure_ascii=False) + "\n"); cf.flush(); cards[s["id"]] = rec
                state["done"][s["id"]] = rec["created"]
                jsave(brain_p, brain); jsave(state_p, state); jsave(codeidx_p, code_index)   # checkpoint every session
                log(f"[{i}/{len(sessions)}] {rec['grade']}  {rec['created'][:10]}  {s['source']:4}  {s['title'][:60]}  (+{len(saved)} code)")
    render(out, list(cards.values()), brain, None, code_index, ask)
    log(f"\nVAULT READY -> {out}\n  index.html · HANDOFF.md · BRAIN.md · OPEN_LOOPS.md · SPEND.md · INVENTORY.csv · CODE_INDEX.csv\n"
        f"  sessions/ (one permanent file each) · code/ (every file and block) · vault.sqlite (search: python abuz8_vault.py search \"words\")")

if __name__ == "__main__":
    main()
