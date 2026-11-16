# Keeping the e2e run off the loopback port pool

Every TCP connection a browser closes leaves its (ephemeral port, server port) pair in TIME_WAIT for minutes, and on Windows a `connect()` that lands on such a pair fails as `net::ERR_ADDRESS_IN_USE` on whichever page loads next, unrelated to the diff. Three things keep the suite from adding to that. None touches the machine's port range or `TcpTimedWaitDelay`.

- **Shared browser contexts.** `fresh_page` (`tests/e2e/_flow.py`) opens each phase's page in one session-long context per set of context arguments (viewport, touch), instead of a new context per phase. A new context starts with an empty HTTP cache and closes its connection pool on teardown. The phase's page is closed afterwards and the context is reset: the page's local and session storage, then the context's cookies and permissions. Routes, init scripts and listeners belong to the page and go with it. `page.unroute_all` is not used because it blocks on a handler that still holds a request. A test that needs state beyond that (a service worker, a context-level route) builds its own context and closes it. `conftest.py` leaves the per-test `page` fixture unrequested unless a test asks for it, since an autouse `page` opens a context per test.
- **Keep-alive health polling.** The hub readiness check polls through one `httpx.Client`, so it reuses a connection.
- **A named failure.** A failure whose text carries `ERR_ADDRESS_IN_USE` leads with `PORT POOL EXHAUSTED`, and the run ends with a section of that name listing the affected tests (`tests/e2e/_port_pool.py`). A gate reader sees a host-capacity problem, not a page defect: wait a few minutes and re-run.

The scaffold's serve-static-files-from-disk route is not used: this suite tests its own live hub, so the requests are the thing under test.

## What is left

The admin SPA holds two long-lived streams open per page (`/admin/api/hub/requests/stream` and `/admin/api/hub/log/tail`). Closing a page or navigating aborts both, and each abort closes a socket, so a page load costs a couple of TIME_WAIT pairs however the harness is arranged. A context-per-phase run costs those plus the whole connection pool of the context.

## Measuring

Sample `netstat -ano -p tcp` every 200 ms during a full run and count the distinct new loopback TIME_WAIT pairs whose server port is the disposable hub's. The hub port is the one the run's `_isolated_hub` listens on. The host's other apps add their own pairs on their own ports, so count only the hub's. Measured over one full run: about 400 pairs with a context per phase, about 300 with shared contexts.
