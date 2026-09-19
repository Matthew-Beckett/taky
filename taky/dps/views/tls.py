import base64
import binascii
import json
import tempfile
import os

from datetime import datetime as dt, timedelta, timezone

from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12, PrivateFormat

from lxml import etree
from flask import request, Response, abort

from taky.dps import app
from taky.config import app_config
from taky.util import anc


def _enrollment_token():
    return app_config.get("dp_server", "enrollment_token", fallback=None)


def requires_enrollment_token(func):
    """
    TLS enrollment happens before a client has a certificate, so it cannot
    rely on the X-USER check. Require the configured enrollment token
    instead; when unset, these endpoints are disabled entirely.
    """

    def check(*args, **kwargs):
        token = _enrollment_token()
        if not token:
            abort(404)

        supplied = request.args.get("token")
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            supplied = auth[7:]
        elif auth.startswith("Basic "):
            try:
                supplied = base64.b64decode(auth[6:]).decode().split(":", 1)[-1]
            except (binascii.Error, UnicodeDecodeError):
                supplied = None

        if supplied != token:
            abort(403)

        return func(*args, **kwargs)

    check.__name__ = func.__name__
    return check


def _load_ca():
    return anc.load_certificate(
        app_config.get("ssl", "ca"), app_config.get("ssl", "ca_key")
    )


def _parse_csr(data):
    """
    Parse a PEM or base64 encoded CSR
    """
    if isinstance(data, str):
        data = data.encode()

    try:
        return x509.load_pem_x509_csr(data)
    except ValueError:
        pass

    try:
        decoded = base64.b64decode(data)
        try:
            return x509.load_pem_x509_csr(decoded)
        except ValueError:
            return x509.load_der_x509_csr(decoded)
    except (binascii.Error, ValueError):
        return None


def _sign_csr(csr, days=730):
    """
    Sign a CSR with the server's CA
    """
    ca_crt, ca_key = _load_ca()

    now = dt.now(timezone.utc).replace(tzinfo=None)
    cert = (
        x509.CertificateBuilder()
        .issuer_name(ca_crt.subject)
        .subject_name(csr.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False
        )
        .sign(private_key=ca_key, algorithm=hashes.SHA256())  # type: ignore
    )

    anc.CertificateDatabase().add_certificate(cert)
    return cert, ca_crt


def _pems(cert, ca_crt):
    signed_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    ca_pem = ca_crt.public_bytes(serialization.Encoding.PEM).decode()
    return signed_pem, ca_pem


@app.route("/Marti/api/tls/config")
def tls_config():
    """
    Return the certificate enrollment name entries, derived from the CA
    """
    try:
        ca_crt, _ca_key = _load_ca()
    except (OSError, ValueError):
        abort(404)

    root = etree.Element(
        "certificateConfig",
        attrib={"xmlns": "http://bbn.com/marti/xml/config"},
    )
    entries = etree.SubElement(root, "nameEntries")
    for attr in ca_crt.subject:
        etree.SubElement(
            entries,
            "nameEntry",
            attrib={"name": attr.oid._name.upper(), "value": attr.value},
        )

    return Response(etree.tostring(root), mimetype="text/plain; charset=UTF-8")


@app.route("/Marti/api/tls/signClient", methods=["POST"])
@app.route("/Marti/api/tls/signClient/", methods=["POST"])
@requires_enrollment_token
def sign_client():
    """
    Sign a client CSR, returning a PKCS12 truststore
    """
    csr = _parse_csr(request.get_data())
    if csr is None:
        return "Unable to parse CSR", 400

    cert, ca_crt = _sign_csr(csr)

    kseb = PrivateFormat.PKCS12.encryption_builder()
    kseb = kseb.kdf_rounds(1)
    kseb = kseb.hmac_hash(hashes.SHA1())
    kseb = kseb.key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC)

    p12 = pkcs12.serialize_key_and_certificates(
        name=(request.args.get("clientUid") or "signedCert").encode(),
        key=None,
        cert=cert,
        cas=[pkcs12.PKCS12Certificate(cert=ca_crt, friendly_name=b"CA")],
        encryption_algorithm=kseb.build(b"atakatak"),
    )

    return Response(p12, mimetype="application/octet-stream")


@app.route("/Marti/api/tls/signClient/v2", methods=["POST"])
@requires_enrollment_token
def sign_client_v2():
    """
    Sign a client CSR, returning content-negotiated JSON or XML
    """
    csr = _parse_csr(request.get_data())
    if csr is None:
        return "Unable to parse CSR", 400

    cert, ca_crt = _sign_csr(csr)
    signed_pem, ca_pem = _pems(cert, ca_crt)

    accept = request.accept_mimetypes
    data = {"signedCert": signed_pem, "ca0": ca_pem}

    if accept["application/xml"] and not accept["application/json"]:
        root = etree.Element("enrollment")
        etree.SubElement(root, "signedCert").text = signed_pem
        etree.SubElement(root, "ca").text = ca_pem
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?>\n' + etree.tostring(root).decode(),
            mimetype="application/xml",
        )

    # OTS quirk -- iTAK requests text/plain but expects the JSON body
    if accept["text/plain"] and not accept["application/json"]:
        return Response(json.dumps(data), mimetype="text/plain")

    return data


@app.route("/Marti/api/tls/makeClientKeyStore")
@requires_enrollment_token
def make_client_key_store():
    """
    Generate a client certificate and key, and return a PKCS12 keystore
    """
    cn = request.args.get("cn")
    if not cn:
        return "Must supply cn", 400

    password = request.args.get("password", "atakatak")

    with tempfile.TemporaryDirectory(prefix="taky-enroll-") as tdir:
        cert = anc.make_cert(
            path=tdir,
            f_name="client",
            hostname=cn,
            cert_pw=password,
            cert_auth=(app_config.get("ssl", "ca"), app_config.get("ssl", "ca_key")),
            is_server_cert=False,
        )
        anc.CertificateDatabase().add_certificate(cert)

        with open(os.path.join(tdir, "client.p12"), "rb") as fp:
            p12 = fp.read()

    return Response(p12, mimetype="application/octet-stream")


@app.route("/Marti/api/tls/profile/enrollment")
@app.route("/Marti/api/device/profile/connection")
@app.route("/api/connection")
def profile_package():
    """Return a device profile package -- taky does not manage profiles"""
    # upstream returns 204 when no profile files are configured
    return "", 204


@app.route("/Marti/api/device/profile/<name>/missionpackage", methods=["HEAD", "GET"])
def profile_missionpackage(name):
    """Return a named device profile mission package -- none exist"""
    # pylint: disable=unused-argument
    return "", 404
