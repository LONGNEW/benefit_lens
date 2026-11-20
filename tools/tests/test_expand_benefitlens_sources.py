from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from expand_benefitlens_sources import capture_sources, current_window, field_line, span


class FundingExpansionTests(unittest.TestCase):
    def test_multi_intake_uses_live_window(self):
        self.assertEqual(current_window('3 March 2026, 3 June 2026, 6 October 2026',
            '14 April 2026, 14 July 2026, 17 November 2026','2026-11-09'),
            ('2026-10-06','2026-11-17'))

    def test_expired_or_not_yet_open_is_not_current(self):
        for opening,closing in [('1 September 2026','8 November 2026'),
                                ('18 November 2026','1 December 2026')]:
            with self.assertRaises(ValueError):current_window(opening,closing,'2026-11-09')

    def test_ambiguous_literal_and_label_are_not_repaired_silently(self):
        with self.assertRaises(ValueError):span('Apply now\nApply now','Apply now')
        with self.assertRaises(ValueError):field_line('Value\n1\nValue\n2','Value')
        self.assertEqual(field_line('Closing date:\n31 December 2026\n','Closing date')['locator'],'L2')

    def test_saved_snapshot_is_reused_then_tampering_fails(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);raw=root/'a.html';text=root/'a.txt'
            raw.write_text('<main>Apply</main>');text.write_text('Apply\n');url='https://example.org/award'
            record={'url':url,'raw_path':str(raw),'text_path':str(text),
                    'raw_sha256':sha256(raw.read_bytes()).hexdigest(),
                    'text_sha256':sha256(text.read_bytes()).hexdigest()}
            (root/(sha256(url.encode()).hexdigest()[:20]+'.json')).write_text(json.dumps(record))
            with patch('expand_benefitlens_sources.urllib.request.urlopen') as get:
                self.assertEqual(capture_sources([url],root),[record]);get.assert_not_called()
                text.write_text('Different\n')
                with self.assertRaises(ValueError):capture_sources([url],root)

    def test_over_budget_is_rejected_without_saving_partial_snapshot(self):
        with TemporaryDirectory() as tmp:
            response=MagicMock();response.__enter__.return_value=response
            response.read.return_value=b'<main>'+b'x'*1000+b'</main>'
            response.url='https://example.org/award';response.status=200
            with patch('expand_benefitlens_sources.urllib.request.urlopen',return_value=response):
                records=capture_sources(['https://example.org/award'],tmp,max_bytes=100)
            self.assertEqual(records[0]['capture_state'],'held')
            self.assertEqual(list(Path(tmp).iterdir()),[])


if __name__=='__main__':unittest.main()
