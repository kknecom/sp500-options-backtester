"""
Shared moomoo (Futu) OpenAPI client bootstrap.

Unlike Tiger, moomoo's OpenAPI never talks to moomoo's servers directly
from this process -- it connects to OpenD, a separate gateway program
that must already be running and logged in to your moomoo account
(desktop app or headless, see https://www.moomoo.com/download/OpenAPI).
This module only opens a local socket to that already-authenticated
gateway; it never handles, prompts for, or sees your moomoo login
credentials.

Confirmed working (2026-08-31, live account): OpenD reachable at
127.0.0.1:11111, US options quote permission active with no permission
errors -- get_option_chain/get_market_snapshot return real bid/ask and
full greeks. SPX index options use the code 'US..SPX' (double dot --
indices are MARKET..CODE, unlike stock-rooted options like 'US.AAPL').
Individual contracts come back as e.g. 'US.SPX261016C200000'.

SETUP
-----
Start OpenD yourself before running anything that imports this module.
Defaults assume OpenD running on this same machine on its default port;
override via MOOMOO_OPEND_HOST / MOOMOO_OPEND_PORT env vars if it runs
elsewhere (e.g. a cloud box, for unattended daily collection).
"""
from __future__ import annotations
import os

import moomoo as ft

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11111


def get_quote_context() -> ft.OpenQuoteContext:
    """
    Authenticated OpenQuoteContext, ready for get_option_chain/
    get_option_expiration_date/get_market_snapshot calls. Confirms OpenD
    is actually reachable and logged in to a quote-capable session
    (raises RuntimeError otherwise) rather than handing back a context
    that will only fail later on the first real call.

    Supports the context-manager protocol (`with get_quote_context() as
    ctx:`), which closes the underlying socket on exit -- prefer that
    over holding a context open indefinitely.
    """
    host = os.environ.get("MOOMOO_OPEND_HOST", DEFAULT_HOST)
    port = int(os.environ.get("MOOMOO_OPEND_PORT", DEFAULT_PORT))
    ctx = ft.OpenQuoteContext(host=host, port=port)

    ret, state = ctx.get_global_state()
    if ret != ft.RET_OK:
        ctx.close()
        raise RuntimeError(
            f"Could not reach OpenD at {host}:{port} -- is it running? ({state})"
        )
    if not state.get("qot_logined"):
        ctx.close()
        raise RuntimeError(
            f"OpenD at {host}:{port} is reachable but not logged in to a quote "
            f"session -- log in via the OpenD gateway (or moomoo desktop app) first."
        )
    return ctx
