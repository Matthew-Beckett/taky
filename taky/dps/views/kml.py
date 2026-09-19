from datetime import datetime as dt
from datetime import timezone, timedelta
from io import BytesIO
import zipfile

from lxml import etree
from flask import request, Response, send_file

from taky.dps import app, requires_auth
from taky.util import mgmt

KML_NS = "http://www.opengis.net/kml/2.2"
GX_NS = "http://www.google.com/kml/ext/2.2"
NSMAP = {None: KML_NS, "gx": GX_NS}


def _parse_ts(val):
    if not val:
        return None
    ret = None
    try:
        ret = dt.fromisoformat(val.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        try:
            ret = dt.strptime(val, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    if ret is not None and ret.tzinfo is None:
        ret = ret.replace(tzinfo=timezone.utc)
    return ret


def _iter_event_elms():
    """
    Yield parsed <event> elements for all tracked events
    """
    for xml in mgmt.cot_all() or []:
        try:
            yield etree.fromstring(xml.encode())
        except etree.XMLSyntaxError:
            continue


def _build_track_kml(events):
    """
    Build a KML document of gx:Track placemarks from <event> elements
    """
    kml = etree.Element("kml", nsmap=NSMAP)
    doc = etree.SubElement(kml, "Document")
    etree.SubElement(doc, "name").text = f"{app.config['HOSTNAME']} Track History"

    for evt in events:
        uid = evt.get("uid")
        point = evt.find("point")
        if point is None:
            continue

        callsign = None
        contact = evt.find("detail/contact")
        if contact is not None:
            callsign = contact.get("callsign")

        pm = etree.SubElement(doc, "Placemark")
        etree.SubElement(pm, "name").text = callsign or uid or "Unknown"

        track = etree.SubElement(pm, f"{{{GX_NS}}}Track")
        etree.SubElement(track, "when").text = evt.get("time")
        etree.SubElement(track, f"{{{GX_NS}}}coord").text = (
            f"{point.get('lon')} {point.get('lat')} {point.get('hae', '0')}"
        )

    return kml


@app.route("/Marti/ExportMissionKML", methods=["GET", "POST"])
@requires_auth
def export_mission_kml():
    """
    Export the track history of an EUD (or all EUDs) as a KML or KMZ file

    Arguments:
        uid:       (optional) The UID of the device
        startTime: (optional) Only include events after this time
        endTime:   (optional) Only include events before this time
        format:    (optional) "kml" (default) or "kmz"
    """
    uid = request.values.get("uid")
    start = _parse_ts(request.values.get("startTime"))
    end = _parse_ts(request.values.get("endTime"))

    events = []
    for elm in _iter_event_elms():
        if uid and elm.get("uid") != uid:
            continue

        evt_time = _parse_ts(elm.get("time"))
        if start and evt_time and evt_time < start:
            continue
        if end and evt_time and evt_time > end:
            continue

        events.append(elm)

    kml = _build_track_kml(events)
    kml_bytes = etree.tostring(kml, xml_declaration=True, encoding="utf-8")

    fname = f"{app.config['HOSTNAME']}-tracks"

    if request.values.get("format", "kml").lower() == "kmz":
        buff = BytesIO()
        with zipfile.ZipFile(buff, "w", zipfile.ZIP_DEFLATED) as zfp:
            zfp.writestr("doc.kml", kml_bytes)
        buff.seek(0)
        return send_file(
            buff,
            mimetype="application/vnd.google-earth.kmz",
            as_attachment=True,
            download_name=f"{fname}.kmz",
        )

    return Response(
        kml_bytes,
        mimetype="application/vnd.google-earth.kml+xml",
        headers={"Content-Disposition": f'filename="{fname}.kml"'},
    )


@app.route("/Marti/TracksKML", methods=["POST"])
@requires_auth
def upload_track():
    """
    Ingest a KML document of track placemarks, and route them as CoT
    events

    Arguments:
        uid:      The UID the placemarks apply to
        callsign: The callsign for the track
        cotType:  (optional) The CoT type to assign (default a-f-G-U-C-I)
    """
    uid = request.values.get("uid")
    callsign = request.values.get("callsign")
    cot_type = request.values.get("cotType", "a-f-G-U-C-I")

    if not uid or not callsign:
        return "Must supply uid and callsign", 400

    try:
        kml = etree.fromstring(request.data)
    except etree.XMLSyntaxError as exc:
        app.logger.warning("Unable to parse TracksKML: %s", exc)
        return "", 500

    now = dt.now(timezone.utc)
    routed = 0

    for pm in kml.iter(f"{{{KML_NS}}}Placemark", "Placemark"):
        coords = pm.find(f"{{{KML_NS}}}Point/{{{KML_NS}}}coordinates")
        if coords is None:
            coords = pm.find("Point/coordinates")
        if coords is None or not coords.text:
            continue

        try:
            lon, lat, *rest = coords.text.strip().split(",")
            hae = rest[0] if rest else "0"
        except ValueError:
            continue

        when = pm.find(f"{{{KML_NS}}}TimeStamp/{{{KML_NS}}}when")
        if when is None:
            when = pm.find("TimeStamp/when")
        evt_time = _parse_ts(when.text if when is not None else None) or now

        evt = etree.Element("event")
        evt.set("version", "2.0")
        evt.set("uid", uid)
        evt.set("type", cot_type)
        evt.set("how", "h-e")
        evt.set(
            "time", evt_time.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        evt.set(
            "start", evt_time.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        evt.set(
            "stale",
            (now + timedelta(minutes=5))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        )

        etree.SubElement(
            evt,
            "point",
            {"lat": lat, "lon": lon, "hae": hae, "ce": "9999999", "le": "9999999"},
        )
        detail = etree.SubElement(evt, "detail")
        etree.SubElement(detail, "contact", {"callsign": callsign})

        ret = mgmt.broadcast(etree.tostring(evt).decode())
        if ret and "routed" in ret:
            routed += 1

    app.logger.info("TracksKML routed %d placemarks for %s", routed, uid)
    return "", 200
