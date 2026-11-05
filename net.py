"""HTTPS settings shared by the DeepL client and the dictionary updater."""

from __future__ import annotations

import ssl


def https_context() -> ssl.SSLContext:
    """Trust the Windows store plus certifi's roots.

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
