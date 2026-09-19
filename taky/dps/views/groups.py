from flask import request

from taky.dps import app, requires_auth, api_response
from taky.util import mgmt


@app.route("/Marti/api/groups/groupCacheEnabled")
@requires_auth
def group_cache_enabled():
    """Report whether group caching is enabled"""
    return api_response(data=False, rtype="java.lang.Boolean")


@app.route("/Marti/api/groups/all")
@requires_auth
def groups_all():
    """Return the caller's groups -- taky has no Marti group system"""
    return api_response(data=[], rtype="com.bbn.marti.remote.groups.Group")


@app.route("/Marti/api/groups")
@requires_auth
def groups_filtered():
    """Return LDAP groups matching groupNameFilter"""
    return api_response(data=[], rtype="com.bbn.marti.remote.groups.LdapGroup")


@app.route("/Marti/api/groups/members")
@requires_auth
def groups_members():
    """Return the number of members of a group"""
    return api_response(data=0, rtype="java.lang.Integer")


@app.route("/Marti/api/groupprefix")
@requires_auth
def group_prefix():
    """Return the configured LDAP group prefix"""
    return api_response(data="", rtype="java.lang.String")


@app.route("/Marti/api/groups/user")
@requires_auth
def groups_user():
    """Return the groups for a user"""
    return api_response(data=[], rtype="com.bbn.marti.remote.groups.Group")


@app.route("/Marti/api/groups/update/<username>")
@requires_auth
def groups_update(username):
    """Group update notification endpoint"""
    # pylint: disable=unused-argument
    return api_response(data=True, messages=[""])


@app.route("/Marti/api/groups/<group_name>/<direction>")
@requires_auth
def groups_get(group_name, direction):
    """Return a specific group -- always 404, taky has no Marti groups"""
    # pylint: disable=unused-argument
    return api_response(data={}, rtype="com.bbn.marti.remote.groups.Group"), 404


@app.route("/Marti/api/groups/activebits", methods=["PUT"])
@app.route("/Marti/api/groups/active", methods=["PUT"])
@requires_auth
def groups_active():
    """Set the caller's active groups -- accepted and ignored"""
    return "", 200


@app.route("/Marti/api/groups/activeForce", methods=["GET", "PUT"])
@requires_auth
def groups_active_force():
    """Force the given user's groups active"""
    if not request.args.get("username"):
        return "", 400

    return {
        "name": "",
        "distinguishedName": "",
        "direction": "",
        "created": "",
        "bitpos": 0,
        "active": True,
        "description": "",
        "type": "SYSTEM",
    }


@app.route("/Marti/api/users/all")
@requires_auth
def users_all():
    """Return all users -- taky has no user database"""
    return api_response(data=[], rtype="com.bbn.marti.remote.groups.User")


@app.route("/Marti/api/subscriptions/all")
@requires_auth
def subscriptions_all():
    """
    Return the connected clients as subscription records
    """
    stat = mgmt.status()
    if not stat:
        return api_response(data=[], rtype="SubscriptionInfo", messages=[])

    data = []
    for client in stat.get("clients", []):
        if client.get("anonymous"):
            continue

        data.append(
            {
                "clientUid": client.get("uid"),
                "callsign": client.get("callsign"),
                "username": client.get("username") or client.get("callsign"),
                "ipAddress": client.get("ip"),
                "port": str(client.get("port", "")),
                "protocol": "SSL" if client.get("protocol") == "ssl" else "TCP",
                "team": client.get("team"),
                "role": client.get("role"),
                "takv": client.get("takv"),
            }
        )

    return api_response(data=data, rtype="SubscriptionInfo", messages=[])
