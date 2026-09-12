import struct

from django.test import TestCase


class AuthenticationRouteTests(TestCase):
    def test_auth_failure_is_binary_result(self):
        response = self.client.get("/nbapi/title-index")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.content[:4], b"RSLT")
        self.assertEqual(struct.unpack_from("<I", response.content, 16)[0], (7 << 16) | 1)
