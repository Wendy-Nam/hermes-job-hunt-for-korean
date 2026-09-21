"""Crawl4AI web extract provider — user plugin, no API key, local browsers.

Runs the volume-local crawl4ai venv (CRAWL_PY) as a subprocess because the
gateway venv has no crawl4ai/playwright. Serialized (one browser run at a
time) to protect the 2vCPU box; per-URL failures use the legacy error shape
so the dispatcher never chokes.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

CRAWL_PY = Path(os.environ.get("HERMES_CRAWL4AI_PYTHON", "/opt/data/crawl4ai-env/bin/python"))
# NOTE: gateway env already sets PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/.playwright
# (image default, browsers NOT there). Prefer the volume path when present.
_PW_DEFAULT = "/opt/data/pw-browsers"
PW_BROWSERS = _PW_DEFAULT if Path(_PW_DEFAULT).is_dir() else os.environ.get(
    "PLAYWRIGHT_BROWSERS_PATH", _PW_DEFAULT)
_TIMEOUT = int(os.environ.get("HERMES_CRAWL4AI_TIMEOUT", "150"))
_MAX_CHARS = int(os.environ.get("HERMES_CRAWL4AI_MAX_CHARS", "12000"))

# One browser run at a time — 2vCPU box shared with the gateway.
_LOCK = threading.Lock()

_INLINE = "\n".join([
    "import sys,json,asyncio",
    "from crawl4ai import AsyncWebCrawler,BrowserConfig,CrawlerRunConfig",
    "async def go(url):",
    "    async with AsyncWebCrawler(config=BrowserConfig(headless=True,verbose=False)) as c:",
    "        r=await c.arun(url,config=CrawlerRunConfig(wait_until='domcontentloaded',"
    "            delay_before_return_html=2.0,page_timeout=60000))",
    "        md=getattr(r,'markdown',None)",
    "        md=md.raw_markdown if hasattr(md,'raw_markdown') else (md or '')",
    "        mt=getattr(r,'metadata',None) or {}",
    "        print(json.dumps({'success':bool(getattr(r,'success',False)),"
    "            'title':mt.get('title','') if isinstance(mt,dict) else '',"
    "            'content':md or '',"
    "            'error':getattr(r,'error_message','') or ''}))",
    "asyncio.run(go(sys.argv[1]))",
])


def _fetch_one(url: str, timeout: int) -> Dict[str, Any]:
    env = dict(os.environ)
    env["PLAYWRIGHT_BROWSERS_PATH"] = PW_BROWSERS
    try:
        r = subprocess.run(
            [str(CRAWL_PY), "-c", _INLINE, url],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"url": url, "title": "", "content": "",
                "raw_content": "", "metadata": {}, "error": "crawl4ai timeout"}
    except Exception as exc:
        return {"url": url, "title": "", "content": "",
                "raw_content": "", "metadata": {}, "error": str(exc)[:200]}
    if r.returncode != 0 or not r.stdout.strip():
        err = (r.stderr or "")[-200:] or "crawl4ai rc=%d" % r.returncode
        return {"url": url, "title": "", "content": "",
                "raw_content": "", "metadata": {}, "error": err}
    try:
        d = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"url": url, "title": "", "content": "",
                "raw_content": "", "metadata": {}, "error": "crawl4ai bad json"}
    if not d.get("success"):
        return {"url": url, "title": "", "content": "",
                "raw_content": "", "metadata": {},
                "error": str(d.get("error") or "crawl failed")[:200]}
    return {"url": url, "title": d.get("title", ""),
            "content": d.get("content", ""), "raw_content": "",
            "metadata": {"backend": "crawl4ai"}}


class Crawl4AIWebExtractProvider(WebSearchProvider):
    """Local browser extract backend. Search is NOT supported (ddgs stays)."""

    @property
    def name(self) -> str:
        return "crawl4ai"

    @property
    def display_name(self) -> str:
        return "Crawl4AI (local)"

    def is_available(self) -> bool:
        return CRAWL_PY.is_file() and os.access(str(CRAWL_PY), os.X_OK)

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return True

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        max_chars = int(kwargs.get("max_chars") or _MAX_CHARS)
        timeout = int(kwargs.get("timeout") or _TIMEOUT)
        out: List[Dict[str, Any]] = []
        for url in urls or []:
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                out.append({"url": str(url), "title": "", "content": "",
                            "raw_content": "", "metadata": {}, "error": "bad url"})
                continue
            with _LOCK:
                row = _fetch_one(url, timeout)
            if max_chars > 0 and len(row.get("content") or "") > max_chars:
                row["content"] = row["content"][:max_chars]
            out.append(row)
        return out


def register(ctx) -> None:
    ctx.register_web_search_provider(Crawl4AIWebExtractProvider())
    logger.info("web-crawl4ai registered (local extract backend)")
