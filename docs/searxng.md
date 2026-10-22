# Self-hosted SearXNG

A self-hosted [SearXNG](https://docs.searxng.org/) meta-search instance,
started by `start_searxng.bat` / stopped by `stop_searxng.bat`
(`docker compose -f docker/searxng/docker-compose.yml`), serving on
`:8085`. It backs a `rest`-type function on the Home Assistant voice
assistant's Tier-3 freeform LLM brain, giving it real web-search access. No API key, no
per-query cost, and — like the hub's own `:8000` — bound to `0.0.0.0`
for LAN reachability but never meant to be port-forwarded to the public
internet. Not otherwise integrated with this repo's Python/webapp code.
`docker/searxng/config/` (the container-generated `settings.yml`,
including a fresh random secret key) is gitignored — JSON output is off by
default in SearXNG, so `start_searxng.bat` runs
`docker/searxng/ensure_json_format.py` after every start to idempotently
patch `search.formats` on the generated file (never touching `secret_key`),
restarting the container only the one time it actually changes something.

