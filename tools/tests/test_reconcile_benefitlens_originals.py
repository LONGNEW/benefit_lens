from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from reconcile_benefitlens_originals import verify_document, upgrade_url
from benefitlens_source_storage import store_source_bytes


class OriginalGateTests(unittest.TestCase):
    def test_verified_compressed_source_and_text(self):
        with TemporaryDirectory() as folder:
            raw=store_source_bytes(b'<p>'+b'original '*100+b'</p>',Path(folder),suffix='.html')
            text=store_source_bytes(b'original '*100,Path(folder),suffix='.txt')
            result=verify_document(('D1',raw['sha256'],text['sha256'],raw['path'],text['path'],
                                    {'raw_storage':raw['storage'],'text_storage':text['storage']}))
            self.assertEqual(result[3],'verified')

    def test_changed_text_cannot_be_verified(self):
        with TemporaryDirectory() as folder:
            raw=Path(folder)/'raw';raw.write_bytes(b'<p>original</p>')
            text=Path(folder)/'text';text.write_bytes(b'x'*200)
            result=verify_document(('D1',sha256(raw.read_bytes()).hexdigest(),'bad',str(raw),str(text),{}))
            self.assertEqual(result[3],'unavailable')

    def test_missing_raw_is_excluded(self):
        result=verify_document(('D1','x','y','/nonexistent/raw','/nonexistent/text',{}))
        self.assertEqual(result[3],'unavailable')

    def test_short_text_is_excluded(self):
        with TemporaryDirectory() as folder:
            p=Path(folder)/'text';p.write_bytes(b'access')
            h=sha256(p.read_bytes()).hexdigest()
            self.assertEqual(verify_document(('D1',h,h,str(p),str(p),{}))[3],'unavailable')

    def test_upgrade_preserves_host_path_query(self):
        self.assertEqual(upgrade_url('http://example.org/a?q=1'),'https://example.org/a?q=1')

    def test_does_not_invent_host_or_fix_arbitrary_scheme(self):
        self.assertEqual(upgrade_url('example.org/a'),'example.org/a')
        self.assertEqual(upgrade_url('htps://example.org/a'),'htps://example.org/a')

if __name__=='__main__':unittest.main()
