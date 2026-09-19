import time

from datetime import datetime as dt
from datetime import timezone

from flask import request

from taky.dps import app, requires_auth, api_response
from taky.util import mgmt


@app.route("/")
def hello():
    """A healthcheck endpoint"""
    return "Hello, world!"


def _client_endpoints():
    """
    Fetch the list of connected clients from the COT server's management
    socket, mapped to Marti ClientEndpoint objects
    """
    stat = mgmt.status()
    if not stat:
        return []

    try:
        sec_ago = int(request.args.get("secAgo", 0))
    except (TypeError, ValueError):
        sec_ago = 0

    cutoff = time.time() - sec_ago if sec_ago > 0 else 0

    endpoints = []
    seen_uids = set()
    for client in stat.get("clients", []):
        if client.get("anonymous"):
            continue

        last_rx = client.get("last_rx", 0)
        if cutoff and last_rx < cutoff:
            continue

        uid = client.get("uid")
        if uid in seen_uids:
            continue
        seen_uids.add(uid)

        endpoints.append(
            {
                "callsign": client.get("callsign"),
                "uid": uid,
                "username": client.get("username") or client.get("callsign"),
                "team": client.get("team"),
                "role": client.get("role"),
                "lastEventTime": dt.fromtimestamp(last_rx, timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "lastStatus": "Connected",
            }
        )

    return endpoints


@app.route("/Marti/api/clientEndPoints")
@requires_auth
def client_end_points():
    """
    Return a list of connected clients
    """
    return api_response(
        data=_client_endpoints(), rtype="com.bbn.marti.remote.ClientEndpoint"
    )
