# ABUZ8 VAULT

Your permanent, compounding memory of every Claude session — claude.ai chats + Claude Code sessions.

## Run (Pegasus)
1. claude.ai → Settings → Privacy → **Export data** → put the emailed `data-*.zip` in Downloads.
2. Copy this folder to `C:\Users\wirec\ABUZ8_VAULT_ENGINE\` and double-click **RUN_VAULT.bat**.
   Option 1 uses your local brain (start Qwen on :8011 or Ollama first). Option 2 uses Haiku.
3. Output lands in `E:\ABU\ABUZ8_VAULT\` — open `index.html`.

Claude Code sessions are read automatically from `C:\Users\wirec\.claude\projects\`.
Stop any time; rerun continues. New export next month → only new sessions are processed and the brain keeps growing.

## What you get
| File | What it is |
|---|---|
| `sessions/YYYY/MM/*.md` | One permanent file per session: report card, how to use what was built, spend, full transcript |
| `code/` + `CODE_INDEX.csv` | Every file the AI wrote and every code block, as real files, sha256-deduped |
| `BRAIN.md` / `brain.json` | The compounding brain: projects + status, decisions, your rules, corrections, lessons, people |
| `OPEN_LOOPS.md` | Every open loop by project — and which later session closed which loop |
| `HANDOFF.md` | Where everything stands + 5 next actions ranked by distance to a paying customer |
| `SPEND.md` + `spend_manual.csv` | Token spend (real for Claude Code, estimated for web) + your own payment log |
| `INVENTORY.csv`, `index.html`, `vault.sqlite` | Spreadsheet, dashboard, full-text search |

## How the compounding works
Sessions are read oldest → newest. Each one is graded with the brain built from every session before it,
and returns a delta (project status, new facts, rules, corrections, lessons, loops opened and loops closed)
that is merged back in and saved before the next session. Session 500 is read by a brain that knows 1–499.

## Knobs
`--since 2026-01-17` · `--brain auto|local|ollama|haiku|heuristic` · `--no-code` · `--limit N` ·
`--render-only` · `--out PATH` · `search "words"`. Edit `PRICES` at the top of the script to your real rates.
Everything stays on your machine unless you choose the Haiku option.
