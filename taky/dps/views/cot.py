from datetime import datetime as dt
from datetime import timezone, timedelta

from lxml import etree
from flask import request, Response

from taky.dps import app, requires_auth
from taky.util import mgmt

XML_HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def _parse_ts(val):
    if not val:
        return None
    try:
        ret = dt.fromisoformat(val.replace("Z", "+00:00"))
        if ret.tzinfo is None:
            ret = ret.replace(tzinfo=timezone.utc)
        return ret
    except (ValueError, AttributeError):
        return None


def _parse_events(xml_list):
    """
    Parse a list of event XML strings into elements
    """
    for xml in xml_list or []:
        try:
            yield etree.fromstring(xml.encode())
        except (etree.XMLSyntaxError, TypeError):
            continue


def _events_doc(elms):
    """
    Build an <events> document from event elements
    """
    parts = [XML_HEADER, "<events>"]
    for elm in elms:
        parts.append(etree.tostring(elm).decode())
    parts.append("</events>")

    return Response("\n".join(parts), mimetype="application/xml")


@app.route("/Marti/api/cot/xml/<path:uid>")
@requires_auth
def cot_get(uid):
    """
    Return the latest CoT event for a UID
    """
    events = mgmt.cot_get(uid)
    if not events:
        return "", 404

    for elm in _parse_events(events):
        return Response(
            XML_HEADER + etree.tostring(elm).decode(),
            mimetype="application/xml",
        )

    return "", 404


@app.route("/Marti/api/cot/xml/<path:uid>/all")
@requires_auth
def cot_get_all(uid):
    """
    Return all CoT events for a UID

    Arguments:
        secago: (optional) Only include events within the last N seconds
        start:  (optional) Only include events after this ISO8601 time
        end:    (optional) Only include events before this ISO8601 time
    """
    events = mgmt.cot_get(uid)
    if not events:
        return "", 404

    start = _parse_ts(request.args.get("start"))
    end = _parse_ts(request.args.get("end"))
    try:
        secago = int(request.args.get("secago", 0) or 0)
    except ValueError:
        return "secago must be an integer", 400

    if secago:
        start = dt.now(timezone.utc) - timedelta(seconds=secago)

    elms = []
    for elm in _parse_events(events):
        evt_time = _parse_ts(elm.get("time"))
        if start and evt_time and evt_time < start:
            continue
        if end and evt_time and evt_time > end:
            continue
        elms.append(elm)

    if not elms:
        return "", 404

    return _events_doc(elms)


@app.route("/Marti/api/cot", methods=["GET", "POST"])
@requires_auth
def cot_multi():
    """
    Return the latest CoT event for each UID in the posted JSON list
    """
    uids = request.get_json(silent=True)
    if not isinstance(uids, list) or not uids:
        return "one or more UIDs must be specified", 400

    elms = []
    for uid in uids:
        elms.extend(_parse_events(mgmt.cot_get(uid)))

    return _events_doc(elms)


@app.route("/Marti/api/cot/sa")
@requires_auth
def cot_sa():
    """
    Query tracked events by time and bounding box

    Arguments:
        start: (required) Start time (ISO8601)
        end:   (required) End time (ISO8601, must be within 24h of start)
        left, bottom, right, top: (optional) Bounding box
    """
    start = _parse_ts(request.args.get("start"))
    end = _parse_ts(request.args.get("end"))
    if start is None or end is None:
        return "Must supply start and end", 400

    if start > end or (end - start) > timedelta(hours=24):
        return "", 400

    bbox = None
    try:
        left = request.args.get("left", type=float)
        bottom = request.args.get("bottom", type=float)
        right = request.args.get("right", type=float)
        top = request.args.get("top", type=float)
        if (
            left is not None
            and bottom is not None
            and right is not None
            and top is not None
        ):
            bbox = (left, bottom, right, top)
    except ValueError:
        bbox = None

    elms = []
    for elm in _parse_events(mgmt.cot_all()):
        evt_time = _parse_ts(elm.get("time"))
        if evt_time is None or evt_time < start or evt_time > end:
            continue

        if bbox:
            point = elm.find("point")
            if point is None:
                continue
            try:
                lat = float(point.get("lat"))
                lon = float(point.get("lon"))
            except (TypeError, ValueError):
                continue

            if not (bbox[1] <= lat <= bbox[3] and bbox[0] <= lon <= bbox[2]):
                continue

        elms.append(elm)

    if not elms:
        return "", 404

    return _events_doc(elms)


@app.route("/Marti/api/cot/matchUid")
@requires_auth
def cot_match_uid():
    """
    Return a list of tracked UIDs matching the search string
    """
    search = request.args.get("search", "")

    uids = []
    for elm in _parse_events(mgmt.cot_all()):
        uid = elm.get("uid")
        if uid and search.lower() in uid.lower():
            uids.append(uid)

    return uids
