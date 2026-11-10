import unittest

from awd4all.plugins.http_poc import (
    build_request,
    parse_curl,
    parse_raw,
    parse_request,
    parse_url,
    render_placeholders,
)
from awd4all.targets import Target


class CurlParsingTests(unittest.TestCase):
    def test_post_with_json(self):
        parsed = parse_curl(
            "curl -X POST 'http://1.2.3.4:8080/api/login' "
            "-H 'Content-Type: application/json' -d '{\"user\":\"admin\"}'"
        )
        self.assertEqual(parsed.method, "POST")
        self.assertEqual(parsed.path, "/api/login")
        self.assertEqual(parsed.body, '{"user":"admin"}')
        self.assertEqual(parsed.scheme, "http")
        self.assertEqual(parsed.source, "curl")
        self.assertIn(("Content-Type", "application/json"), parsed.headers)
        self.assertTrue(any(name.lower() == "host" for name, _ in parsed.headers))

    def test_default_method_is_get(self):
        parsed = parse_curl("curl 'https://10.0.0.1/flag?a=1'")
        self.assertEqual(parsed.method, "GET")
        self.assertEqual(parsed.path, "/flag?a=1")
        self.assertEqual(parsed.scheme, "https")

    def test_get_with_data_moves_to_query(self):
        parsed = parse_curl("curl -G 'http://10.0.0.1/search' -d 'q=flag'")
        self.assertEqual(parsed.method, "GET")
        self.assertEqual(parsed.path, "/search?q=flag")
        self.assertEqual(parsed.body, "")

    def test_multiple_data_joined(self):
        parsed = parse_curl("curl 'http://10.0.0.1/x' -d a=1 -d b=2")
        self.assertEqual(parsed.body, "a=1&b=2")

    def test_line_continuation(self):
        parsed = parse_curl(
            "curl -X POST 'http://10.0.0.1/login' \\\n"
            "  -H 'X-Test: 1' \\\n"
            "  -d 'u=admin'"
        )
        self.assertEqual(parsed.method, "POST")
        self.assertEqual(parsed.body, "u=admin")
        self.assertIn(("X-Test", "1"), parsed.headers)

    def test_data_equals_form(self):
        parsed = parse_curl("curl 'http://10.0.0.1/x' --data='a=b'")
        self.assertEqual(parsed.body, "a=b")
        self.assertEqual(parsed.method, "POST")

    def test_basic_auth(self):
        parsed = parse_curl("curl -u admin:secret 'http://10.0.0.1/x'")
        auth = [value for name, value in parsed.headers if name.lower() == "authorization"]
        self.assertEqual(len(auth), 1)
        self.assertTrue(auth[0].startswith("Basic "))

    def test_cookie(self):
        parsed = parse_curl("curl -b 'sid=abc' 'http://10.0.0.1/x'")
        cookies = [value for name, value in parsed.headers if name.lower() == "cookie"]
        self.assertEqual(cookies, ["sid=abc"])

    def test_form_upload_rejected(self):
        with self.assertRaises(ValueError):
            parse_curl("curl 'http://10.0.0.1/x' -F 'file=@a.txt'")

    def test_no_url(self):
        with self.assertRaises(ValueError):
            parse_curl("curl -s -k")


class RawRequestTests(unittest.TestCase):
    def test_crlf_request(self):
        parsed = parse_raw(
            "GET /admin HTTP/1.1\r\nHost: 10.0.0.1\r\nCookie: a=1\r\n\r\n"
        )
        self.assertEqual(parsed.method, "GET")
        self.assertEqual(parsed.path, "/admin")
        self.assertEqual(parsed.source, "raw")
        self.assertIn(("Cookie", "a=1"), parsed.headers)

    def test_lf_request_with_body(self):
        parsed = parse_raw("POST /api HTTP/1.1\nHost: 10.0.0.1\n\n{\"a\":1}")
        self.assertEqual(parsed.body, '{"a":1}')

    def test_bad_request_line(self):
        with self.assertRaises(ValueError):
            parse_raw("not a request")

    def test_parse_request_dispatch(self):
        self.assertEqual(parse_request("curl http://10.0.0.1/x").source, "curl")
        self.assertEqual(parse_request("GET /x HTTP/1.1\nHost: a\n\n").source, "raw")
        self.assertEqual(parse_request("http://10.0.0.1/x").source, "url")


class BuildRequestTests(unittest.TestCase):
    def test_host_replaced(self):
        parsed = parse_raw("GET /x HTTP/1.1\r\nHost: 1.2.3.4\r\n\r\n")
        payload, use_tls = build_request(parsed, Target("10.9.9.9", 8080), replace_host=True)
        text = payload.decode()
        self.assertIn("Host: 10.9.9.9:8080", text)
        self.assertFalse(use_tls)

    def test_host_kept_when_disabled(self):
        parsed = parse_raw("GET /x HTTP/1.1\r\nHost: virtual.example\r\n\r\n")
        payload, _ = build_request(parsed, Target("10.9.9.9", 8080), replace_host=False)
        self.assertIn("Host: virtual.example", payload.decode())

    def test_host_added_when_missing(self):
        parsed = parse_raw("GET /x HTTP/1.1\r\n\r\n")
        payload, _ = build_request(parsed, Target("10.9.9.9", 8080))
        self.assertIn("Host: 10.9.9.9:8080", payload.decode())

    def test_curl_port_parsed(self):
        self.assertEqual(parse_curl("curl 'http://10.0.0.1:8080/x'").port, 8080)

    def test_raw_host_port_parsed(self):
        parsed = parse_raw("GET /x HTTP/1.1\r\nHost: 10.0.0.1:8080\r\n\r\n")
        self.assertEqual(parsed.port, 8080)

    def test_fallback_port_goes_into_host(self):
        parsed = parse_url("http://10.0.0.1:8080/x")
        payload, _ = build_request(parsed, Target("10.9.9.9"), port=8080)
        self.assertIn("Host: 10.9.9.9:8080", payload.decode())

    def test_default_port_not_shown(self):
        parsed = parse_url("http://10.0.0.1/x")
        payload, _ = build_request(parsed, Target("10.9.9.9"), port=80)
        self.assertIn("Host: 10.9.9.9\r\n", payload.decode())

    def test_content_length_recomputed(self):
        parsed = parse_raw("POST /x HTTP/1.1\r\nHost: a\r\nContent-Length: 999\r\n\r\nhi")
        payload, _ = build_request(parsed, Target("10.9.9.9", 80))
        text = payload.decode()
        self.assertIn("Content-Length: 2", text)
        self.assertNotIn("999", text)

    def test_https_scheme(self):
        parsed = parse_url("https://10.0.0.1/x")
        _, use_tls = build_request(parsed, Target("10.9.9.9", 443))
        self.assertTrue(use_tls)

    def test_scheme_override(self):
        parsed = parse_url("http://10.0.0.1/x")
        _, use_tls = build_request(parsed, Target("10.9.9.9", 8443), scheme="https")
        self.assertTrue(use_tls)


class PlaceholderTests(unittest.TestCase):
    def test_placeholders_replaced(self):
        target = Target("10.9.9.9", 8080)
        text = render_placeholders("curl 'http://{{ip}}:{{port}}/flag' -H 'X-Host: {{host}}'", target)
        self.assertEqual(text, "curl 'http://10.9.9.9:8080/flag' -H 'X-Host: 10.9.9.9'")
        parsed = parse_curl(text)
        self.assertEqual(parsed.path, "/flag")


if __name__ == "__main__":
    unittest.main()
