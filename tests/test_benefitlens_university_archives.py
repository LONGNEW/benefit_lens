import sys
from pathlib import Path
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from discover_benefitlens_university_archives import (
    extract_text, in_scope, institution_candidates, normalize, observed_links, scoped_capture,
)
from run_benefitlens_korea import Held


class UniversityArchiveTests(unittest.TestCase):
    def setUp(self):
        self.source = {'id': 'official-reports', 'allowed_hosts': ['university.example'],
            'article_pattern': r'/reports\.do\?[^#]*articleNo=\d+', 'max_depth': 2}
        self.task = {'url': 'https://university.example/reports.do', 'depth': 0,
                     'original': {'sha256': 'listing-source-hash'}}

    def test_attachment_needs_no_report_word_inside_declared_archive(self):
        rows = observed_links(b'<main><a href="/assets/toronto.pdf">Toronto 2025</a></main>', self.task, self.source)
        self.assertEqual(rows[0]['kind'], 'attachment')
        self.assertEqual(rows[0]['listing_original']['sha256'], 'listing-source-hash')

    def test_templates_are_not_experience_documents(self):
        raw = '<main><a href="/vorlage.pdf">Vorlage</a><a href="/form.pdf">보고서 양식</a></main>'.encode()
        self.assertEqual(observed_links(raw, self.task, self.source), [])

    def test_article_identity_excludes_listing_offset(self):
        raw = b'<main><a href="?mode=view&amp;articleNo=7&amp;article.offset=20">Story</a></main>'
        rows = observed_links(raw, self.task, self.source)
        self.assertEqual(rows[0]['kind'], 'article')
        self.assertEqual(rows[0]['url'], 'https://university.example/reports.do?articleNo=7&mode=view')

    def test_only_observed_same_board_pagination(self):
        raw = b'<main><a href="?page=2">2</a><a href="/news?page=2">Other board</a></main>'
        rows = observed_links(raw, self.task, self.source)
        self.assertEqual([r['url'] for r in rows], ['https://university.example/reports.do?page=2'])
        self.assertEqual(rows[0]['depth'], 0)

    def test_navigation_and_off_origin_attachments_not_followed(self):
        raw = b'<nav><a href="/nav.pdf">PDF</a></nav><main><a href="https://other.example/a.pdf">x</a></main>'
        self.assertEqual(observed_links(raw, self.task, self.source), [])

    def test_blocked_suffix_and_private_address_rejected(self):
        self.assertIsNone(normalize('https://www.helsinki.fi/report.pdf'))
        self.assertIsNone(normalize('https://127.0.0.1/report.pdf'))
        self.assertFalse(in_scope('https://university.example.evil.com/report.pdf', self.source))

    def test_source_specific_attachment_scope(self):
        source = dict(self.source, attachment_include_pattern='/student-reports/')
        raw = b'<main><a href="/fees.pdf">Fees</a><a href="/student-reports/a.pdf">Report</a></main>'
        self.assertEqual(len(observed_links(raw, self.task, source)), 1)

    def test_authentication_redirect_never_followed(self):
        ledger = Mock()
        ledger.request.return_value = (b'', {'status':302,
            'headers': {'location': 'https://university.example/etc/total_login.do;SESSION=abc'}})
        with self.assertRaisesRegex(Held, 'authentication route'):
            scoped_capture(ledger, 'https://university.example/report.pdf', self.source)
        self.assertEqual(ledger.request.call_count, 1)

    def test_login_html_is_not_a_staged_report(self):
        ledger = Mock()
        ledger.request.return_value = (b'<html><title>Login</title><input type="password"></html>', {'status':200})
        with self.assertRaisesRegex(Held, 'requires authentication'):
            scoped_capture(ledger, 'https://university.example/story', self.source)

    def test_login_shell_with_external_sso_and_no_password_field_is_held(self):
        ledger = Mock()
        ledger.request.return_value = (b'<html><title>University | Sign in</title><iframe></iframe></html>', {'status':200})
        with self.assertRaisesRegex(Held, 'requires authentication'):
            scoped_capture(ledger, 'https://university.example/story', self.source)

    def test_permission_denial_in_public_page_shell_is_held(self):
        ledger = Mock()
        ledger.request.return_value = ('<title>체험수기</title><article id="_contentBuilder">접근 권한이 없습니다.</article>'.encode(), {'status':200})
        with self.assertRaisesRegex(Held, 'denies article access'):
            scoped_capture(ledger, 'https://university.example/story', self.source)

    def test_download_html_does_not_count_as_original_pdf(self):
        with self.assertRaisesRegex(Held, 'HTML response'):
            extract_text(b'<main>Document temporarily unavailable</main>',
                         {'kind':'attachment'}, self.source, Path('/tmp'))

    def test_registry_round_robin_and_candidate_authority(self):
        registry = {'revision':'pinned', 'original':{'sha256':'abc'}, 'records':[
            {'name':'K1', 'alpha_two_code':'KR', 'domains':['k1.ac.kr'], 'web_pages':['http://k1.ac.kr/']},
            {'name':'K2', 'alpha_two_code':'KR', 'domains':['k2.ac.kr'], 'web_pages':['https://k2.ac.kr/']},
            {'name':'D1', 'alpha_two_code':'DE', 'domains':['d1.de'], 'web_pages':['https://d1.de/']}]}
        rows = institution_candidates(registry, ['KR', 'DE'])
        self.assertEqual([r['institution'] for r in rows], ['K1','D1','K2'])
        self.assertTrue(rows[0]['https_transport_upgrade'])
        self.assertFalse(any(r['official_publisher'] for r in rows))


if __name__ == '__main__':
    unittest.main()
