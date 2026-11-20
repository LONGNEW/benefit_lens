import json
from pathlib import Path
import sys
import sqlite3
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from expand_benefitlens_experience_quality import zone_candidates,classify_span,project_occurrence,sense_hints,CONFIG,WhitespaceView,digest,structure_fingerprint,can_reuse_structure,STRUCTURE_FILES,paragraph_registry,paragraph_position
from expand_benefitlens_experience_quality import quality_writer,readonly


class ExperienceQualityTests(unittest.TestCase):
    def setUp(self):
        self.config=json.loads(CONFIG.read_text())

    def test_semantic_link_reader_does_not_block_quality_commit_or_lose_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'quality.sqlite'
            writer=quality_writer(path)
            writer.execute('CREATE TABLE observed(version INTEGER)')
            writer.execute('INSERT INTO observed VALUES(1)');writer.commit()
            reader=readonly(path)
            self.assertEqual(reader.execute('SELECT version FROM observed').fetchone()[0],1)
            writer.execute('UPDATE observed SET version=2');writer.commit()
            self.assertEqual(reader.execute('SELECT version FROM observed').fetchone()[0],1)
            fresh=readonly(path)
            self.assertEqual(fresh.execute('SELECT version FROM observed').fetchone()[0],2)
            fresh.close();reader.close();writer.close()

    def zones(self,raw,text,url='https://example.test/post'):
        zones,_=zone_candidates(raw.encode(),text,url,self.config)
        for i,z in enumerate(zones):z['zone_id']='z'+str(i)
        return zones

    def test_dom_body_and_related_first_person_story_are_separated(self):
        raw='<main><article><p>I built a useful tool and learned to test.</p></article><div class="widget PopularPosts"><p>I learned something in another project last year.</p></div></main>'
        text='I built a useful tool and learned to test.\nI learned something in another project last year.'
        zones=self.zones(raw,text)
        self.assertFalse(classify_span(0,40,zones)['auto_excluded'])
        start=text.index('I learned')
        self.assertTrue(classify_span(start,len(text),zones)['auto_excluded'])
        self.assertEqual(classify_span(0,len(text),zones)['zone'],'mixed_body_and_noise')

    def test_cookie_word_in_narrative_does_not_trigger_exclusion(self):
        text='I tested cookie handling and reduced a security defect.'
        zones=self.zones('<article><p>'+text+'</p></article>',text)
        self.assertEqual(classify_span(0,len(text),zones)['zone'],'body')
        self.assertFalse(classify_span(0,len(text),zones)['auto_excluded'])

    def test_cookie_consent_dom_structure_is_excluded(self):
        text='We collect information to improve this website.'
        zones=self.zones('<div id="cookie-consent-banner">'+text+'</div>',text)
        self.assertTrue(classify_span(0,len(text),zones)['auto_excluded'])

    def test_ambiguous_dom_text_is_not_automatically_excluded(self):
        value='A duplicated phrase appears in two sections.'
        zones=self.zones('<nav>'+value+'</nav><article>'+value+'</article>',value+'\n'+value)
        self.assertFalse(classify_span(0,len(value),zones)['auto_excluded'])

    def test_ordered_known_ui_menu_is_structural_not_a_keyword_rule(self):
        menu='\n'.join(self.config['higherin_menu_lines'])
        zones=self.zones('<html></html>',menu,'https://higherin.com/review/1')
        self.assertTrue(classify_span(0,len(menu),zones)['auto_excluded'])
        ordinary='I discuss my future career prospects after the internship.'
        zones=self.zones('<article>'+ordinary+'</article>',ordinary,'https://higherin.com/review/1')
        self.assertFalse(classify_span(0,len(ordinary),zones)['auto_excluded'])

    def test_partial_overlap_does_not_exclude_whole_passage(self):
        zones=[{'zone_id':'x','zone':'footer','auto_excludable':True,'char_start':50,'char_end':100}]
        row=classify_span(0,100,zones)
        self.assertEqual(row['zone'],'mixed_body_and_noise')
        self.assertFalse(row['auto_excluded'])
        self.assertEqual(row['noise_characters'],50)

    def test_exact_manual_context_can_exclude_but_changed_context_cannot(self):
        text='A menu.\nFuture Career Prospects\nView All Reviews'
        a,b=text.index('Future'),len(text)
        row={'occurrence_id':'o','group_id':'g','record_key':'c','document_id':'d','text_sha256':digest(text.encode()),
            'char_start':a,'char_end':b,'context_start':0,'context_end':len(text),'sentence_index':1,
            'source_role':'review','genre':'review','source_url':'https://example.test','model_representative':0}
        rep={'text':text[a:b],'context_text':text}
        projected=project_occurrence(row,rep,{'relevance':'irrelevant'},text,[])
        self.assertTrue(projected['quality']['auto_excluded'])
        self.assertEqual(projected['semantic_state'],'code_reuse_of_exact_codex_context')
        rep['context_text']='A different person and context.'
        projected=project_occurrence(row,rep,{'relevance':'irrelevant'},text,[])
        self.assertFalse(projected['quality']['auto_excluded'])
        self.assertFalse(projected['source_binding_verified'])

    def test_review_container_and_separate_review_carousel_keep_different_people_apart(self):
        own='I learned how to work with a supervisor.'
        other='Another role review from a different student.'
        raw='<main>Breadcrumb absent from text<div class="js-review-question-container">'+own+'</div><div class="swiper-wrapper"><a href="/review/2">'+other+'</a><a href="/review/3">Another review title.</a></div></main>'
        text=own+'\n'+other+' Another review title.'
        zones=self.zones(raw,text,'https://higherin.com/review/1')
        self.assertEqual(classify_span(0,len(own),zones,text)['zone'],'body')
        start=text.index(other)
        self.assertTrue(classify_span(start,len(text),zones,text)['auto_excluded'])

    def test_whitespace_between_known_noise_regions_does_not_create_false_body(self):
        text='menu\nfooter'
        zones=[{'zone_id':'a','zone':'navigation','auto_excludable':True,'char_start':0,'char_end':4},
               {'zone_id':'b','zone':'footer','auto_excludable':True,'char_start':5,'char_end':11}]
        self.assertTrue(classify_span(0,len(text),zones,text)['auto_excluded'])

    def test_word_sense_hints_do_not_become_semantic_approval(self):
        rows=sense_hints('I built a web application for anonymous peer feedback.',100)
        self.assertTrue(rows)
        self.assertTrue(all(row['status']=='lexical_candidate' for row in rows))
        self.assertIn('application_as_software_candidate',{r['hint'] for r in rows})
        self.assertTrue(all(row['char_start']>=100 for row in rows))

    def test_whitespace_alignment_preserves_actual_unicode_positions(self):
        text='앞 문장\n\nI  learned\n from testing. 뒤 문장'
        span=WhitespaceView(text).locate_unique('I learned from testing.')
        self.assertEqual(text[span[0]:span[1]],'I  learned\n from testing.')

    def test_structure_fingerprint_changes_for_cases_config_or_processing_version(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('documents.jsonl.gz','cases.jsonl.gz','segments.jsonl.gz'):(root/name).write_bytes(name.encode())
            config=root/'config.json';config.write_text('{}')
            original=structure_fingerprint(root,config)
            (root/'cases.jsonl.gz').write_bytes(b'new source role')
            self.assertNotEqual(original,structure_fingerprint(root,config))
            changed=structure_fingerprint(root,config)
            config.write_text('{"changed":true}')
            self.assertNotEqual(changed,structure_fingerprint(root,config))
            self.assertNotEqual(structure_fingerprint(root,config),structure_fingerprint(root,config,version='new-code'))

    def test_structure_cache_requires_artifact_hashes_and_database_row_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);fingerprint={'sha256':'input','inputs':{}}
            for name in STRUCTURE_FILES:(root/name).write_bytes(name.encode())
            cx=sqlite3.connect(root/'quality.sqlite')
            for name in ('documents','zones','segments'):cx.execute('CREATE TABLE '+name+'(id INTEGER)')
            cx.commit()
            manifest={'fingerprint':fingerprint,'artifact_sha256':{name:digest((root/name).read_bytes()) for name in STRUCTURE_FILES},
                'table_counts':{name:0 for name in ('documents','zones','segments')}}
            (root/'structure-cache.json').write_text(json.dumps(manifest))
            self.assertTrue(can_reuse_structure(root,fingerprint))
            self.assertFalse(can_reuse_structure(root,{'sha256':'different'}))
            cx.execute('INSERT INTO zones VALUES(1)');cx.commit()
            self.assertFalse(can_reuse_structure(root,fingerprint))
            cx.close()

    def paragraph_segments(self):
        return [{'document_id':'d','text_sha256':'sha','segment_id':str(i),'char_start':a,'char_end':b,
                 'kind':kind,'source_span_hash_verified':True} for i,(a,b,kind) in enumerate(
            [(0,20,'source_dom_paragraph_aligned'),(30,50,'source_dom_paragraph_aligned'),
             (0,50,'blank_line_block_candidate'),(60,80,'blank_line_block_candidate')])]

    def test_paragraph_registry_prefers_dom_over_overlapping_layout_block(self):
        rows=paragraph_registry(self.paragraph_segments())
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[0]['next_paragraph_id'],rows[1]['paragraph_id'])
        self.assertEqual(rows[1]['previous_paragraph_id'],rows[0]['paragraph_id'])
        self.assertFalse(rows[0]['semantic_paragraph_approval'])

    def test_opening_closing_middle_and_fragment_are_boundary_facts(self):
        paragraphs=paragraph_registry(self.paragraph_segments())
        for bounds,expected in [((0,20),'only'),((0,8),'opening'),((8,20),'closing'),((4,16),'middle'),((18,32),'fragment'),((90,100),'unknown')]:
            row=paragraph_position(*bounds,paragraphs)
            self.assertEqual(row['opening_or_closing_boundary_match'],expected)
            self.assertFalse(row['paragraph_order_is_logical_connection'])

    def test_arbitrary_bounded_chunk_is_not_promoted_to_a_paragraph(self):
        chunk={'document_id':'d','text_sha256':'sha','segment_id':'s','char_start':0,'char_end':2000,
               'kind':'bounded_text_layout_chunk','source_span_hash_verified':True}
        self.assertEqual(paragraph_registry([chunk]),[])


if __name__=='__main__':unittest.main()
