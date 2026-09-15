"""CORE web subsystem.

Fetch → Extract → Search → Crawl → Inspect → Render → Compare.

Evidence levels are kept distinct, per the operating contract:
1. fetched HTML
2. extracted readable content
3. rendered DOM
4. screenshot
"""

from core.web.context import WebContext
from core.web.tools import register_web_tools

__all__ = ["WebContext", "register_web_tools"]
