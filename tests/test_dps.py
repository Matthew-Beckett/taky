import os
import json
import tempfile
import unittest as ut
import zipfile

from io import BytesIO
from hashlib import sha256
from unittest import mock

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from taky.config import load_config, app_config
from taky.util import anc

import taky.dps
from taky.dps import app

AUTH = {"X-USER": "testuser"}


class DPSTestcase(ut.TestCase):
    def setUp(self):
        self.upload_dir = tempfile.mkdtemp(prefix="taky-dp-")
        self.root_dir = tempfile.mkdtemp(prefix="taky-root-")
        self.cert_dir = tempfile.mkdtemp(prefix="taky-cert-")

        load_config(os.devnull)
        app_config.set("taky", "root_dir", self.root_dir)
        app_config.set("taky", "hostname", "test.local")
        app_config.set("taky", "node_id", "TESTNODE")
        app_config.set("taky", "redis", "false")
        app_config.set("dp_server", "upload_path", self.upload_dir)
        app_config.set("dp_server", "enrollment_token", "")
        app_config.set("ssl", "enabled", "false")
        app_config.set("ssl", "ca", os.path.join(self.cert_dir, "ca.crt"))
        app_config.set("ssl", "ca_key", os.path.join(self.cert_dir, "ca.key"))
        app_config.set("ssl", "cert_db", os.path.join(self.cert_dir, "cert-db.txt"))

        anc.make_ca(app_config.get("ssl", "ca"), app_config.get("ssl", "ca_key"))

        taky.dps.configure_app(app_config)
        self.cli = app.test_client()

    def jbody(self, resp):
        return json.loads(resp.get_data(as_text=True))


class TestVersion(DPSTestcase):
    def test_auth_required(self):
        app.config["SSL_ENABLED"] = True
        self.assertEqual(self.cli.get("/Marti/api/version").status_code, 401)

    def test_anonymous_when_ssl_disabled(self):
        """
        When SSL is disabled, clients cannot present certificates -- the
        API must remain usable rather than failing every request with 401.
        """
        app.config["SSL_ENABLED"] = False
        self.assertEqual(self.cli.get("/Marti/api/version").status_code, 200)

    def test_version(self):
        resp = self.cli.get("/Marti/api/version", headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_data(as_text=True).startswith("taky-"))

    def test_version_config(self):
        resp = self.cli.get("/Marti/api/version/config", headers=AUTH)
        body = resp.get_json()
        self.assertEqual(body["version"], "3")
        self.assertEqual(body["type"], "ServerConfig")
        self.assertEqual(body["data"]["api"], "3")
        self.assertEqual(body["data"]["hostname"], "test.local")
        self.assertEqual(body["nodeId"], "TESTNODE")

    def test_version_info(self):
        resp = self.cli.get("/Marti/api/version/info", headers=AUTH)
        body = resp.get_json()
        self.assertIn("major", body)
        self.assertEqual(body["variant"], "DIRECT")

    def test_node_id(self):
        resp = self.cli.get("/Marti/api/node/id", headers=AUTH)
        self.assertEqual(resp.get_data(as_text=True), "TESTNODE")


class TestDatapackage(DPSTestcase):
    def _upload(
        self,
        content=b"hello world",
        filename="test.zip",
        creator="ANDROID-123",
        extra_args="",
    ):
        data = {"assetfile": (BytesIO(content), filename)}
        return self.cli.post(
            f"/Marti/sync/missionupload?filename={filename}&creatorUid={creator}{extra_args}",
            headers=AUTH,
            data=data,
            content_type="multipart/form-data",
        )

    def test_upload_flow(self):
        content = b"hello world"
        f_hash = sha256(content).hexdigest()

        resp = self._upload(content)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(f"Marti/sync/content?hash={f_hash}", resp.get_data(as_text=True))

        # Metadata written keyed by the computed hash
        meta_path = os.path.join(self.upload_dir, "meta", f"{f_hash}.json")
        self.assertTrue(os.path.exists(meta_path))
        with open(meta_path) as fp:
            meta = json.load(fp)
        self.assertEqual(meta["Hash"], f_hash)
        self.assertEqual(meta["UID"], f_hash)
        self.assertEqual(meta["Name"], "test.zip")
        self.assertEqual(meta["Keywords"], ["missionpackage"])
        self.assertEqual(meta["Tool"], "public")

        # missionquery returns the content URL
        resp = self.cli.get(f"/Marti/sync/missionquery?hash={f_hash}", headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(f"?hash={f_hash}", resp.get_data(as_text=True))

        # content download
        resp = self.cli.get(f"/Marti/sync/content?hash={f_hash}", headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_data(), content)
        self.assertEqual(resp.headers.get("api-version"), "3")
        self.assertIn("inline", resp.headers.get("Content-Disposition"))

        # HEAD request
        resp = self.cli.head(f"/Marti/sync/content?hash={f_hash}", headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_data(), b"")
        self.assertEqual(resp.content_length, len(content))

        # range request
        resp = self.cli.get(
            f"/Marti/sync/content?hash={f_hash}&offset=1&length=4", headers=AUTH
        )
        self.assertEqual(resp.status_code, 206)
        self.assertEqual(resp.get_data(), content[1:5])

        # search finds it
        resp = self.cli.get("/Marti/sync/search", headers=AUTH)
        body = self.jbody(resp)
        self.assertEqual(body["resultCount"], 1)
        self.assertEqual(body["results"][0]["Hash"], f_hash)
        self.assertIsInstance(body["results"][0]["Keywords"], list)

        # keyword + tool filtering
        resp = self.cli.get("/Marti/sync/search?keywords=missionpackage", headers=AUTH)
        self.assertEqual(self.jbody(resp)["resultCount"], 1)
        resp = self.cli.get("/Marti/sync/search?keywords=nope", headers=AUTH)
        self.assertEqual(self.jbody(resp)["resultCount"], 0)
        resp = self.cli.get("/Marti/sync/search?tool=private", headers=AUTH)
        self.assertEqual(self.jbody(resp)["resultCount"], 0)

    def test_duplicate_upload(self):
        self.assertEqual(self._upload().status_code, 200)
        self.assertEqual(self._upload().status_code, 403)

    def test_metadata_updates(self):
        content = b"some data"
        f_hash = sha256(content).hexdigest()
        self._upload(content)

        # tool
        resp = self.cli.put(
            f"/Marti/api/sync/metadata/{f_hash}/tool",
            headers=AUTH,
            data="private",
        )
        self.assertEqual(resp.status_code, 200)

        # now hidden from the default search, but visible with tool=private
        resp = self.cli.get("/Marti/sync/search", headers=AUTH)
        self.assertEqual(self.jbody(resp)["resultCount"], 0)
        resp = self.cli.get("/Marti/sync/search?tool=private", headers=AUTH)
        self.assertEqual(self.jbody(resp)["resultCount"], 1)

        # keywords
        resp = self.cli.put(
            f"/Marti/api/sync/metadata/{f_hash}/keywords",
            headers=AUTH,
            json=["alpha", "beta"],
        )
        self.assertEqual(resp.status_code, 200)
        resp = self.cli.get(
            "/Marti/sync/search?tool=private&keywords=alpha", headers=AUTH
        )
        self.assertEqual(self.jbody(resp)["resultCount"], 1)

        # expiration
        resp = self.cli.put(
            f"/Marti/api/sync/metadata/{f_hash}/expiration?expiration=9999",
            headers=AUTH,
        )
        self.assertEqual(resp.status_code, 200)

        # invalid field
        resp = self.cli.put(
            f"/Marti/api/sync/metadata/{f_hash}/bogus",
            headers=AUTH,
            data="x",
        )
        self.assertEqual(resp.status_code, 400)

        # unknown hash
        resp = self.cli.put(
            "/Marti/api/sync/metadata/deadbeef/tool", headers=AUTH, data="public"
        )
        self.assertEqual(resp.status_code, 404)

    def test_sync_upload(self):
        content = b"raw upload"
        resp = self.cli.post(
            "/Marti/sync/upload?name=foo.txt&uid=myuid&CreatorUid=ANDROID-9"
            "&keywords=one,two",
            headers=AUTH,
            data=content,
        )
        self.assertEqual(resp.status_code, 200)
        meta = self.jbody(resp)
        self.assertEqual(meta["UID"], "myuid")
        self.assertEqual(meta["Name"], "foo.txt")
        self.assertEqual(meta["Hash"], sha256(content).hexdigest())
        self.assertEqual(meta["Keywords"], ["one", "two"])

        resp = self.cli.get("/Marti/sync/content?uid=myuid", headers=AUTH)
        self.assertEqual(resp.get_data(), content)

    def test_delete(self):
        content = b"delete me"
        f_hash = sha256(content).hexdigest()
        self._upload(content)

        resp = self.cli.delete(f"/Marti/sync/delete?hash={f_hash}", headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Deleted 1 resource(s)", resp.get_data(as_text=True))

        resp = self.cli.get(f"/Marti/sync/content?hash={f_hash}", headers=AUTH)
        self.assertEqual(resp.status_code, 404)

    def test_missioncreate(self):
        data = {
            "assetfile": (BytesIO(b"one"), "a.txt"),
            "resource": (BytesIO(b"two"), "b.txt"),
        }
        resp = self.cli.post(
            "/Marti/sync/missioncreate?filename=bundle",
            headers=AUTH,
            data=data,
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        url = resp.get_data(as_text=True)

        resp = self.cli.get(url, headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        with zipfile.ZipFile(BytesIO(resp.get_data())) as zfp:
            self.assertEqual(sorted(zfp.namelist()), ["a.txt", "b.txt"])

    def test_api_sync_search_and_files_metadata(self):
        self._upload()

        resp = self.cli.get("/Marti/api/sync/search", headers=AUTH)
        body = resp.get_json()
        self.assertEqual(
            body["type"], "gov.tak.api.comms.takserver.mission.data.Resource"
        )
        self.assertEqual(body["nodeId"], "TESTNODE")
        self.assertEqual(len(body["data"]), 1)
        self.assertEqual(body["data"][0]["name"], "test.zip")

        resp = self.cli.get("/Marti/api/files/metadata", headers=AUTH)
        body = resp.get_json()
        self.assertEqual(len(body["data"]), 1)
        self.assertEqual(body["data"][0]["Name"], "test.zip")
        self.assertIn("Hash", body["data"][0])


class TestClientEndpoints(DPSTestcase):
    @mock.patch(
        "taky.util.mgmt.status",
        return_value={
            "clients": [
                {
                    "uid": "ANDROID-1",
                    "callsign": "JENNY",
                    "username": "jennycert",
                    "team": "Cyan",
                    "role": "Team Member",
                    "last_rx": 1700000000,
                    "ip": "10.0.0.1",
                    "port": 40000,
                    "protocol": "ssl",
                    "takv": "ATAK-CIV 5.0",
                }
            ]
        },
    )
    def test_client_endpoints(self, _mock_status):
        resp = self.cli.get("/Marti/api/clientEndPoints", headers=AUTH)
        body = resp.get_json()
        self.assertEqual(body["version"], "3")
        self.assertEqual(body["type"], "com.bbn.marti.remote.ClientEndpoint")
        self.assertEqual(body["nodeId"], "TESTNODE")
        self.assertEqual(len(body["data"]), 1)
        ep = body["data"][0]
        self.assertEqual(ep["callsign"], "JENNY")
        self.assertEqual(ep["uid"], "ANDROID-1")
        self.assertEqual(ep["username"], "jennycert")
        self.assertEqual(ep["team"], "Cyan")
        self.assertEqual(ep["role"], "Team Member")
        self.assertEqual(ep["lastStatus"], "Connected")
        self.assertTrue(ep["lastEventTime"].startswith("2023-"))

    @mock.patch("taky.util.mgmt.status", return_value=None)
    def test_client_endpoints_no_server(self, _mock_status):
        resp = self.cli.get("/Marti/api/clientEndPoints", headers=AUTH)
        self.assertEqual(resp.get_json()["data"], [])


class TestContacts(DPSTestcase):
    @mock.patch(
        "taky.util.mgmt.status",
        return_value={
            "clients": [
                {
                    "uid": "ANDROID-1",
                    "callsign": "JENNY",
                    "team": "Cyan",
                    "role": "Team Member",
                    "takv": "ATAK-CIV 5.0",
                }
            ]
        },
    )
    def test_contacts_all(self, _mock_status):
        resp = self.cli.get("/Marti/api/contacts/all", headers=AUTH)
        body = resp.get_json()
        self.assertIsInstance(body, list)
        self.assertEqual(body[0]["callsign"], "JENNY")
        self.assertEqual(body[0]["uid"], "ANDROID-1")
        self.assertEqual(body[0]["team"], "Cyan")
        self.assertEqual(body[0]["takv"], "ATAK-CIV 5.0")


class TestCot(DPSTestcase):
    EVT = (
        '<event version="2.0" uid="u1" type="a-f-G-U-C" how="m-g" '
        'time="2026-09-19T12:00:00.000Z" start="2026-09-19T12:00:00.000Z" '
        'stale="2026-09-19T13:00:00.000Z">'
        '<point lat="1.0" lon="2.0" hae="0" ce="9" le="9"/>'
        '<detail><contact callsign="TEST"/></detail></event>'
    )

    @mock.patch("taky.util.mgmt.cot_get", return_value=[EVT])
    def test_cot_get(self, _mock):
        resp = self.cli.get("/Marti/api/cot/xml/u1", headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'uid="u1"', resp.get_data())

    @mock.patch("taky.util.mgmt.cot_get", return_value=[])
    def test_cot_get_missing(self, _mock):
        resp = self.cli.get("/Marti/api/cot/xml/nope", headers=AUTH)
        self.assertEqual(resp.status_code, 404)

    @mock.patch("taky.util.mgmt.cot_get", return_value=[EVT])
    def test_cot_get_all(self, _mock):
        resp = self.cli.get("/Marti/api/cot/xml/u1/all", headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"<events>", resp.get_data())
        self.assertIn(b'uid="u1"', resp.get_data())

    @mock.patch("taky.util.mgmt.cot_get", return_value=[EVT])
    def test_cot_multi(self, _mock):
        resp = self.cli.post("/Marti/api/cot", headers=AUTH, json=["u1"])
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'uid="u1"', resp.get_data())

    def test_cot_multi_empty(self):
        resp = self.cli.post("/Marti/api/cot", headers=AUTH, json=[])
        self.assertEqual(resp.status_code, 400)

    @mock.patch("taky.util.mgmt.cot_all", return_value=[EVT])
    def test_cot_sa(self, _mock):
        resp = self.cli.get(
            "/Marti/api/cot/sa?start=2026-09-19T00:00:00Z&end=2026-09-19T23:59:59Z",
            headers=AUTH,
        )
        self.assertEqual(resp.status_code, 200)

        # bbox that excludes the point
        resp = self.cli.get(
            "/Marti/api/cot/sa?start=2026-09-19T00:00:00Z&end=2026-09-19T23:59:59Z"
            "&left=10&bottom=10&right=20&top=20",
            headers=AUTH,
        )
        self.assertEqual(resp.status_code, 404)

    @mock.patch("taky.util.mgmt.cot_all", return_value=[EVT])
    def test_cot_match_uid(self, _mock):
        resp = self.cli.get("/Marti/api/cot/matchUid?search=u", headers=AUTH)
        self.assertEqual(resp.get_json(), ["u1"])


class TestGroups(DPSTestcase):
    def test_group_cache_enabled(self):
        resp = self.cli.get("/Marti/api/groups/groupCacheEnabled", headers=AUTH)
        body = resp.get_json()
        self.assertEqual(body["type"], "java.lang.Boolean")
        self.assertEqual(body["data"], False)

    def test_groups_all(self):
        resp = self.cli.get("/Marti/api/groups/all", headers=AUTH)
        body = resp.get_json()
        self.assertEqual(body["type"], "com.bbn.marti.remote.groups.Group")
        self.assertEqual(body["data"], [])

    def test_groups_members(self):
        resp = self.cli.get("/Marti/api/groups/members", headers=AUTH)
        self.assertEqual(resp.get_json()["data"], 0)

    def test_group_prefix(self):
        resp = self.cli.get("/Marti/api/groupprefix", headers=AUTH)
        self.assertEqual(resp.get_json()["data"], "")

    def test_groups_active(self):
        resp = self.cli.put("/Marti/api/groups/active", headers=AUTH, json=[])
        self.assertEqual(resp.status_code, 200)
        resp = self.cli.put("/Marti/api/groups/activebits", headers=AUTH, json={})
        self.assertEqual(resp.status_code, 200)

    @mock.patch("taky.util.mgmt.status", return_value=None)
    def test_subscriptions_all(self, _mock):
        resp = self.cli.get("/Marti/api/subscriptions/all", headers=AUTH)
        body = resp.get_json()
        self.assertEqual(body["type"], "SubscriptionInfo")
        self.assertEqual(body["data"], [])


class TestVideo(DPSTestcase):
    FEED = (
        b"<videoConnections><feed><uid>feed1</uid><alias>cam1</alias>"
        b"<address>rtsp://user:pw@example.com:554/stream</address>"
        b"<protocol>rtsp</protocol></feed></videoConnections>"
    )

    def test_vcm_flow(self):
        resp = self.cli.post("/Marti/vcm", headers=AUTH, data=self.FEED)
        self.assertEqual(resp.status_code, 200)

        resp = self.cli.get("/Marti/vcm", headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data()
        self.assertIn(b">feed1</uid>", body)
        # userinfo stripped from stored address
        self.assertNotIn(b"user:pw@", body)

        # setActive
        resp = self.cli.post(
            "/Marti/vcm?action=setActive&id=feed1&active=false", headers=AUTH
        )
        self.assertEqual(resp.status_code, 200)

        # delete
        resp = self.cli.delete("/Marti/vcm?id=feed1", headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        resp = self.cli.get("/Marti/vcm", headers=AUTH)
        self.assertNotIn(b"feed1", resp.get_data())

    def test_video_api(self):
        self.cli.post("/Marti/vcm", headers=AUTH, data=self.FEED)

        resp = self.cli.get("/Marti/api/video", headers=AUTH)
        body = resp.get_json()
        self.assertEqual(len(body["videoConnections"]), 1)
        conn = body["videoConnections"][0]
        self.assertEqual(conn["uuid"], "feed1")
        self.assertEqual(conn["feeds"][0]["alias"], "cam1")
        self.assertEqual(conn["feeds"][0]["url"], "rtsp://example.com:554/stream")

        resp = self.cli.get("/Marti/api/video/feed1", headers=AUTH)
        self.assertEqual(resp.status_code, 200)

        resp = self.cli.delete("/Marti/api/video/feed1", headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        resp = self.cli.get("/Marti/api/video/feed1", headers=AUTH)
        self.assertEqual(resp.status_code, 404)


class TestCITrap(DPSTestcase):
    def _report_zip(self):
        buff = BytesIO()
        with zipfile.ZipFile(buff, "w") as zfp:
            zfp.writestr(
                "report.xml",
                '<report id="rpt-1" title="Test Report" type="spot" />',
            )
        return buff.getvalue()

    def test_flow(self):
        payload = self._report_zip()

        # missing clientUid
        resp = self.cli.post("/Marti/api/citrap", headers=AUTH, data=payload)
        self.assertEqual(resp.status_code, 400)

        resp = self.cli.post(
            "/Marti/api/citrap?clientUid=ANDROID-1", headers=AUTH, data=payload
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.get_json(), {"id": "rpt-1"})

        resp = self.cli.get("/Marti/api/citrap", headers=AUTH)
        body = resp.get_json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["id"], "rpt-1")

        resp = self.cli.get("/Marti/api/citrap/rpt-1", headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_data(), payload)

        resp = self.cli.delete("/Marti/api/citrap/rpt-1", headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        resp = self.cli.get("/Marti/api/citrap/rpt-1", headers=AUTH)
        self.assertEqual(resp.status_code, 404)


class TestTLS(DPSTestcase):
    def _csr(self, cn="device1"):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        return (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
            .sign(key, hashes.SHA256())
        )

    def test_enrollment_disabled(self):
        csr = self._csr()
        resp = self.cli.post(
            "/Marti/api/tls/signClient/v2",
            data=csr.public_bytes(serialization.Encoding.PEM),
        )
        self.assertEqual(resp.status_code, 404)

    def test_tls_config(self):
        resp = self.cli.get("/Marti/api/tls/config")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"certificateConfig", resp.get_data())

    def test_sign_client_v2(self):
        app_config.set("dp_server", "enrollment_token", "sekrit")
        csr = self._csr()

        # no token -> 403
        resp = self.cli.post(
            "/Marti/api/tls/signClient/v2",
            data=csr.public_bytes(serialization.Encoding.PEM),
        )
        self.assertEqual(resp.status_code, 403)

        resp = self.cli.post(
            "/Marti/api/tls/signClient/v2",
            headers={"Authorization": "Bearer sekrit"},
            data=csr.public_bytes(serialization.Encoding.PEM),
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertIn("signedCert", body)
        self.assertIn("ca0", body)
        cert = x509.load_pem_x509_certificate(body["signedCert"].encode())
        self.assertEqual(
            cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value,
            "device1",
        )

        # XML variant
        resp = self.cli.post(
            "/Marti/api/tls/signClient/v2?token=sekrit",
            headers={"Accept": "application/xml"},
            data=csr.public_bytes(serialization.Encoding.PEM),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"<enrollment>", resp.get_data())

    def test_profile_204(self):
        resp = self.cli.get("/Marti/api/tls/profile/enrollment", headers=AUTH)
        self.assertEqual(resp.status_code, 204)
        resp = self.cli.get("/Marti/api/device/profile/connection", headers=AUTH)
        self.assertEqual(resp.status_code, 204)
        resp = self.cli.get("/api/connection", headers=AUTH)
        self.assertEqual(resp.status_code, 204)


class TestKML(DPSTestcase):
    EVT = (
        '<event version="2.0" uid="u1" type="a-f-G-U-C" how="m-g" '
        'time="2026-09-19T12:00:00.000Z" start="2026-09-19T12:00:00.000Z" '
        'stale="2026-09-19T13:00:00.000Z">'
        '<point lat="1.0" lon="2.0" hae="0" ce="9" le="9"/>'
        '<detail><contact callsign="TEST"/></detail></event>'
    )

    @mock.patch("taky.util.mgmt.cot_all", return_value=[EVT])
    def test_export_kml(self, _mock):
        resp = self.cli.get("/Marti/ExportMissionKML?uid=u1", headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"gx:Track", resp.get_data())
        self.assertIn(b"2.0 1.0", resp.get_data())

        resp = self.cli.get("/Marti/ExportMissionKML?uid=u1&format=kmz", headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        with zipfile.ZipFile(BytesIO(resp.get_data())) as zfp:
            self.assertIn("doc.kml", zfp.namelist())

        # filtered out by uid
        resp = self.cli.get("/Marti/ExportMissionKML?uid=other", headers=AUTH)
        self.assertNotIn(b"gx:Track", resp.get_data())

    @mock.patch("taky.util.mgmt.broadcast", return_value={"routed": "u1"})
    def test_tracks_kml(self, mock_broadcast):
        kml = (
            b'<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            b"<Placemark><TimeStamp><when>2026-09-19T12:00:00Z</when></TimeStamp>"
            b"<Point><coordinates>2.0,1.0,0</coordinates></Point></Placemark>"
            b"</Document></kml>"
        )
        resp = self.cli.post(
            "/Marti/TracksKML?uid=u1&callsign=TEST", headers=AUTH, data=kml
        )
        self.assertEqual(resp.status_code, 200)
        mock_broadcast.assert_called_once()
        self.assertIn('uid="u1"', mock_broadcast.call_args[0][0])

        # missing required params
        resp = self.cli.post("/Marti/TracksKML", headers=AUTH, data=kml)
        self.assertEqual(resp.status_code, 400)
