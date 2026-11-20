import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from benefitlens_completed_catalog import identity,completed_relevant,source_fragment,browse,find_materials


class CompletedCatalogTests(unittest.TestCase):
    def test_unfinished_cannot_enter_completed_materials(self):
        for state in ['pending','codex_running','unresolved']:
            self.assertFalse(completed_relevant({'state':state,'result_json':'{"relevance":"relevant"}'}))

    def test_irrelevant_is_not_a_useful_material(self):
        self.assertFalse(completed_relevant({'state':'analyzed','result_json':'{"relevance":"irrelevant"}'}))
        self.assertTrue(completed_relevant({'state':'analyzed','result_json':'{"relevance":"relevant"}'}))

    def test_same_form_different_answer_is_retained(self):
        self.assertNotEqual(identity('What did you do?','Built a parser.'),identity('What did you do?','Organized interviews.'))

    def test_same_answer_in_different_question_is_not_merged(self):
        self.assertNotEqual(identity('What did you learn?','Programming.'),identity('What did you do?','Programming.'))

    def test_whitespace_duplicates_share_content_identity(self):
        self.assertEqual(identity('What did you do?','Built  a parser.'),identity('What did you do?','Built a\nparser.'))

    def test_changed_quote_is_rejected(self):
        with self.assertRaises(ValueError):source_fragment('real evidence',{'char_start':0,'char_end':4,'exact_quote':'fake'})

    def test_browse_requires_no_personal_profile(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'catalog.sqlite';c=sqlite3.connect(path)
            c.execute('CREATE TABLE notices(candidate_key,title,source_url,country,issuer,checked_at,qualification_status)')
            c.execute("INSERT INTO notices VALUES('N1','Research internship','https://example.org','KR','Example','2026-11-20','not_evaluated')")
            c.commit();c.close()
            result=browse(path,'internship',None,10)
            self.assertEqual(len(result),1)
            self.assertEqual(result[0]['qualification_status'],'not_evaluated')

    def test_material_search_keeps_different_answers_without_source_quota(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'catalog.sqlite';c=sqlite3.connect(path)
            c.execute('''CREATE TABLE experience_material(material_id,case_key,source_url,
              source_role,question,answer_fragment,content_key,source_sha256,
              char_start,char_end,analysis_json)''')
            c.executemany('INSERT INTO experience_material VALUES(?,?,?,?,?,?,?,?,?,?,?)',[
              ('M1','C1','https://same-source.example/1','participation_experience','Role?','Built a parser.','A','h1',0,15,'{}'),
              ('M2','C2','https://same-source.example/2','participation_experience','Role?','Organized interviews.','B','h2',0,21,'{}'),
              ('M3','C3','https://same-source.example/3','participation_experience','Role?','Built a parser.','A','h3',0,15,'{}')])
            c.commit();c.close()
            self.assertEqual(len(find_materials(path)),2)
            self.assertEqual(len(find_materials(path,'interviews')),1)
            c=sqlite3.connect(path)
            self.assertEqual(c.execute('select count(*) from experience_material').fetchone()[0],3)
            c.close()

if __name__=='__main__':unittest.main()
