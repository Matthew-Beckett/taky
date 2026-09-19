"""
A simple client for querying the taky management socket from the datapackage
server (which runs in a separate process from the COT server).
"""

import os
import json
import socket
import logging

from taky.config import app_config

lgr = logging.getLogger(__name__)


def mgmt_socket_path():
    """
    The path to the management socket
    """
    return os.path.join(app_config.get("taky", "root_dir"), "taky-mgmt.sock")


def query(payload, timeout=5):
    """
    Send a command (as a dict) to the management socket, and return the
    decoded JSON response. Returns None if the socket is unavailable, or
    the response cannot be understood.
    """
    msg = json.dumps(payload).encode() + b"\0"

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(mgmt_socket_path())
        sock.settimeout(timeout)
        sock.sendall(msg)

        data = b""
        while True:
            try:
                rx = sock.recv(65536)
            except socket.timeout:
                lgr.debug("Timeout waiting for management response")
                return None

            if len(rx) == 0:
                break
            data += rx
            if b"\0" in data:
                break

        idx = data.index(b"\0")
        return json.loads(data[:idx].decode())
    except (OSError, ValueError) as exc:
        lgr.debug("Management query failed: %s", exc)
        return None
    finally:
        sock.close()


def status():
    """
    Query the COT server for its status, including connected clients
    """
    return query({"cmd": "status"})


def cot_get(uid):
    """
    Return a list of event XML strings tracked for the given UID
    """
    ret = query({"cmd": "cot_get", "uid": uid})
    if ret is None:
        return None

    return ret.get("events", [])


def cot_all():
    """
    Return a list of event XML strings for all tracked events
    """
    ret = query({"cmd": "cot_all"})
    if ret is None:
        return None

    return ret.get("events", [])


def broadcast(xml):
    """
    Ask the COT server to route an event (as an XML string)
    """
    return query({"cmd": "broadcast", "xml": xml})
