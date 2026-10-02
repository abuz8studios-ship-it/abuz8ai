# ABUZ8 — Sovereign AI, built in the open

**Ahmad A M Odeh · Founder, ABUZ8 LLC · Dayton, Ohio · [abuz8ai.com](https://abuz8ai.com) · ceo@abuz8ai.com**

> Mission: generate the income to desalinate the ocean, turn the Sahara into a living rainforest, and build villages, mosques and a zero-emission, self-sufficient city built by AI — in symbiosis with the land. Step one: an AI agent in every pocket.

## What's in this repo
| Folder | What it is |
|---|---|
| `site/` | The abuz8ai.com homepage — mission, brains, shipped work, gallery, store, hubs |
| `rig/` | **ABUZ8 RIG** — always-on opportunity agents (signal, YouTube, Telegram digest, OpenRig bridge). Local brain = $0 tokens. `RUN_RIG.bat` |
| `vault/` | **ABUZ8 Vault Engine** — `RUN_VAULT.bat` |
| `record/` | The year, graded: 88 sessions, A–F report cards, open loops |

## The brains (measured on 2× RTX 5090, 128 GB RAM)
- **FAIE** — GLM-5.2-REAM 380.9B MoE on one RTX 5090 · 32.03 tok/s measured, bit-identical · ternary 129 GB → 70 GB · 9/9 proof checks
- **ABUZ8 Superbrain** — AHMAD-1 + MUTHALLATH-1 + FAIE fused: geometric · Arabic-root · frontier · Isnad-verified nightly QLoRA
- **First fleet** — Muse Glimmer 30B · Qwen3.8-27B (abliterated) · Nemotron 3 Nano Omni (1M ctx) · Ornith 1.0 35B-A3B · Qwen-AgentWorld 35B-A3B · Bonsai-27B Ternary

## The year, by the numbers
2,366 Claude Code sessions · 60,363 exchanges · ~7.09B tokens · 88 sessions graded · 38 shipped builds · 904 pages live

## Deploy the site
```
npx wrangler pages deploy site --project-name abuz8ai --branch main
```

## Hire me
Open to AI engineering / agent-systems roles and fixed-scope builds. Project Rescue $1,500 (48h) · Build Sprint $4,800 (2 weeks). **ceo@abuz8ai.com**
