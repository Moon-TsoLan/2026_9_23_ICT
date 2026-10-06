"""Shared HTTP connection pools.

`httpx.Client` is documented as safe to share between threads, and reusing one keeps concurrent
calls from paying a TLS handshake each. Timeouts stay per request on purpose: the parse service
wants a much longer one than the chat endpoint.

Nothing here decides anything about a document; it only owns the sockets.
"""

from __future__ import annotations

import threading

import httpx

_lock = threading.Lock()
_clients: dict[str, httpx.Client] = {}

# name -> (max_connections, max_keepalive_connections)
_LIMITS = {
    "llm": (32, 16),      # many small requests, concurrency bounded by ICT_LLM_MAX_CONCURRENCY
    "parse": (8, 4),      # few large uploads, concurrency bounded by ICT_PARSE_MAX_INFLIGHT
}


def shared_client(name: str) -> httpx.Client:
    """The process-wide client for one endpoint role ('llm' or 'parse')."""
    client = _clients.get(name)
    if client is not None:
        return client
    with _lock:
        client = _clients.get(name)
        if client is None:
            connections, keepalive = _LIMITS.get(name, _LIMITS["llm"])
            client = httpx.Client(limits=httpx.Limits(max_connections=connections,
                                                      max_keepalive_connections=keepalive))
            _clients[name] = client
    return client
