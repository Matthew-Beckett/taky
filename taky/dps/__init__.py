import os
import functools

import flask
from flask import Flask

from taky.config import load_config, app_config

application = app = Flask(__name__)


MARTI_API_VERSION = "3"


def api_response(data=None, rtype=None, messages=None):
    """
    Build a Marti ApiResponse envelope

    {"version": "3", "type": <type>, "data": <data>, "nodeId": <node id>}
    """
    ret = {
        "version": MARTI_API_VERSION,
        "type": rtype,
        "data": data,
        "nodeId": app.config["NODEID"],
    }
    if messages is not None:
        ret["messages"] = messages

    return ret


def requires_auth(func):
    """
    Function to ensure that a valid client certificate is submitted
    """

    @functools.wraps(func)
    def check_headers(*args, **kwargs):
        # X-USER is injected by the gunicorn worker from the client
        # certificate. When SSL is disabled, clients cannot authenticate at
        # all -- fall back to anonymous rather than disabling the API.
        if not flask.request.headers.get("X-USER") and app.config.get(
            "SSL_ENABLED", True
        ):
            flask.abort(401)
        if flask.request.headers.get("X-REVOKED"):
            flask.abort(403)

        return func(*args, **kwargs)

    return check_headers


def configure_app(config):
    app.config["HOSTNAME"] = config.get("taky", "hostname")
    app.config["NODEID"] = config.get("taky", "node_id")
    app.config["UPLOAD_PATH"] = config.get("dp_server", "upload_path")
    app.config["SSL_ENABLED"] = config.getboolean("ssl", "enabled")

    cot_port = config.getint("cot_server", "port")
    if config.getboolean("ssl", "enabled"):
        app.config["COT_CONN_STR"] = f'ssl:{app.config["HOSTNAME"]}:{cot_port}'
    else:
        app.config["COT_CONN_STR"] = f'tcp:{app.config["HOSTNAME"]}:{cot_port}'


try:
    load_config(os.environ.get("TAKY_CONFIG"))
except FileNotFoundError:
    pass
configure_app(app_config)

from taky.dps import views  # pylint: disable=wrong-import-position
