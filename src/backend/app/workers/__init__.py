"""Worker lane entrypoints.

The Render worker service is one Linux container, but it runs several small
lane processes. Each lane owns one kind of durable work and communicates via
Postgres/Redis instead of calling other lanes inline.
"""

