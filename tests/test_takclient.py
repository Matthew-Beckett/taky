import os
import unittest as ut
from unittest import mock

from taky import cot
from taky.cot import models
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

    def tearDown(self):
        self.mock_sock.stop()
