import os
import zipfile

from lxml import etree
from flask import request, send_file

from taky.dps import app, requires_auth


def _citrap_dir():
    return os.path.join(app.config["UPLOAD_PATH"], "citrap")


def _report_meta(zip_path):
    """
    Parse the report.xml inside a citrap zip, returning its attributes
    """
    try:
        with zipfile.ZipFile(zip_path) as zfp:
            for name in zfp.namelist():
                if name.endswith("report.xml"):
                    root = etree.fromstring(zfp.read(name))
                    if root.tag != "report":
                        return None
                    return dict(root.attrib)
    except (zipfile.BadZipFile, etree.XMLSyntaxError, OSError):
        return None

    return None


def _iter_reports():
    citrap_dir = _citrap_dir()
    if not os.path.isdir(citrap_dir):
        return

    for item in os.listdir(citrap_dir):
        path = os.path.join(citrap_dir, item)
        if not os.path.isfile(path) or not item.endswith(".zip"):
            continue

        meta = _report_meta(path)
        if meta is None:
            continue

        meta.setdefault("id", item[:-4])
        yield meta


@app.route("/Marti/api/citrap", methods=["GET", "POST"])
@requires_auth
def citrap():
    """
    GET:  List the stored CITrap reports
    POST: Upload a new report zip (requires clientUid argument)
    """
    if request.method == "GET":
        return list(_iter_reports())

    client_uid = request.args.get("clientUid")
    if not client_uid:
        return "Must supply clientUid", 400

    payload = request.get_data()
    if not payload:
        return "A file is required", 400

    # The report ID comes from the report.xml inside the zip -- stash the
    # file first, then parse
    os.makedirs(_citrap_dir(), exist_ok=True)
    tmp_path = os.path.join(_citrap_dir(), "incoming.zip")
    with open(tmp_path, "wb") as fp:
        fp.write(payload)

    meta = _report_meta(tmp_path)
    if meta is None or not meta.get("id"):
        os.unlink(tmp_path)
        return "Unable to find report id", 400

    report_id = meta["id"]
    os.replace(tmp_path, os.path.join(_citrap_dir(), f"{report_id}.zip"))

    return {"id": report_id}, 201


@app.route("/Marti/api/citrap/<report_id>", methods=["GET", "PUT", "DELETE"])
@requires_auth
def citrap_id(report_id):
    """Get, replace, or delete a stored CITrap report"""
    if request.method == "DELETE":
        try:
            os.unlink(os.path.join(_citrap_dir(), f"{report_id}.zip"))
            return "", 200
        except OSError:
            return "", 404

    if request.method == "PUT":
        payload = request.get_data()
        if payload:
            os.makedirs(_citrap_dir(), exist_ok=True)
            with open(os.path.join(_citrap_dir(), f"{report_id}.zip"), "wb") as fp:
                fp.write(payload)
        return "", 200

    path = os.path.join(_citrap_dir(), f"{report_id}.zip")
    if not os.path.isfile(path):
        return "", 404

    return send_file(path, mimetype="application/zip", as_attachment=True)


@app.route("/Marti/api/citrap/attachment", methods=["POST"])
@app.route("/Marti/api/citrap/<report_id>/attachment", methods=["POST"])
@requires_auth
def citrap_attachment(report_id=None):
    """Attach files to a report -- attachments are inside the report zip"""
    # pylint: disable=unused-argument
    return "", 200
