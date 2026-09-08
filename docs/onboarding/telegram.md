# Telegram gateway setup

## What it is

The Telegram gateway sends status alerts and opens approval prompts on the founder's Telegram chat. Voice messages are transcribed locally. Approval buttons record decisions without typing.

## Where it lives

Code: `hermes-agent/plugins/platforms/telegram/adapter.py`  
Config: `config.yaml` under `platforms.telegram`

## How to activate

1. Set `TELEGRAM_BOT_TOKEN` to your bot token from BotFather
2. Set `TELEGRAM_ALLOWED_USERS` to your Telegram user ID  
3. `bin/hermes gateway install` starts the polling loop

## How to stop it

`bin/hermes gateway stop` or `killall hermes_cli` to hard-kill.

## Voice transcription

Faster-whisper model (~1.5 MB) is lazy-loaded on first voice note. No API calls, runs locally. Memory usage stays under 1 GB on a 16 GB Mac.

## Approval buttons

When the agent asks for approval, click ✅ Approve or ❌ Deny instead of typing. The word is recorded instantly.
