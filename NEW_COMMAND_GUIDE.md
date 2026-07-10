# How to Add a New Command to the Telegram Bot

## Overview
Adding a new command follows the same pattern as existing commands in the bot. Commands are defined using the `@bot.message_handler(commands=["command_name"])` decorator.

## Steps to Add a New Command

### 1. Choose a Command Name
Select a unique command name (without the `/` prefix). For example: `hello`, `ping`, `stats`, etc.

### 2. Add the Command Handler
Add a new function to `bot/handlers.py` following the existing pattern:

```python
@bot.message_handler(commands=["your_command"], func=is_allowed)
def cmd_your_command(message):
    # Your command logic here
    bot.send_message(message.chat.id, "Response to your command")
```

### 3. Update Help Command (Optional)
Add your command to the `/help` command listing in `bot/handlers.py`:

```python
@bot.message_handler(commands=["help"], func=is_allowed)
def cmd_help(message):
    lines = [
        "/start — welcome message",
        "/help  — show this message",
        "/reset — clear conversation history",
        "/about — about this bot",
        "/your_command — description of your command",  # Add this line
    ]
    if ARMGPT_BASE_URL:
        lines.append("/model — switch AI provider")
    bot.send_message(message.chat.id, "\n".join(lines))
```

## Complete Example: Adding a "ping" Command

Here's a complete example of adding a ping command that responds with the bot's uptime:

```python
import time
from datetime import datetime
from bot.clients import bot
from bot.config import COMMIT_SHA
from bot.helpers import is_allowed

# Store bot start time
BOT_START_TIME = time.time()

@bot.message_handler(commands=["ping"], func=is_allowed)
def cmd_ping(message):
    uptime_seconds = time.time() - BOT_START_TIME
    uptime_hours = uptime_seconds / 3600
    bot.send_message(
        message.chat.id,
        f"Pong! 🏓\n\n"
        f"Uptime: {uptime_hours:.1f} hours\n"
        f"Version: {COMMIT_SHA if COMMIT_SHA else 'unknown'}"
    )

@bot.message_handler(commands=["help"], func=is_allowed)
def cmd_help(message):
    lines = [
        "/start — welcome message",
        "/help  — show this message",
        "/reset — clear conversation history",
        "/about — about this bot",
        "/ping — check bot status",
    ]
    if ARMGPT_BASE_URL:
        lines.append("/model — switch AI provider")
    bot.send_message(message.chat.id, "\n".join(lines))
```

## Advanced Example: Command with Parameters

Here's an example of a command that accepts parameters:

```python
@bot.message_handler(commands=["echo"], func=is_allowed)
def cmd_echo(message):
    # Extract command arguments
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 1:
        bot.send_message(message.chat.id, "Usage: /echo <text to echo>")
        return
    
    text_to_echo = parts[1].strip()
    if not text_to_echo:
        bot.send_message(message.chat.id, "Please provide text to echo")
        return
        
    bot.send_message(message.chat.id, f"Echo: {text_to_echo}")
```

## Key Points to Remember

1. **Always use `func=is_allowed`** - This ensures proper user whitelisting
2. **Follow the existing pattern** - Use the same structure as other commands
3. **Handle edge cases** - Validate inputs and provide helpful error messages
4. **Keep responses concise** - Telegram has a 4096 character limit per message
5. **Use helper functions** - Leverage existing functions like `send_reply()` for better formatting
6. **Update help text** - Keep the `/help` command up-to-date with new commands
7. **Consider permissions** - Commands should respect the ALLOWED_USERS whitelist

## Testing Your Command

1. Run the bot locally with `python run_local.py`
2. Send your new command to the bot in Telegram
3. Verify it behaves as expected
4. Test edge cases (empty input, invalid parameters, etc.)
5. Deploy to PythonAnywhere to test in production