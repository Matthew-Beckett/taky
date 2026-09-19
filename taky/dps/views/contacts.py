from flask import request

from taky.dps import app, requires_auth
from taky.util import mgmt


def _contacts():
    """
    Return a list of RemoteSubscriptionLite objects for connected clients
    """
    stat = mgmt.status()
    if not stat:
        return []

    contacts = []
    for client in stat.get("clients", []):
        if client.get("anonymous"):
            continue

        contacts.append(
            {
                "filterGroups": [],
                "notes": "",
                "callsign": client.get("callsign"),
                "team": client.get("team"),
                "role": client.get("role"),
                "takv": client.get("takv"),
                "uid": client.get("uid"),
            }
        )

    return contacts


@app.route("/Marti/api/contacts/all")
@requires_auth
def contacts_all():
    """
    Return a bare array of contacts for all connected clients
    """
    contacts = _contacts()
    if request.args.get("direction", "ASCENDING") == "DESCENDING":
        contacts.reverse()

    return contacts


@app.route("/Marti/api/contacts/all/lite")
@app.route("/Marti/api/contacts/all/full")
@requires_auth
def contacts_all_detail():
    """
    Taky has no user database -- the lite and full variants return the same
    subscription information as /Marti/api/contacts/all
    """
    return _contacts()
