"""HTTPS settings shared by the DeepL client and the dictionary updater."""

from __future__ import annotations

from functools import lru_cache
import ssl


@lru_cache(maxsize=1)
def https_context() -> ssl.SSLContext:
    """Trust the Windows store plus certifi's roots. Built once, because loading them takes most of a second.

    Windows fetches root certificates on demand, and Python cannot trigger that, so a PC
    missing a root would otherwise fail verification. This only adds trusted roots;
    certificate and host name checks stay on.
    """
    context = ssl.create_default_context()
    try:
        import certifi
        context.load_verify_locations(certifi.where())
    except (ImportError, OSError, ssl.SSLError):
        pass
    return context
