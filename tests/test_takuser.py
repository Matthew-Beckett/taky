import unittest as ut
from datetime import datetime as dt
from datetime import timedelta

from lxml import etree
from dateutil.parser import isoparse

from taky.cot import models
from . import elements_equal

XML_S = b'<event version="2.0" uid="TEST-deadbeef" type="a" how="m-g" time="2021-03-11T15:49:07.138Z" start="2021-03-11T15:49:07.138Z" stale="2021-03-12T15:49:07.138Z"><point lat="0.000000" lon="0.000000" hae="0.0" ce="9999999.0" le="9999999.0"/><detail><takv os="Android" version="10" device="Some Device" platform="python unittest"/><status battery="83"/><uid Droid="JENNY"/><contact callsign="JENNY" endpoint="*:-1:stcp" phone="800-867-5309"/><__group role="Team Member" name="Cyan"/><track course="90.1" speed="10.3"/></detail></event>'


class TAKUserTestcase(ut.TestCase):
    def setUp(self):
        elm = etree.fromstring(XML_S)
        self.answer = elm.find("detail")

    def test_as_element(self):
        tak_u = models.TAKUser(None)

        tak_u.callsign = "JENNY"
        tak_u.marker = "a"
        tak_u.group = models.Teams.CYAN
        tak_u.role = "Team Member"

        tak_u.phone = "800-867-5309"  # hahahaha
        tak_u.endpoint = "*:-1:stcp"

        tak_u.course = 90.1
        tak_u.speed = 10.3
        tak_u.battery = "83"

        tak_u.device = models.TAKDevice(
            os="Android", version="10", device="Some Device", platform="python unittest"
        )

        self.assertTrue(elements_equal(self.answer, tak_u.as_element))

    def test_no_group_is_type(self):
        """
        A SA detail without __group must still classify as TAKUser so
        group-less clients can be identified and receive directed messages.
        """
        elm = etree.fromstring(
            '<detail><takv os="30" version="1" device="d" platform="ATAK-CIV"/>'
            '<contact callsign="NOGRP" endpoint="*:-1:stcp"/></detail>'
        )
        self.assertTrue(models.TAKUser.is_type({"takv", "contact"}))

        user = models.TAKUser.from_elm(elm, uid="x")
        self.assertEqual(user.callsign, "NOGRP")
        self.assertIsNone(user.group)

    def test_track_missing_attrs(self):
        """
        A <track> element missing course/speed must not cause the whole
        event to be rejected.
        """
        elm = etree.fromstring(
            '<detail><takv os="30" version="1" device="d" platform="ATAK-CIV"/>'
            '<contact callsign="X"/><track/></detail>'
        )
        user = models.TAKUser.from_elm(elm, uid="x")
        self.assertIsNone(user.course)
        self.assertIsNone(user.speed)
