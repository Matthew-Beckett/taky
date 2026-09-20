import logging
import time
import json

from lxml import etree

from . import models
from .client import SocketClient, TAKClient


class MgmtClient(SocketClient):
    """
    MgmtClient implements a socket client that handles connections to taky's
    management socket. This socket communicates with null terminated JSON,
    in the style of {"cmd": "..."}\\0
    """

    MAX_RX_BUFF = 4096

    def __init__(self, server, **kwargs):
        self.lgr = logging.getLogger(self.__class__.__name__)
        self.server = server
        self.buff = b""
        super().__init__(**kwargs)

    @property
    def has_data(self):
        self.handle_rx()
        return super().has_data

    def feed(self, data):
        self.buff += data
        self.handle_rx()

        if len(self.buff) > self.MAX_RX_BUFF:
            self.disconnect("RX buffer overflow")

    def handle_rx(self):
        try:
            idx = self.buff.index(b"\0")
        except ValueError:
            return

        msg = self.buff[0:idx]
        self.buff = self.buff[idx + 1 :]

        try:
            msg = msg.decode()
            msg = json.loads(msg)

            if msg.get("cmd") == "status":
                ret = self.status()
            elif msg.get("cmd") == "ping":
                ret = {"pong": "taky"}
            elif msg.get("cmd") == "kickban":
                ret = self.kickban(msg.get("user"))
            elif msg.get("cmd") == "cot_get":
                ret = self.cot_get(msg.get("uid"))
            elif msg.get("cmd") == "cot_all":
                ret = self.cot_all()
            elif msg.get("cmd") == "broadcast":
                ret = self.broadcast(msg.get("xml"))
            else:
                ret = {"error": f"Invalid cmd: {msg.get('cmd')}"}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            ret = {"error": str(exc)}

        ret = json.dumps(ret)
        self.queue_tx(ret.encode() + b"\0")

    def kickban(self, user):
        cdb = self.server.cert_db
        revoked_sns = []

        for cert in cdb.get_certificates_by_name(user):
            if cert["status"] == "R":
                continue

            cdb.revoke_certificate(cert["serial_num"])
            revoked_sns.append(cert["serial_num"])
            self.lgr.info(
                f"Revoked certificate for {user} (SN: {cert['serial_num']:040x})"
            )

            for client in list(self.server.clients.values()):
                if not client.peer_cert:
                    continue

                if int(client.peer_cert.get("serialNumber"), 16) == cert["serial_num"]:
                    self.lgr.info(f"Kicking user {user} from server")
                    self.server.client_disconnect(client, "Banned")

        return {"revoked_sns": revoked_sns}

    def status(self):
        ret = {
            "uptime": time.time() - self.server.started,
            "num_clients": 0,
            "clients": [],
        }
        for client in self.server.clients.values():
            if not isinstance(client, TAKClient):
                continue

            ret["num_clients"] += 1
            cli_meta = {
                "last_rx": client.last_rx,
                "num_rx": client.num_rx,
                "connected": client.connected,
            }
            if client.user:
                if isinstance(client, SocketClient):
                    cli_meta["ip"] = client.addr[0]
                    cli_meta["port"] = client.addr[1]
                    cli_meta["protocol"] = "ssl" if client.ssl else "tcp"
                cli_meta["uid"] = client.user.uid
                cli_meta["callsign"] = client.user.callsign
                cli_meta["group"] = str(client.user.group)
                cli_meta["battery"] = client.user.battery
                cli_meta["device"] = client.user.device.device
                cli_meta["os"] = client.user.device.os
                cli_meta["version"] = client.user.device.version
                cli_meta["platform"] = client.user.device.platform
                cli_meta["role"] = client.user.role
                if client.user.group is not None:
                    cli_meta["team"] = client.user.group.value
                cli_meta["takv"] = " ".join(
                    p
                    for p in [
                        client.user.device.platform,
                        client.user.device.version,
                    ]
                    if p
                )
                if isinstance(client, SocketClient) and client.peer_cert:
                    try:
                        cli_meta["username"] = dict(
                            i
                            for subtuple in client.peer_cert.get("subject")
                            for i in subtuple
                        ).get("commonName")
                    except (KeyError, TypeError):
                        pass
            else:
                cli_meta["anonymous"] = True

            ret["clients"].append(cli_meta)

        return ret

    def cot_get(self, uid):
        """
        Return the tracked event for a UID as an XML string
        """
        if not uid:
            return {"error": "Must specify uid"}

        evt = self.server.router.persist.get_event(uid)
        if evt is None:
            return {"events": []}

        try:
            xml = etree.tostring(evt.as_element).decode()
            return {"events": [xml]}
        except (TypeError, AttributeError) as exc:
            return {"error": f"Unable to serialize event: {exc}"}

    def cot_all(self):
        """
        Return all tracked events as a list of XML strings
        """
        events = []
        for evt in self.server.router.persist.get_all():
            try:
                events.append(etree.tostring(evt.as_element).decode())
            except (TypeError, AttributeError) as exc:
                self.lgr.warning("Unable to serialize event %s: %s", evt, exc)

        return {"events": events}

    def broadcast(self, xml):
        """
        Route an event (expressed as an XML string) through the COT router
        """
        if not xml:
            return {"error": "Must specify xml"}

        try:
            parser = etree.XMLParser(resolve_entities=False)
            parser.feed(xml.encode() if isinstance(xml, str) else xml)
            elm = parser.close()

            evt = models.Event.from_elm(elm)
        except (etree.XMLSyntaxError, models.UnmarshalError) as exc:
            return {"error": f"Unable to parse event: {exc}"}

        try:
            self.server.router.route(None, evt)
        except Exception as exc:  # pylint: disable=broad-except
            self.lgr.error("Unable to route broadcast event: %s", exc, exc_info=exc)
            return {"error": "Unable to route event"}

        return {"routed": evt.uid}
