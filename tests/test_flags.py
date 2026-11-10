import unittest

from awd4all.plugins.base import find_flags, normalize_flag_pattern, parse_credentials


class FlagParsingTests(unittest.TestCase):
    def test_default_flag(self):
        self.assertEqual(find_flags("xxx flag{abc123} yyy"), ["flag{abc123}"])

    def test_multiple_and_dedupe(self):
        text = "flag{aaaa} flag{aaaa} flag{bbbb}"
        self.assertEqual(find_flags(text), ["flag{aaaa}", "flag{bbbb}"])

    def test_custom_regex(self):
        self.assertEqual(find_flags("AWD-2024-xyz", [r"AWD-\d+-\w+"]), ["AWD-2024-xyz"])

    def test_chinese_flag_value(self):
        self.assertEqual(find_flags("flag{中文也行}"), ["flag{中文也行}"])

    def test_no_flag(self):
        self.assertEqual(find_flags("nothing here"), [])


class CredentialParsingTests(unittest.TestCase):
    def test_colon_format(self):
        self.assertEqual(parse_credentials("root:root\nadmin:admin"),
                         [("root", "root"), ("admin", "admin")])

    def test_space_format(self):
        self.assertEqual(parse_credentials("root toor"), [("root", "toor")])

    def test_comments_and_blanks(self):
        self.assertEqual(parse_credentials("# comment\n\nroot:root\n  \n"),
                         [("root", "root")])

    def test_dedupe(self):
        self.assertEqual(parse_credentials("root:root\nroot:root"), [("root", "root")])


class FlagPatternTests(unittest.TestCase):
    def test_prefix_with_brace(self):
        self.assertEqual(normalize_flag_pattern("flag{"), r"flag\{[^{}\s]{1,300}\}")

    def test_prefix_without_brace(self):
        self.assertEqual(normalize_flag_pattern("flag"), r"flag\{[^{}\s]{1,300}\}")

    def test_uppercase_prefix(self):
        self.assertEqual(normalize_flag_pattern("CTF{"), r"CTF\{[^{}\s]{1,300}\}")

    def test_half_regex_style(self):
        self.assertEqual(normalize_flag_pattern("flag{.*}"), r"flag\{[^{}\s]{1,300}\}")

    def test_full_regex_untouched(self):
        pattern = r"flag\{[a-z0-9]{8}\}"
        self.assertEqual(normalize_flag_pattern(pattern), pattern)

    def test_prefix_finds_flag(self):
        self.assertEqual(find_flags("xx flag{abc123} yy", ["flag{"]), ["flag{abc123}"])

    def test_half_regex_not_greedy(self):
        text = "flag{abc123} trailing words here"
        self.assertEqual(find_flags(text, ["flag{.*}"]), ["flag{abc123}"])

    def test_prefix_requires_braces(self):
        self.assertEqual(find_flags("MYFLAG no braces", ["MYFLAG"]), [])

    def test_invalid_regex_ignored(self):
        self.assertEqual(find_flags("nothing", ["["]), [])
        self.assertEqual(find_flags("flag{abc123}", ["["]), ["flag{abc123}"])


if __name__ == "__main__":
    unittest.main()
