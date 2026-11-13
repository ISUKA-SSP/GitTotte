import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs
from githug.__main__ import App
from githug import __version__


class ReportingTests(unittest.TestCase):
    def test_report_opens_reviewable_issue_form_with_only_version_and_os(self):
        with patch('platform.platform', return_value='Windows-test'), patch('webbrowser.open') as browser:
            App.report_issue(SimpleNamespace())
        browser.assert_called_once()
        url = urlparse(browser.call_args.args[0])
        self.assertEqual(url.netloc, 'github.com')
        self.assertEqual(url.path, '/ISUKA-SSP/GitTotte/issues/new')
        fields=parse_qs(url.query)
        self.assertEqual(set(fields), {'title','body'})
        self.assertEqual(fields['title'], ['[不具合] '])
        body=fields['body'][0]
        self.assertIn(__version__,body)
        self.assertIn('Windows-test',body)
        self.assertIn('再現手順',body)
