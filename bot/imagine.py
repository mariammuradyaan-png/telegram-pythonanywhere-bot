import urllib.parse

from bot.config import (
    IMAGINE_BASE_URL,
    IMAGINE_HEIGHT,
    IMAGINE_MODEL,
    IMAGINE_STYLE,
    IMAGINE_WIDTH,
)


def build_image_url(description: str, seed: int | None = None) -> str:
    """Build a Pollinations text-to-image URL for a user's description.

    The romantic house style (IMAGINE_STYLE) is appended to every prompt so
    output stays on-brand for Mariam regardless of what the user asks for.
    The whole prompt is percent-encoded into the path segment; sizing/model
    knobs go in the query string. An optional `seed` (we pass the Telegram
    message_id) varies the result run-to-run — without it Pollinations
    returns the same image for the same prompt because it caches by URL.

    The bot never fetches this URL itself: it hands it to Telegram's
    sendPhoto, and Telegram's servers do the download. That is why this
    works on PythonAnywhere's free tier despite the outbound whitelist.
    """
    prompt = f"{description.strip()}, {IMAGINE_STYLE}"
    encoded_prompt = urllib.parse.quote(prompt, safe="")
    params = {
        "width": IMAGINE_WIDTH,
        "height": IMAGINE_HEIGHT,
        "model": IMAGINE_MODEL,
        "nologo": "true",
    }
    if seed is not None:
        params["seed"] = seed
    query = urllib.parse.urlencode(params)
    return f"{IMAGINE_BASE_URL}/{encoded_prompt}?{query}"
