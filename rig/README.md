# ABUZ8 RIG

One board, many agents. The always-on agents run on your local brain, so they cost zero Claude tokens.

| Agent | What it does | Cost |
|---|---|---|
| signal | Every hour scans Hacker News (last 10 days), Reddit (forhire, slavelabour, n8n, smallbusiness, askcarsales), Devpost hackathons, Product Hunt. Local brain scores each one 0-100 for "money for ABUZ8 in 30 days" and writes the next step. | local brain |
| youtube | Every 3 hours checks Nate Herk, RoboNuggets, Jack Roberts, Julian Goldie, Greg Isenberg, Starter Story. Pulls the transcript with yt-dlp, local brain extracts up to 5 executable plays, tools, and how it makes money. | local brain |
| digest | Every morning at 8 (hour in rig.json) sends the top 5 signals scored 60+ and yesterday's task results to your own Telegram bot. | free |
| openrig | Bridge. A task addressed to "openrig" is sent to an OpenRig seat with `rig send`; the seat writes its result to E:\ABU\RIG_INBOX\task-N.md and the board closes the task. Off until OpenRig runs in WSL. | your Claude/Codex plan |
| claude / codex / qwen | Seats. Each takes tasks addressed to it (or any free task for claude), runs the CLI headless in E:\ABU, logs the output on the board. | whatever that CLI costs |

Agents report. Nothing is sent, posted, applied to, or bought without you.

## Start (Pegasus)
1. Start your local brain: Qwen on :8011, or Ollama (`ollama serve`, any pulled model), or LM Studio server on :1234. The rig finds the first one alive.
2. `pip install yt-dlp` (only needed for YouTube transcripts).
3. Double-click RUN_RIG.bat. Dashboard opens at http://127.0.0.1:8787.
4. Always-on: double-click INSTALL_AUTOSTART.bat once. It starts at every login.

## Telegram digest (5 minutes)
1. Put your bot token (from @BotFather, e.g. @Qadirsr_bot) in rig.json -> "telegram": {"token": "..."}. Or set the env var TELEGRAM_BOT_TOKEN.
2. Open the bot in Telegram and send it "hi".
3. `python rig.py telegram-setup` saves your chat id. `python rig.py digest-now` sends a test digest.
The digest only goes to you. It never messages anyone else.

## OpenRig bridge (Windows needs WSL)
OpenRig needs tmux and runs on Linux/macOS only; its docs say native Windows is not supported and WSL2 is untested.
An npm install on Windows gives you the `rig` command but seats will not start. Run it inside WSL:
1. PowerShell: `wsl --install -d Ubuntu` (skip if you have WSL).
2. In Ubuntu: install Node 22 (nvm) and tmux, then `npm install -g @openrig/cli` and `claude auth login`.
3. `cd /mnt/e/ABU && rig up first-project-claude --cwd .` then `rig ps --nodes --rig first-project-claude` until seats are ready.
4. In rig.json set "openrig": "enabled": true (seat defaults to dev-owner@first-project-claude). Restart the rig.
5. On the dashboard pick seat "openrig" for a task. `rig tui --shared` in WSL shows the team working; herdr/cmux terminals via `rig terminal open`.

## Use
- Signals column: Make task turns an opportunity into a task for a seat. Keep saves it. Dismiss hides it.
- Tasks column: write a task, pick a seat, Add task. Output and failures show on the card; Retry re-queues.
- CLI: `python rig.py task "text" --to claude` · `python rig.py status` · `python rig.py once signal`

## Change it
Everything is in rig.json: sources, keywords, offers, channels, schedules, seats and their commands.
Add a seat = copy the "codex" block, rename it, set "enabled": true and its command. `{prompt}` is replaced by the task.
No brain running = keyword mode (stricter, cruder). Standard-library Python only.
