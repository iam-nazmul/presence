"""Web pack. Exa where a key exists, DuckDuckGo HTML otherwise.

Everything fetched from the open web comes back wrapped in <untrusted> and the
system prompt tells the model that such content is data, never instructions.
The real defence is the policy gate -- scopes are fixed before the model runs --
but the wrapper costs one line.
"""

from __future__ import annotations

import html
import re

import httpx

from presence.config import settings
from presence.tools.registry import ToolContext, tool

_UA = {"User-Agent": "Mozilla/5.0 (compatible; PresenceAgent/0.1)"}


def _untrusted(source: str, body: str) -> str:
    return f'<untrusted source="{source}">\n{body}\n</untrusted>'


@tool(risk="external", scopes={"web"})
async def web_search(ctx: ToolContext, query: str, n: int = 5) -> str:
    """Search the web and return titles, URLs and short snippets.

    Use this for anything you are not certain about, anything recent, and
    anything the user asks you to look up. Follow with web_fetch when a result
    looks worth reading in full.
    """
    query = (query or "").strip()
    if not query:
        return "No query given."
    n = max(1, min(int(n or 5), 8))

    if settings.exa_api_key:
        try:
            async with httpx.AsyncClient(timeout=25) as c:
                r = await c.post(
                    "https://api.exa.ai/search",
                    headers={"x-api-key": settings.exa_api_key},
                    json={"query": query, "numResults": n,
                          "contents": {"text": {"maxCharacters": 300}}},
                )
                r.raise_for_status()
                results = r.json().get("results", [])
            if results:
                lines = [
                    f"{i}. {x.get('title', 'Untitled')}\n   {x.get('url', '')}\n"
                    f"   {(x.get('text') or '').strip()[:300]}"
                    for i, x in enumerate(results[:n], 1)
                ]
                return _untrusted("exa:" + query, "\n".join(lines))
        except Exception:  # fall through to the free backend
            pass

    try:
        async with httpx.AsyncClient(timeout=25, headers=_UA, follow_redirects=True) as c:
            r = await c.post("https://html.duckduckgo.com/html/", data={"q": query})
            r.raise_for_status()
        blocks = re.findall(
            r'result__a"[^>]*href="(.*?)".*?>(.*?)</a>.*?result__snippet[^>]*>(.*?)</a>',
            r.text, re.S,
        )
        if not blocks:
            return f"No results for '{query}'."
        lines = []

        def clean(s: str) -> str:
            return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()

        for i, (url, title, snip) in enumerate(blocks[:n], 1):
            lines.append(f"{i}. {clean(title)}\n   {html.unescape(url)}\n   {clean(snip)[:300]}")
        return _untrusted("search:" + query, "\n".join(lines))
    except Exception as e:
        return (f"Search failed: {type(e).__name__}. Try a different query, or "
                f"answer from what you already know.")


@tool(risk="external", scopes={"web"})
async def web_fetch(ctx: ToolContext, url: str) -> str:
    """Fetch one web page and return its readable text.

    Output is capped at roughly 6000 characters. Use it after web_search when a
    result looks worth reading properly.
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "That is not a fetchable http(s) URL."
    try:
        async with httpx.AsyncClient(timeout=30, headers=_UA, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            body = r.text
    except Exception as e:
        return f"Could not fetch {url}: {type(e).__name__}."

    body = re.sub(r"(?is)<(script|style|nav|footer|header|svg)[^>]*>.*?</\1>", " ", body)
    try:
        from markdownify import markdownify

        text = markdownify(body, heading_style="ATX")
    except Exception:
        text = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()
    if not text:
        return f"{url} returned no readable text."
    clipped = text[:6000]
    if len(text) > 6000:
        clipped += f"\n\n…{len(text) - 6000} more characters omitted."
    return _untrusted(url, clipped)
