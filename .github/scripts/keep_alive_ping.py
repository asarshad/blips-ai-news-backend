#!/usr/bin/env python3
"""
Keep-alive pinger for Render services.

Helps prevent cold starts by periodically hitting the health endpoint.

Environment variables:
    HEALTH_URL: The URL to ping (required)
    INTERVAL_SECONDS: Time between pings (default: 60)
    DURATION_SECONDS: How long to run (default: 60, for single run use 0)
    TIMEOUT_SECONDS: Request timeout (default: 10)

Usage:
    # Single ping
    HEALTH_URL=https://your-app.onrender.com/health python keep_alive_ping.py
    
    # Multiple pings for 5 minutes
    HEALTH_URL=... DURATION_SECONDS=300 INTERVAL_SECONDS=60 python keep_alive_ping.py
"""

import os
import sys
import time
from datetime import datetime
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    # Fallback to urllib for minimal dependencies
    import urllib.request
    import urllib.error
    
    class MinimalRequests:
        """Minimal requests-like interface using urllib."""
        
        @staticmethod
        def get(url: str, timeout: int = 10):
            """Make a GET request."""
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "blips-keepalive/1.0"})
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    return type("Response", (), {
                        "status_code": response.status,
                        "text": response.read().decode(),
                        "ok": 200 <= response.status < 300,
                    })()
            except urllib.error.HTTPError as e:
                return type("Response", (), {
                    "status_code": e.code,
                    "text": str(e.reason),
                    "ok": False,
                })()
            except urllib.error.URLError as e:
                raise ConnectionError(str(e.reason))
    
    requests = MinimalRequests()


def log(message: str) -> None:
    """Print timestamped log message."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{timestamp}] {message}", flush=True)


def sanitize_url_for_log(url: str) -> str:
    """Remove query params from URL for safe logging."""
    parsed = urlparse(url)
    safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return safe_url


def ping(url: str, timeout: int = 10) -> bool:
    """
    Ping the health endpoint.
    
    Returns:
        True if the ping was successful (2xx), False otherwise.
    """
    safe_url = sanitize_url_for_log(url)
    
    try:
        start = time.time()
        response = requests.get(url, timeout=timeout)
        elapsed_ms = (time.time() - start) * 1000
        
        if response.ok:
            log(f"OK {safe_url} -> {response.status_code} ({elapsed_ms:.0f}ms)")
            return True
        else:
            log(f"WARN {safe_url} -> {response.status_code} ({elapsed_ms:.0f}ms)")
            return False
            
    except Exception as e:
        log(f"ERROR {safe_url} -> {type(e).__name__}: {str(e)[:100]}")
        return False


def main() -> int:
    """Main entry point."""
    health_url = os.environ.get("HEALTH_URL")
    
    if not health_url:
        print("ERROR: HEALTH_URL environment variable is required", file=sys.stderr)
        return 1
    
    interval = int(os.environ.get("INTERVAL_SECONDS", "60"))
    duration = int(os.environ.get("DURATION_SECONDS", "60"))
    timeout = int(os.environ.get("TIMEOUT_SECONDS", "10"))
    
    safe_url = sanitize_url_for_log(health_url)
    log(f"Starting keep-alive for {safe_url}")
    log(f"Interval: {interval}s, Duration: {duration}s, Timeout: {timeout}s")
    
    # Single ping mode
    if duration <= 0:
        success = ping(health_url, timeout)
        return 0 if success else 1
    
    # Multi-ping mode
    start_time = time.time()
    end_time = start_time + duration
    success_count = 0
    fail_count = 0
    
    while time.time() < end_time:
        if ping(health_url, timeout):
            success_count += 1
        else:
            fail_count += 1
        
        # Sleep until next interval (or exit if past duration)
        time_remaining = end_time - time.time()
        if time_remaining > 0:
            sleep_time = min(interval, time_remaining)
            time.sleep(sleep_time)
    
    log(f"Finished: {success_count} successful, {fail_count} failed")
    
    # Return error if all pings failed
    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
