import os
import json
import time
import hashlib
import zipfile

from datetime import datetime as dt
from datetime import timezone
from io import BytesIO

from flask import request, send_file, Response
from werkzeug.utils import secure_filename

from taky.dps import app, requires_auth, api_response

# Metadata fields understood by the Marti Enterprise Sync API. Parameters
# are matched case-insensitively, and "MIME" is an alias for "MIMEType".
META_ARRAY_FIELDS = {"Keywords", "Permissions", "Contacts", "Groups"}
META_FIELDS = {
    "Altitude",
    "DownloadPath",
    "Keywords",
    "Latitude",
    "Longitude",
    "Hash",
    "MIMEType",
    "Name",
    "Permissions",
    "Size",
    "Remarks",
    "SubmissionUser",
    "PrimaryKey",
    "SubmissionDateTime",
    "UID",
    "Contacts",
    "CreatorUid",
    "Tool",
    "EXPIRATION",
    "PluginClassName",
    "Groups",
    "MissionName",
}


def iso_now():
    """Return the current time as an ISO8601 string"""
    return dt.now(timezone.utc).isoformat().replace("+00:00", "Z")


def url_for(f_hash):
    """
    Returns the URL for the given hash
    """
    return f"{request.host_url}Marti/sync/content?hash={f_hash}"


def _meta_dir():
    return os.path.join(app.config["UPLOAD_PATH"], "meta")


def get_meta(f_hash=None, f_name=None, f_uid=None):
    """
    Gets the metadata for an assigned filename, hash, or UID
    """
    key = f_hash or f_name or f_uid
    if not key:
        return {}

    meta_path = os.path.join(_meta_dir(), f"{key}.json")
    try:
        with open(meta_path, encoding="utf8") as meta_fp:
            return normalize_meta(json.load(meta_fp))
    except (json.JSONDecodeError, OSError):
        return {}


def normalize_meta(meta):
    """
    Normalize a metadata record to the upstream Marti schema.

    Older taky records stored "Visibility" and a string "Keywords" --
    translate those into the upstream "Tool" and list-typed "Keywords".
    """
    if not meta:
        return meta

    meta = dict(meta)

    if "Tool" not in meta:
        meta["Tool"] = meta.get("Visibility", "public")

    kws = meta.get("Keywords")
    if kws is None:
        meta["Keywords"] = []
    elif isinstance(kws, str):
        meta["Keywords"] = [k.strip() for k in kws.split(",") if k.strip()]

    meta.setdefault("EXPIRATION", "-1")

    return meta


def put_meta(meta):
    """
    Updates the metadata - the supplied hash/UID is used to find the target file
    """
    f_uid = meta.get("UID")
    f_hash = meta.get("Hash")

    os.makedirs(_meta_dir(), exist_ok=True)

    # Save the file's meta/{f_uid}.json
    meta_path = os.path.join(_meta_dir(), f"{f_uid}.json")
    with open(meta_path, "w", encoding="utf8") as meta_fp:
        json.dump(meta, meta_fp)

    # Symlink the meta/{f_hash}.json to {f_uid}.json
    if f_hash and f_hash != f_uid:
        meta_hash_path = os.path.join(_meta_dir(), f"{f_hash}.json")
        try:
            os.symlink(f"{f_uid}.json", meta_hash_path)
        except FileExistsError:
            pass
        except OSError:
            # Filesystem may not support symlinks -- write a copy
            with open(meta_hash_path, "w", encoding="utf8") as meta_fp:
                json.dump(meta, meta_fp)


def _candidate_files(meta):
    """
    Candidate on-disk names for the content described by a metadata record
    """
    yield meta.get("_taky_file")
    yield meta.get("UID")
    creator_uid = meta.get("CreatorUid")
    name = meta.get("Name")
    if creator_uid and name:
        yield f"{creator_uid}_{name}"
        yield f"{creator_uid}_{name}.zip"


def file_path_for(meta):
    """
    The on-disk path for the content described by the metadata record
    """
    for f_name in _candidate_files(meta):
        if not f_name:
            continue
        path = os.path.join(app.config["UPLOAD_PATH"], f_name)
        if os.path.isfile(path):
            return path

    return None


def del_meta(meta):
    """
    Remove a metadata record, its hash alias, and the stored file
    """
    f_uid = meta.get("UID")
    f_hash = meta.get("Hash")

    for key in [f_uid, f_hash]:
        if not key:
            continue
        try:
            os.unlink(os.path.join(_meta_dir(), f"{key}.json"))
        except OSError:
            pass

    f_path = file_path_for(meta)
    if f_path:
        try:
            os.unlink(f_path)
        except OSError:
            pass


def all_meta():
    """
    Iterate all unique metadata records in the upload path
    """
    meta_dir = _meta_dir()
    if not os.path.isdir(meta_dir):
        return

    seen = set()
    for item in os.listdir(meta_dir):
        path = os.path.join(meta_dir, item)
        if not os.path.isfile(path) or not item.endswith(".json"):
            continue

        meta = get_meta(f_name=item[:-5])
        if not meta:
            continue

        key = meta.get("UID") or item
        if key in seen:
            continue
        seen.add(key)
        yield meta


def get_arg(*names, default=None):
    """
    Fetch a request argument, case-insensitively
    """
    lowered = {k.lower(): v for k, v in request.args.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]

    return default


@app.route("/Marti/sync/search")
@requires_auth
def datapackage_search():
    """
    Search for a datapackage

    Arguments (matched case-insensitively):
        BBox, Circle, StartTime, StopTime, SubmissionDateTime, MinAltitude,
        MaxAltitude, PrimaryKey, Filename, Keywords, MIMEType (or MIME), Name,
        Permissions, Remarks, UID, Tool
    """
    kw_filter = get_arg("keywords")
    tool_filter = get_arg("tool")
    uid_filter = get_arg("uid")
    name_filter = get_arg("name")
    mime_filter = get_arg("mimetype", "mime")
    pk_filter = get_arg("primarykey")
    user_filter = get_arg("submissionuser")
    creator_filter = get_arg("creatoruid")
    remarks_filter = get_arg("remarks")
    start_filter = get_arg("starttime", "submissiondatetime")
    stop_filter = get_arg("stoptime")

    keywords = [k.strip().lower() for k in (kw_filter or "").split(",") if k.strip()]

    def parse_ts(val):
        if not val:
            return None
        try:
            return dt.fromisoformat(val.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    start_ts = parse_ts(start_filter)
    stop_ts = parse_ts(stop_filter)

    ret = []
    for meta in all_meta():
        tool = meta.get("Tool", meta.get("Visibility", "public"))

        if tool_filter and tool.lower() != tool_filter.lower():
            continue
        elif not tool_filter and tool.lower() != "public":
            # Preserve taky's privacy semantics -- files marked private are
            # only returned when a tool filter explicitly requests them
            continue

        if (
            uid_filter
            and meta.get("UID") != uid_filter
            and meta.get("Hash") != uid_filter
        ):
            continue
        if name_filter and meta.get("Name", "").lower() != name_filter.lower():
            continue
        if mime_filter and meta.get("MIMEType", "").lower() != mime_filter.lower():
            continue
        if pk_filter and str(meta.get("PrimaryKey")) != pk_filter:
            continue
        if (
            user_filter
            and meta.get("SubmissionUser", "").lower() != user_filter.lower()
        ):
            continue
        if (
            creator_filter
            and meta.get("CreatorUid", "").lower() != creator_filter.lower()
        ):
            continue
        if (
            remarks_filter
            and remarks_filter.lower() not in meta.get("Remarks", "").lower()
        ):
            continue

        if keywords:
            meta_kw = [k.lower() for k in meta.get("Keywords", [])]
            if not any(req in mkw or mkw in req for req in keywords for mkw in meta_kw):
                continue

        if start_ts or stop_ts:
            sub_ts = parse_ts(meta.get("SubmissionDateTime"))
            if sub_ts is None:
                continue
            if start_ts and sub_ts < start_ts:
                continue
            if stop_ts and sub_ts > stop_ts:
                continue

        meta.pop("_taky_file", None)
        ret.append(meta)

    return Response(
        json.dumps({"resultCount": len(ret), "results": ret}),
        mimetype="text/json",
    )


@app.route("/Marti/api/sync/search")
@requires_auth
def api_sync_search():
    """
    Search for resources, returning the newer Marti Resource JSON shape
    """
    hash_filter = get_arg("hash")
    uid_filter = get_arg("uid")
    kw_filter = get_arg("keyword")

    data = []
    for meta in all_meta():
        if hash_filter and meta.get("Hash") != hash_filter:
            continue
        if (
            uid_filter
            and meta.get("UID") != uid_filter
            and meta.get("Hash") != uid_filter
        ):
            continue
        if kw_filter:
            meta_kw = [k.lower() for k in meta.get("Keywords", [])]
            if kw_filter.lower() not in meta_kw and not any(
                kw_filter.lower() in k for k in meta_kw
            ):
                continue

        try:
            expiration = int(meta.get("EXPIRATION", "-1"))
        except (TypeError, ValueError):
            expiration = -1

        try:
            size = int(meta.get("Size", 0))
        except (TypeError, ValueError):
            size = 0

        data.append(
            {
                "filename": meta.get("Name"),
                "keywords": meta.get("Keywords", []),
                "mimeType": meta.get("MIMEType"),
                "name": meta.get("Name"),
                "submissionTime": meta.get("SubmissionDateTime"),
                "submitter": meta.get("SubmissionUser"),
                "uid": meta.get("UID"),
                "creatorUid": meta.get("CreatorUid"),
                "hash": meta.get("Hash"),
                "size": size,
                "tool": meta.get("Tool", "public"),
                "groups": meta.get("Groups", []),
                "expiration": expiration,
                "latitude": float(meta.get("Latitude", 0) or 0),
                "longitude": float(meta.get("Longitude", 0) or 0),
                "altitude": float(meta.get("Altitude", 0) or 0),
            }
        )

    return api_response(
        data=data,
        rtype="gov.tak.api.comms.takserver.mission.data.Resource",
        messages=[],
    )


@app.route("/Marti/sync/content", methods=["GET", "HEAD", "PUT"])
@requires_auth
def datapackage_get():
    """
    Download a datapackage

    Arguments:
        hash:     The file hash
        uid:      The file UID
        offset:   Byte offset to start reading at
        length:   Maximum number of bytes to return
    """
    f_hash = get_arg("hash") or get_arg("uid")
    if not f_hash:
        return "Must supply hash or uid", 400

    meta = get_meta(f_hash=f_hash) or get_meta(f_uid=f_hash)
    if not meta:
        return "File not found", 404

    # Upstream quirk: clients PUT the "tool" value (public/private) to the
    # URL returned by missionupload
    if request.method == "PUT":
        meta["Tool"] = request.get_data().decode("utf-8").strip() or "private"
        put_meta(meta)
        return "", 200

    name = file_path_for(meta)
    if name is None:
        return "File not found", 404

    f_size = os.path.getsize(name)
    f_name = meta.get("DownloadPath") or meta.get("Name") or "EnterpriseSync.dat"

    headers = {
        "api-version": "3",
        "Content-Disposition": f'inline; filename="{f_name}"',
    }
    if meta.get("MIMEType"):
        headers["Content-Type"] = meta["MIMEType"]

    if request.method == "HEAD":
        resp = Response(headers=headers)
        resp.content_length = f_size
        return resp

    try:
        offset = int(get_arg("offset", default=0) or 0)
        length = int(get_arg("length", default=0) or 0)
    except ValueError:
        return "Bad numeric format in request parameters.", 400

    if offset > 0 or length > 0:
        with open(name, "rb") as fp:
            fp.seek(offset)
            data = fp.read(length if length > 0 else -1)

        status = 206 if length > 0 and offset + length < f_size else 200
        return Response(
            data,
            status=status,
            headers=headers,
            content_type=meta.get("MIMEType"),
        )

    resp = send_file(name, download_name=f_name, mimetype=meta.get("MIMEType"))
    for key, val in headers.items():
        resp.headers[key] = val
    return resp


def _get_upload_payload():
    """
    Return (file_bytes, part_filename, part_mimetype) from a raw POST body
    or a multipart submission (the "assetfile" or "resource" part)
    """
    if request.content_type and request.content_type.startswith("multipart/form-data"):
        part = request.files.get("assetfile") or request.files.get("resource")
        if part is None:
            # Take the first uploaded part, like OTS does
            part = next(iter(request.files.values()), None)
        if part is None:
            return b"", None, None
        return part.read(), part.filename, part.mimetype

    # Anything else is a raw byte stream -- read it directly so a
    # mislabeled content type does not lose the body to form parsing
    return request.stream.read(), None, request.mimetype


def _collect_meta_params():
    """
    Map request arguments onto upstream Metadata fields
    """
    meta = {}
    for key, val in request.args.items():
        field = None
        for candidate in META_FIELDS:
            if candidate.lower() == key.lower():
                field = candidate
                break
        if field is None and key.lower() == "mime":
            field = "MIMEType"
        if field is None or field in [
            "Hash",
            "Size",
            "PrimaryKey",
            "SubmissionDateTime",
        ]:
            continue

        if field in META_ARRAY_FIELDS:
            meta[field] = [v.strip() for v in val.split(",") if v.strip()]
        else:
            meta[field] = val

    return meta


@app.route("/Marti/sync/upload", methods=["POST"])
@requires_auth
def datapackage_upload_itak():
    """
    Upload a resource to the server (used by iTAK for datapackage upload)

    Arguments: any Metadata field (name, uid, CreatorUid, keywords, ...)

    Return:
        The uploaded resource's metadata, as JSON
    """
    payload, part_name, part_mime = _get_upload_payload()
    if not payload:
        return "POST request contained no data!", 400

    meta = _collect_meta_params()

    f_hash = hashlib.sha256(payload).hexdigest()
    uid = meta.get("UID") or f_hash
    name = meta.get("Name") or part_name
    if not name:
        return "Must supply name", 400

    filename = secure_filename(f"{uid}_{name}")

    meta.update(
        {
            "UID": uid,
            "Name": name,
            "Hash": f_hash,
            "PrimaryKey": str(round(time.time() * 1000)),
            "SubmissionDateTime": iso_now(),
            "SubmissionUser": request.headers.get("X-USER", "Anonymous"),
            "MIMEType": meta.get("MIMEType") or part_mime or "application/octet-stream",
            "Size": str(len(payload)),
            "Tool": meta.get("Tool", "public"),
            "EXPIRATION": meta.get("EXPIRATION", "-1"),
            "_taky_file": filename,
        }
    )
    meta.setdefault("Keywords", [])
    meta.setdefault("CreatorUid", "")

    file_path = os.path.join(app.config["UPLOAD_PATH"], filename)
    with open(file_path, "wb") as binary_file:
        binary_file.write(payload)

    put_meta(meta)

    # Upstream returns the resource metadata as JSON
    ret = {k: v for k, v in meta.items() if not k.startswith("_")}
    return Response(json.dumps(ret), mimetype="text/json")


@app.route("/Marti/sync/missionupload", methods=["POST"])
@requires_auth
def datapackage_upload():
    """
    Upload a datapackage to the server

    Arguments:
        filename=... (lacking extension)
        creatorUid=ANDROID-43... (optional)
        hash=... (optional, ignored -- computed server-side)
        keyword=... (optional, default "missionpackage")
        tool=... (optional, default "public")

    Return:
        The URL where the file can be downloaded
    """
    filename = get_arg("filename")
    if not filename:
        return "Required parameter filename is not present", 400

    payload, part_name, part_mime = _get_upload_payload()
    if not payload:
        return "Unable to find content in multi-part submission", 400

    creator_uid = get_arg("creatorUid") or ""
    keyword = get_arg("keyword", "keywords") or "missionpackage"
    tool = get_arg("tool") or "public"

    f_hash = hashlib.sha256(payload).hexdigest()
    uid = f_hash  # upstream uses the content hash as the UID

    # Refuse to overwrite an identical database entry -- upstream keys on
    # filename and creatorUid, not content
    for existing in all_meta():
        if (
            existing.get("Name") == filename
            and existing.get("CreatorUid", "") == creator_uid
        ):
            return (
                "HTTP post attempting to overwrite existing database entry with same values",
                403,
            )

    taky_file = secure_filename(
        f"{creator_uid}_{filename}" if creator_uid else filename
    )

    file_path = os.path.join(app.config["UPLOAD_PATH"], taky_file)
    with open(file_path, "wb") as binary_file:
        binary_file.write(payload)

    meta = {
        "UID": uid,
        "Name": filename,
        "DownloadPath": part_name or filename,
        "Hash": f_hash,
        "PrimaryKey": str(round(time.time() * 1000)),
        "SubmissionDateTime": iso_now(),
        "SubmissionUser": request.headers.get("X-USER", "Anonymous"),
        "CreatorUid": creator_uid,
        "Keywords": [k.strip() for k in keyword.split(",") if k.strip()],
        "MIMEType": get_arg("mimetype") or part_mime or "application/x-zip-compressed",
        "Size": str(len(payload)),
        "Tool": tool,
        "EXPIRATION": "-1",
        "_taky_file": taky_file,
    }

    put_meta(meta)

    # src/main/java/com/atakmap/android/missionpackage/http/MissionPackageDownloader.java:539
    # This is needed for client-to-client data package transmission
    return Response(url_for(f_hash), mimetype="text/plain")


@app.route("/Marti/sync/missioncreate", methods=["POST"])
@requires_auth
def datapackage_create():
    """
    Combine uploaded files into a zip datapackage

    Arguments:
        filename=... (required)
        contacts=... (optional)

    Return:
        The URL where the created zip can be downloaded
    """
    filename = get_arg("filename")
    if not filename:
        return "Required parameter filename is not present", 400

    if not filename.endswith(".zip"):
        filename += ".zip"

    buff = BytesIO()
    with zipfile.ZipFile(buff, "w", zipfile.ZIP_DEFLATED) as zfp:
        for part in request.files.values():
            zfp.writestr(secure_filename(part.filename or "content"), part.read())

    payload = buff.getvalue()
    if not payload:
        return "No content submitted", 400

    f_hash = hashlib.sha256(payload).hexdigest()
    taky_file = secure_filename(f"{get_arg('creatorUid') or ''}_{filename}".lstrip("_"))

    file_path = os.path.join(app.config["UPLOAD_PATH"], taky_file)
    with open(file_path, "wb") as fp:
        fp.write(payload)

    meta = {
        "UID": f_hash,
        "Name": filename,
        "Hash": f_hash,
        "PrimaryKey": str(round(time.time() * 1000)),
        "SubmissionDateTime": iso_now(),
        "SubmissionUser": request.headers.get("X-USER", "Anonymous"),
        "CreatorUid": get_arg("creatorUid") or "",
        "Keywords": ["missionpackage"],
        "MIMEType": "application/x-zip-compressed",
        "Size": str(len(payload)),
        "Tool": "public",
        "EXPIRATION": "-1",
        "_taky_file": taky_file,
    }
    put_meta(meta)

    return Response(url_for(f_hash), mimetype="text/plain")


@app.route("/Marti/sync/delete", methods=["GET", "POST", "DELETE"])
@requires_auth
def datapackage_delete():
    """
    Delete resources by PrimaryKey or Hash
    """
    f_hash = get_arg("hash")
    pks = [v for k, v in request.args.items() if k.lower() == "primarykey"]

    deleted = 0
    if f_hash:
        meta = get_meta(f_hash=f_hash)
        if meta:
            del_meta(meta)
            deleted += 1
    else:
        for pk in pks:
            if not pk.isdigit():
                return "PrimaryKey must be numeric.", 400
            for meta in all_meta():
                if str(meta.get("PrimaryKey")) == pk:
                    del_meta(meta)
                    deleted += 1
                    break

    return Response(
        "<html><head><title>Enterprise Sync Status</title></head>"
        f"<h1>Success</h1><p>Deleted {deleted} resource(s).</p></html>",
        mimetype="text/html",
    )


@app.route("/Marti/api/sync/metadata/<f_hash>/tool", methods=["GET", "PUT"])
@app.route("/Marti/api/sync/metadata/<f_hash>/<metadata>", methods=["PUT"])
@requires_auth
def datapackage_metadata(f_hash, metadata="tool"):
    """
    Update a metadata field on a datapackage. Upstream only permits "tool"
    and "mimetype" on this path; "keywords" and "expiration" have their own
    semantics.
    """
    meta = get_meta(f_hash=f_hash) or get_meta(f_uid=f_hash)
    if not meta:
        return "", 404

    field = metadata.lower()

    # OTS quirk: a GET on the tool URL serves the file
    if request.method == "GET":
        f_path = file_path_for(meta)
        if f_path is None:
            return "", 404
        return send_file(
            f_path,
            as_attachment=True,
            download_name=meta.get("DownloadPath") or meta.get("Name") or "",
        )

    if field == "keywords":
        try:
            keywords = request.get_json(force=True)
            if not isinstance(keywords, list):
                return "", 400
            meta["Keywords"] = [str(k) for k in keywords]
        except Exception:  # pylint: disable=broad-except
            return "", 400
    elif field == "expiration":
        expiration = get_arg("expiration")
        if expiration is None:
            return "", 400
        meta["EXPIRATION"] = str(expiration)
    elif field in ["tool", "mimetype"]:
        field_map = {"tool": "Tool", "mimetype": "MIMEType"}
        meta[field_map[field]] = request.get_data().decode("utf-8").strip()
    else:
        return "", 400

    put_meta(meta)
    return "", 200


@app.route("/Marti/sync/missionquery")
@requires_auth
def datapackage_exists():
    """
    Called when trying to determine if the file exists on the server

    Arguments:
        hash: The file hash
    """
    f_hash = get_arg("hash")
    if not f_hash:
        return "Must supply hash", 400

    meta = get_meta(f_hash=f_hash) or get_meta(f_uid=f_hash)
    if not meta:
        return "File not found", 404

    return Response(url_for(f_hash), mimetype="text/plain")


def _file_entry(meta):
    """
    Build the upstream FileManager metadata entry for a record
    """
    try:
        f_size = int(meta.get("Size", 0))
    except (TypeError, ValueError):
        f_size = 0

    if f_size < 1024:
        h_size = f"{f_size}B"
    elif f_size < 1024 * 1024:
        h_size = f"{f_size // 1024}kB"
    elif f_size < 1024 * 1024 * 1024:
        h_size = f"{f_size // (1024 * 1024)}MB"
    else:
        h_size = f"{f_size // (1024 * 1024 * 1024)}GB"

    try:
        expiration = int(meta.get("EXPIRATION", "-1"))
    except (TypeError, ValueError):
        expiration = -1
    exp_str = (
        dt.fromtimestamp(expiration, timezone.utc).isoformat()
        if expiration >= 0
        else "none"
    )

    return {
        "Name": meta.get("Name"),
        "User": meta.get("SubmissionUser"),
        "Creator": meta.get("CreatorUid"),
        "Size": h_size,
        "Time": meta.get("SubmissionDateTime"),
        "MimeType": meta.get("MIMEType"),
        "Keywords": ",".join(meta.get("Keywords", [])),
        "Expiration": exp_str,
        "Hash": meta.get("Hash"),
    }


@app.route("/Marti/api/files/metadata")
@requires_auth
def files_metadata():
    """
    List metadata for all files on the server
    """
    name_filter = get_arg("name")

    data = []
    for meta in all_meta():
        if name_filter and meta.get("Name", "").lower() != name_filter.lower():
            continue
        data.append(_file_entry(meta))

    return api_response(data=data, rtype="Files")


@app.route("/Marti/api/files/metadata/count")
@requires_auth
def files_metadata_count():
    """
    Return the number of files on the server
    """
    return api_response(data=sum(1 for _m in all_meta()), rtype="Count")


@app.route("/Marti/api/files/<f_hash>", methods=["GET", "HEAD", "DELETE"])
@requires_auth
def files_get(f_hash):
    """
    Download, describe, or delete a file by hash
    """
    meta = get_meta(f_hash=f_hash) or get_meta(f_uid=f_hash)
    if not meta:
        return "", 404

    if request.method == "DELETE":
        del_meta(meta)
        return "", 200

    if request.method == "HEAD":
        return api_response(data=_file_entry(meta), rtype="data")

    f_path = file_path_for(meta)
    if f_path is None:
        return "", 404

    return send_file(
        f_path,
        as_attachment=True,
        download_name=meta.get("DownloadPath") or meta.get("Name") or "",
        mimetype=meta.get("MIMEType"),
    )


@app.route("/Marti/api/files/<f_hash>/metadata", methods=["PUT"])
@requires_auth
def files_put_metadata(f_hash):
    """
    Update a file's user, expiration, or keywords metadata
    """
    meta = get_meta(f_hash=f_hash) or get_meta(f_uid=f_hash)
    if not meta:
        return "", 404

    if get_arg("user"):
        meta["SubmissionUser"] = get_arg("user")
    if get_arg("expiration"):
        meta["EXPIRATION"] = get_arg("expiration")
    keywords = [
        v for k, vals in request.args.lists() if k.lower() == "keywords" for v in vals
    ]
    if keywords:
        meta["Keywords"] = keywords

    put_meta(meta)
    return "", 200
