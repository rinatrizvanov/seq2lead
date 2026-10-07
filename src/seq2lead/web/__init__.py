"""A local browser interface over the standalone inference API.

Deliberately built on the standard library's `http.server` rather than a web
framework. The project gains no new dependency for a single-user local tool, and
the thing being demonstrated is the inference API, not a server stack.

**Local only.** The server binds to 127.0.0.1 and refuses any other address. It
has no authentication, no TLS and no rate limiting, because it is not a hosted
service and must not be run as one.
"""

from seq2lead.web.jobs import JobQueue, JobState
from seq2lead.web.server import DEFAULT_PORT, serve

__all__ = ["DEFAULT_PORT", "JobQueue", "JobState", "serve"]
