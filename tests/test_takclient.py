import os
import json
import unittest as ut
from unittest import mock
from datetime import datetime as dt
from datetime import timedelta, timezone

from taky import cot
from taky.cot import models
from taky.cot.mgmt import MgmtClient
from taky.config import load_config, app_config

from .test_cot_event import XML_S


class TAKClientTest(ut.TestCase):
    def setUp(self):
        load_config(os.devnull)
        app_config.set("taky", "redis", "false")
        router = cot.COTRouter()
        self.tk = cot.TAKClient(cbs={"route": router.route})

    def test_ident(self):
        self.tk.feed(XML_S)

        self.assertEqual(self.tk.user.callsign, "JENNY")
        self.assertEqual(self.tk.user.uid, "ANDROID-deadbeef")
        self.assertEqual(self.tk.user.device.os, "29")
        self.assertEqual(self.tk.user.device.device, "Some Android Device")
        self.assertEqual(self.tk.user.group, cot.Teams.CYAN)
        self.assertEqual(self.tk.user.battery, "78")
        self.assertEqual(self.tk.user.role, "Team Member")

    def test_feed_bounded_parser_tree(self):
        """
        Regression test: feed() must prune processed elements from the pull
        parser's primed <root>, or the tree grows with every event received.
        """
        seen = []
        orig = models.Event.from_elm

        def spy(elm):
            seen.append(elm)
            return orig(elm)

        with mock.patch.object(models.Event, "from_elm", staticmethod(spy)):
            self.tk.feed(XML_S * 100)

        self.assertEqual(len(seen), 100)
        self.assertLessEqual(len(seen[-1].getparent()), 1)
        self.assertIsNone(seen[0].getparent())

    def test_ident_no_group(self):
        """
        Clients without a __group element must still be identified --
        otherwise directed messages and marti routing can never reach them.
        """
        xml = b'<event version="2.0" uid="ANDROID-nogroup" type="a-f-G-U-C" how="m-g" time="2021-02-27T20:32:24.771Z" start="2021-02-27T20:32:24.771Z" stale="2021-02-27T20:38:39.771Z"><point lat="1.234567" lon="-3.141592" hae="-25.7" ce="9.9" le="9999999.0"/><detail><takv os="29" version="4.0.0.0" device="dev" platform="ATAK-CIV"/><contact endpoint="*:-1:stcp" callsign="NOGROUP"/><uid Droid="NOGROUP"/></detail></event>'
        self.tk.feed(xml)

        self.assertIsNotNone(self.tk.user)
        self.assertEqual(self.tk.user.callsign, "NOGROUP")
        self.assertEqual(self.tk.user.uid, "ANDROID-nogroup")
        self.assertIsNone(self.tk.user.group)

    def test_pong_uid_correlation(self):
        """
        The pong UID must correlate to the incoming ping UID (<uid>-pong),
        matching OpenTAKServer and TAK Server behaviour.
        """
        self.tk.send_event = mock.Mock()
        xml = b'<event version="2.0" uid="PING-1" type="t-x-c-t" how="h-g-i-g-o" time="2021-02-27T20:32:24.771Z" start="2021-02-27T20:32:24.771Z" stale="2021-02-27T20:38:39.771Z"><point lat="0" lon="0" hae="0" ce="9999999" le="9999999"/></event>'
        self.tk.feed(xml)

        pong = self.tk.send_event.call_args[0][0]
        self.assertEqual(pong.etype, "t-x-c-t-r")
        self.assertEqual(pong.uid, "PING-1-pong")

    def test_takp_q_deny(self):
        """
        t-x-takp-q (TAK Protocol negotiation request) must be answered with
        t-x-takp-v denying the request, and must not be routed to clients.
        """
        self.tk.send_event = mock.Mock()
        self.tk.route = mock.Mock()
        xml = b'<event version="2.0" uid="PROTO-Q" type="t-x-takp-q" how="m-g" time="2021-02-27T20:32:24.771Z" start="2021-02-27T20:32:24.771Z" stale="2021-02-27T20:38:39.771Z"><point lat="0" lon="0" hae="0" ce="9999999" le="9999999"/><detail><TakControl><TakRequest version="1"/></TakControl></detail></event>'
        self.tk.feed(xml)

        self.tk.route.assert_not_called()
        evt = self.tk.send_event.call_args[0][0]
        self.assertEqual(evt.etype, "t-x-takp-v")
        resp = evt.detail.elm.find("TakControl/TakResponse")
        self.assertEqual(resp.get("status"), "false")


class SocketTAKClientTest(ut.TestCase):
    def setUp(self):
        load_config(os.devnull)
        app_config.set("taky", "redis", "false")
        router = cot.COTRouter()

        self.mock_sock = mock.patch("socket.socket")
        self.sock = self.mock_sock.start()
        self.sock.recv.return_value = b"</invalid>"
        self.sock.getpeername.return_value = (
            "127.0.0.1",
            12345,
        )

        self.tk = cot.SocketTAKClient(sock=self.sock, use_ssl=False, router=router)

    def test_invalid_xml(self):
        self.tk.socket_rx()
        self.sock.close.assert_called()

    def test_out_buff_overflow(self):
        """
        A client that stops reading must be disconnected once its transmit
        buffer fills, rather than buffering events without bound.
        """
        self.tk.MAX_OUT_BUFF = 1024
        now = dt.now(timezone.utc).replace(tzinfo=None)
        evt = models.Event(
            uid="test-overflow",
            etype="a-f-G-U-C",
            how="m-g",
            time=now,
            start=now,
            stale=now + timedelta(seconds=60),
        )

        for _ in range(10):
            self.tk.send_event(evt)

        self.sock.close.assert_called()
        self.assertLessEqual(len(self.tk.out_buff), self.tk.MAX_OUT_BUFF)

    def tearDown(self):
        self.mock_sock.stop()


class MgmtClientTest(ut.TestCase):
    def setUp(self):
        self.mock_sock = mock.patch("socket.socket")
        self.sock = self.mock_sock.start()
        self.sock.getpeername.return_value = ("127.0.0.1", 12345)

        self.cli = MgmtClient(sock=self.sock, use_ssl=False, server=mock.Mock())

    def test_ping(self):
        self.cli.feed(b'{"cmd": "ping"}\0')
        self.assertIn(b'"pong"', self.cli.out_buff)

    def test_rx_buff_overflow(self):
        """
        A client that streams data without a NUL terminator must be
        disconnected rather than growing the RX buffer without bound.
        """
        self.cli.MAX_RX_BUFF = 64
        self.cli.feed(b'{"cmd": "p')
        self.cli.feed(b"x" * 100)

        self.sock.close.assert_called()
        self.assertGreater(len(self.cli.buff), self.cli.MAX_RX_BUFF)

    def test_broadcast_route_exception(self):
        """
        A route() failure on a broadcast event must return an error to the
        caller, not propagate and kill the server loop.
        """
        self.cli.server.router.route.side_effect = ValueError("boom")
        xml = '<event version="2.0" uid="BC-1" type="a-f-G" how="m-g" time="2021-02-27T20:32:24.771Z" start="2021-02-27T20:32:24.771Z" stale="2021-02-27T20:38:39.771Z"><point lat="1" lon="1" hae="0" ce="1" le="1"/></event>'
        self.cli.feed(json.dumps({"cmd": "broadcast", "xml": xml}).encode() + b"\0")

        self.assertIn(b'"error"', self.cli.out_buff)

    def tearDown(self):
        self.mock_sock.stop()
