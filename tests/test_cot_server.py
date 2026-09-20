import unittest as ut
from unittest import mock

from taky.cot.server import COTServer


class MonPacketTest(ut.TestCase):
    def test_mon_packet_ignores_mgmt_clients(self):
        """
        Regression test: self.clients contains MgmtClient objects which do
        not have a .monitor attribute. Accessing client.monitor raised
        AttributeError, which aborted processing of the received event.
        """
        srv = mock.Mock()
        mgmt = mock.Mock(spec=["send_event"])
        monitor = mock.Mock()
        monitor.monitor = True
        srv.clients = {mock.Mock(): mgmt, mock.Mock(): monitor}

        evt = mock.Mock()
        COTServer.mon_packet(srv, evt)

        monitor.send_event.assert_called_once_with(evt)
        mgmt.send_event.assert_not_called()


class ClientConnectTest(ut.TestCase):
    def test_client_connect_sends_proto_support(self):
        """
        New clients must receive the t-x-takp-v protocol announcement so
        clients expecting a server greeting know the stream is alive.
        """
        srv = mock.Mock()
        client = mock.Mock()
        client.peer_cert = None
        client.monitor = False

        COTServer.client_connect(srv, client)

        client.proto_support.assert_called_once()
        srv.router.send_persist.assert_called_once_with(client)

    def test_client_connect_monitor_skips_greeting(self):
        """
        Monitor clients are not TAK protocol peers -- they should still get
        persistence objects, but not the negotiation event.
        """
        srv = mock.Mock()
        client = mock.Mock()
        client.peer_cert = None
        client.monitor = True

        COTServer.client_connect(srv, client)

        client.proto_support.assert_not_called()
        srv.router.send_persist.assert_called_once_with(client)
