# Backup Orchestrator Telegram Bot

## Finding a Telegram chat id (for `BOT_ALLOWED_CHAT_IDS`)

`BOT_ALLOWED_CHAT_IDS` is a required, comma-separated list of Telegram chat
ids allowed to talk to this bot at all (see `bot/.env.example`). Every id
in it must be found *before* the bot will start.

### Your own chat id (private/DM)

1. Open a chat with your bot in Telegram and send it any message, e.g. `/start`
   (it's fine if the bot isn't running yet or doesn't reply -- Telegram just
   needs to have *received* the message so it shows up in the update queue).
2. In a browser or via `curl`, call:
   ```
   https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates
   ```
   (substitute your real bot token from `bot/.env`).
3. In the JSON response, find `result[].message.chat.id` -- that integer is
   your private chat id (positive, e.g. `123456789`).

Alternative (no bot token needed): forward any message to a third-party
utility bot such as `@userinfobot` or `@RawDataBot` -- it will reply with
your Telegram user id directly.

### A group's chat id

1. Add your bot to the group.
2. Send any message in the group (mentioning the bot isn't required, but
   makes it easy to spot in the response).
3. Call the same `getUpdates` URL as above.
4. Find the update for that group message and read `result[].message.chat.id`
   -- for groups/supergroups this is a **negative** integer, typically of
   the form `-100xxxxxxxxxx`.

### Using the ids

Combine every id you want to allow into one comma-separated value:

```
BOT_ALLOWED_CHAT_IDS=123456789,-100987654321
```

The bot validates this at startup and refuses to run (crashes immediately
with an explanatory error) if it is missing, empty, or contains anything
that doesn't parse as an integer -- there is no "allow everyone" fallback.

### Notes

- `getUpdates` only returns updates Telegram hasn't already delivered to a
  running bot via polling/webhook and hasn't expired (~24h). If the bot is
  already running and consuming updates, stop it first, or just check your
  bot's own logs (`chat_denied_by_allowlist chat_id=...` -- see below) after
  messaging it once from the chat you want to allow.
- Once `BOT_ALLOWED_CHAT_IDS` is set correctly, a disallowed chat gets
  **no reply at all** from the bot (by design -- this is a security
  allowlist, not a "sorry, access denied" bot). If you're trying to find
  your own chat id and the bot never responds, that's expected until you
  add its id to the allowlist and restart the bot; check the bot process's
  own log output for lines starting with `chat_denied_by_allowlist` to
  confirm which chat id was rejected.
