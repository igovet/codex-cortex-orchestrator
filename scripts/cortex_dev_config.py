"""Explicit graph opt-in for the already isolated development profile."""
import re
import tomllib


def enable_configured_graph(source):
    """Preserve every other setting; do not invent an absent MCP transport."""
    parsed = tomllib.loads(source)
    server = parsed.get('mcp_servers', {}).get('codebase_memory', {})
    if not isinstance(server, dict) or not any(
        isinstance(server.get(key), str) and server[key].strip()
        for key in ('command', 'url')
    ):
        return source
    header = re.search(r'(?m)^\[mcp_servers\.codebase_memory\][ \t]*$', source)
    if header is None:
        return source
    following = re.search(r'(?m)^\[', source[header.end():])
    end = header.end() + following.start() if following else len(source)
    body = source[header.end():end]
    enabled = re.compile(r'(?m)^enabled[ \t]*=[ \t]*(?:true|false)[ \t]*(?:#.*)?$')
    if 'enabled' in server:
        body, count = enabled.subn('enabled = true', body, count=1)
        if not count:
            return source
    else:
        body = '\nenabled = true' + body
    return source[:header.end()] + body + source[end:]
