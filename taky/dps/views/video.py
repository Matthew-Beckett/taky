import os

import uuid

from lxml import etree
from flask import request

from taky.dps import app, requires_auth


def video_feed_dir():
    """The directory containing the video feed XML files"""
    return os.path.join(app.config["UPLOAD_PATH"], "video_feed")


def iter_feeds():
    """
    Yield (uid, feed element) tuples for all stored video feeds
    """
    feed_dir = video_feed_dir()
    if not os.path.isdir(feed_dir):
        return

    for feed in os.listdir(feed_dir):
        try:
            elm = etree.parse(os.path.join(feed_dir, feed)).getroot()
        except etree.XMLSyntaxError:
            continue

        if elm.tag != "feed":
            continue

        yield (feed, elm)


def sanitize_feed(elm):
    """
    Strip userinfo credentials from a feed's address element
    """
    addr = elm.find("address")
    if addr is None or not addr.text or "@" not in addr.text:
        return

    # proto://user:pass@host/path -> proto://host/path
    scheme, sep, rest = addr.text.partition("://")
    host = rest.split("@")[-1]
    addr.text = f"{scheme}://{host}" if sep else host


def save_feed(elm):
    """
    Persist a <feed> element keyed by its UID
    """
    uid_elm = elm.find("uid")
    if uid_elm is None:
        return None

    uid = uid_elm.text
    if not uid:
        return None

    os.makedirs(video_feed_dir(), exist_ok=True)
    with open(os.path.join(video_feed_dir(), uid), "wb") as feed_fp:
        feed_fp.write(etree.tostring(elm))

    return uid


def get_feed(uid):
    """
    Return the stored feed element for a UID, or None
    """
    path = os.path.join(video_feed_dir(), uid)
    if not os.path.isfile(path):
        return None

    try:
        return etree.parse(path).getroot()
    except (etree.XMLSyntaxError, OSError):
        return None


def del_feed(uid):
    """Delete a stored video feed"""
    try:
        os.unlink(os.path.join(video_feed_dir(), uid))
    except OSError:
        pass


@app.route("/Marti/vcm", methods=["GET", "POST", "DELETE"])
@requires_auth
def video():
    """
    The legacy Marti video connection manager

    GET:    Returns a videoConnections document of all known feeds
    POST:   ?action=add (default) stores the feeds in the posted document
            ?action=setActive&id=&active= toggles a feed's active flag
    DELETE: ?id= deletes the feed
    """
    if request.method == "DELETE":
        feed_id = request.args.get("id") or request.args.get("uid")
        if not feed_id:
            return "", 500
        del_feed(feed_id)
        return "", 200

    if request.method == "POST":
        if request.args.get("action") == "setActive":
            feed_id = request.args.get("id") or request.args.get("uid")
            elm = get_feed(feed_id)
            if elm is None:
                return "", 500

            active = elm.find("active")
            if active is None:
                active = etree.SubElement(elm, "active")
            active.text = request.args.get("active", "true")

            save_feed(elm)
            return "", 200

        try:
            parser = etree.XMLParser(recover=True)
            elm = etree.fromstring(request.data, parser)

            if elm.tag != "videoConnections":
                raise TypeError("root element must be videoConnections")

            for child in elm.iterchildren():
                if child.tag != "feed":
                    continue

                sanitize_feed(child)
                save_feed(child)

            return "", 200
        except (etree.XMLSyntaxError, TypeError) as exc:
            app.logger.warning("Unable to parse videoConnections: %s", exc)
            return "", 500

    # GET
    conn = etree.Element("videoConnections")
    for _uid, elm in iter_feeds():
        conn.append(elm)

    return etree.tostring(conn)


FEED_V2_MAP = {
    "uuid": "uid",
    "url": "address",
    "macAddress": "preferredMacAddress",
    "networkTimeout": "timeout",
    "bufferTime": "buffer",
}
FEED_V2_SHARED = [
    "active",
    "alias",
    "roverPort",
    "ignoreEmbeddedKLV",
    "source",
    "rtspReliable",
    "thumbnail",
    "classification",
    "latitude",
    "longitude",
    "fov",
    "heading",
    "range",
]


def feed_to_v2(elm):
    """
    Convert a legacy <feed> element to a FeedV2 object
    """
    feed: dict = {
        "order": 0,
        "width": 0,
        "height": 0,
        "bitrate": 0,
    }
    for v2_key, v1_key in FEED_V2_MAP.items():
        child = elm.find(v1_key)
        if child is not None and child.text:
            feed[v2_key] = child.text

    for key in FEED_V2_SHARED:
        child = elm.find(key)
        if child is None:
            continue
        if key == "active":
            feed[key] = child.text == "true"
        else:
            feed[key] = child.text or ""

    return feed


def feed_from_v2(feed, uid=None):
    """
    Convert a FeedV2 object to a legacy <feed> element
    """
    elm = etree.Element("feed")

    mapping = {v1: v2 for v2, v1 in FEED_V2_MAP.items()}
    for key in FEED_V2_SHARED:
        mapping[key] = key

    for v1_key, v2_key in mapping.items():
        val = feed.get(v2_key)
        if val is None or v1_key == "uid":
            continue
        child = etree.SubElement(elm, v1_key)
        child.text = str(val).lower() if isinstance(val, bool) else str(val)

    uid_elm = etree.SubElement(elm, "uid")
    uid_elm.text = uid or feed.get("uuid") or str(uuid.uuid4())

    if elm.find("active") is None:
        etree.SubElement(elm, "active").text = "true"

    return elm


def conn_to_json(uid, elm):
    """
    Build a VideoConnection object from a stored feed
    """
    alias = elm.find("alias")
    return {
        "uuid": uid,
        "active": True,
        "alias": alias.text if alias is not None else "",
        "thumbnail": "",
        "classification": "",
        "feeds": [feed_to_v2(elm)],
    }


@app.route("/Marti/api/video", methods=["GET", "POST"])
@requires_auth
def video_api():
    """
    The Marti v2 video connection manager

    GET:  Returns a VideoCollections object of all known feeds
    POST: Stores the feeds in the posted VideoCollections object
    """
    if request.method == "POST":
        try:
            body = request.get_json(force=True)
            for conn in body.get("videoConnections", []):
                for feed in conn.get("feeds", []):
                    elm = feed_from_v2(feed)
                    sanitize_feed(elm)
                    save_feed(elm)
        except Exception as exc:  # pylint: disable=broad-except
            app.logger.warning("Unable to parse video connections: %s", exc)
            return "", 500

        return "", 200

    connections = []
    for uid, elm in iter_feeds():
        connections.append(conn_to_json(uid, elm))

    return {"videoConnections": connections}


@app.route("/Marti/api/video/<uid>", methods=["GET", "PUT", "DELETE"])
@requires_auth
def video_api_uid(uid):
    """
    Get, update, or delete a single video connection
    """
    if request.method == "DELETE":
        if get_feed(uid) is None:
            return "", 404
        del_feed(uid)
        return "", 200

    if request.method == "PUT":
        try:
            body = request.get_json(force=True)
            feeds = body.get("feeds", [])
            if not feeds:
                return "", 400
            elm = feed_from_v2(feeds[0], uid=uid)
            sanitize_feed(elm)
            save_feed(elm)
            return "", 200
        except Exception as exc:  # pylint: disable=broad-except
            app.logger.warning("Unable to parse video connection: %s", exc)
            return "", 500

    # GET
    elm = get_feed(uid)
    if elm is None:
        return "", 404

    return conn_to_json(uid, elm)
