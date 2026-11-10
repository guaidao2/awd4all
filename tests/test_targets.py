import unittest
from pathlib import Path

from awd4all.targets import TargetSpecError, parse_targets


class TargetParsingTests(unittest.TestCase):
    def test_single_host_with_default_port(self):
        targets = parse_targets("10.0.0.1", default_port=22)
        self.assertEqual([t.label for t in targets], ["10.0.0.1:22"])

    def test_cidr_skips_network_and_broadcast(self):
        targets = parse_targets("192.168.1.0/29")
        hosts = [t.host for t in targets]
        self.assertNotIn("192.168.1.0", hosts)
        self.assertNotIn("192.168.1.7", hosts)
        self.assertEqual(len(targets), 6)

    def test_full_range(self):
        targets = parse_targets("10.0.0.1-10.0.0.4")
        self.assertEqual([t.host for t in targets],
                         ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"])

    def test_last_octet_range(self):
        targets = parse_targets("10.1.2.10-12")
        self.assertEqual([t.host for t in targets], ["10.1.2.10", "10.1.2.11", "10.1.2.12"])

    def test_wildcard_covers_last_octet(self):
        targets = parse_targets("10.0.0.*")
        self.assertEqual(len(targets), 254)
        self.assertEqual(targets[0].host, "10.0.0.1")
        self.assertEqual(targets[-1].host, "10.0.0.254")

    def test_ports(self):
        targets = parse_targets("10.0.0.1:22, 10.0.0.2:2222")
        self.assertEqual([t.label for t in targets], ["10.0.0.1:22", "10.0.0.2:2222"])

    def test_cidr_with_port(self):
        targets = parse_targets("10.0.0.0/30:8080")
        self.assertEqual([t.label for t in targets], ["10.0.0.1:8080", "10.0.0.2:8080"])

    def test_ipv6_with_port(self):
        targets = parse_targets("[::1]:22")
        self.assertEqual(targets[0].host, "::1")
        self.assertEqual(targets[0].port, 22)

    def test_dedupe(self):
        targets = parse_targets("10.0.0.1 10.0.0.1 10.0.0.1:23")
        self.assertEqual(len(targets), 2)

    def test_file_input(self):
        path = Path("tests/data/targets.txt")
        targets = parse_targets(f"@{path}", default_port=80)
        self.assertEqual([t.label for t in targets],
                         ["10.9.9.1:80", "10.9.9.2:80", "10.9.9.3:80"])

    def test_empty(self):
        self.assertEqual(parse_targets("   "), [])

    def test_bad_range_raises(self):
        with self.assertRaises(TargetSpecError):
            parse_targets("10.0.0.300-400")

    def test_missing_file_raises(self):
        with self.assertRaises(TargetSpecError):
            parse_targets("@nope_not_here.txt")


if __name__ == "__main__":
    unittest.main()
