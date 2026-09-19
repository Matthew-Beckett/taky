from flask import Response

from taky import __version__
from taky.dps import app, requires_auth, api_response


@app.route("/Marti/api/version")
@requires_auth
def version():
    """
    Returns a plain text string of the server's version
    """
    return Response(f"taky-{__version__}", mimetype="text/plain")


@app.route("/Marti/api/node/id")
@requires_auth
def node_id():
    """
    Returns a plain text string of the server's node ID
    """
    return Response(app.config["NODEID"], mimetype="text/plain")


@app.route("/Marti/api/version/info")
@requires_auth
def version_info():
    """
    Returns the server's version, expressed as structured JSON
    """
    parts = __version__.split(".")
    try:
        ret = {
            "major": int(parts[0]),
            "minor": int(parts[1]) if len(parts) > 1 else 0,
            "patch": (
                int("".join(c for c in parts[2] if c.isdigit()) or 0)
                if len(parts) > 2
                else 0
            ),
            "branch": "taky",
            "variant": "DIRECT",
        }
    except (IndexError, ValueError):
        ret = {
            "major": 0,
            "minor": 0,
            "patch": 0,
            "branch": "taky",
            "variant": "DIRECT",
        }

    return ret


@app.route("/Marti/api/version/config")
@requires_auth
def version_config():
    """
    Return the server's version, and other metadata
    """
    data = {
        "version": f"taky-{__version__}",
        "api": "3",
        "hostname": app.config["HOSTNAME"],
    }

    return api_response(data=data, rtype="ServerConfig")
