from pathlib import Path
import sys
import io
import zipfile
import json

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from import_benefitlens_scored_writing import essay_key, span_locator
from collect_benefitlens_writing_foundations import archive_members,community,material_unit,source_config,PublicRead
from import_benefitlens_ellipse import read_rows,TRAITS
from collect_benefitlens_open_writing_books import chapters
from benefitlens_source_storage import store_source_bytes
from import_benefitlens_writing_revisions import pair_key


def test_annotation_end_can_be_inclusive():
    result=span_locator('Claim. Evidence.','Claim.',0,5)
    assert result['start']==0 and result['end']==6
    assert result['verified_text']=='Claim.'


def test_whitespace_repairs_keep_original_quote_distinct():
    result=span_locator('Claim.\n\nEvidence.','Claim. \n',0,7)
    assert result['verified_text']=='Claim.'
    assert result['basis']=='trimmed_whitespace_exact'


def test_internal_whitespace_alignment_returns_verbatim_source_slice():
    text='A specific\n\nexample is required.'
    result=span_locator(text,'specific example',2,18)
    assert result['basis']=='whitespace_equivalent_text'
    assert text[result['start']:result['end']]==result['verified_text']=='specific\n\nexample'


def test_changes_to_words_do_not_receive_a_verified_span():
    result=span_locator('Evidence supports the claim.','Evidence contradicts the claim.',0,30)
    assert result['start'] is None and result['verified_text'] is None


def test_repeated_short_phrase_uses_original_source_anchor():
    result=span_locator('fact then fact','fact \n',10,15)
    assert result['start']==10 and result['end']==14
    assert result['basis']=='trimmed_source_offsets_exact'


def test_same_complete_essay_is_not_multiplied_by_annotation_rows():
    assert essay_key('A claim.\nEvidence.')==essay_key('A claim. Evidence.')
    assert essay_key('A different claim.')!=essay_key('A claim.')


def test_repository_admin_is_not_a_drafting_template():
    assert material_unit('good-docs-current','.gitlab/issue_templates/new-template.md')=='repository_context'
    assert material_unit('good-docs-current','CONTRIBUTING.md')=='repository_context'
    assert material_unit('good-docs-current','how-to/template_how-to.md')=='genre_template'
    assert material_unit('persuade-rubrics','README.md')=='repository_context'
    assert material_unit('graduate-admissions-author','Recommendations/letter.pdf')=='recommendation_letter'


def test_extension_registry_retains_base_sources():
    config=source_config(Path(__file__).resolve().parents[2]/'pipeline/config/writing_foundation_sources_v2.json')
    assert {'microsoft-writing-style','ellipse-corpus','gsoc-iitbhu'} <= {s['id'] for s in config['repositories']}
    assert len(config['communities'])==2
    assert 'word-choice' in config['communities'][0]['tags']


def test_adding_a_tag_does_not_restart_compatible_prior_pages(tmp_path):
    import hashlib
    prior={'site':'writing','tags':['essay'],'nottagged':'fiction'}
    signature=hashlib.sha256(json.dumps([prior['site'],prior['tags'],prior['nottagged']]).encode()).hexdigest()
    (tmp_path/'community-checkpoint.json').write_text(json.dumps({'search_signature':signature,
        'queries':{'essay':{'next_page':4,'done':False}},'answer_batches':{}}))
    seen=[]
    class Reader:
        def read(self,url):
            seen.append(url)
            return json.dumps({'items':[],'has_more':False}).encode(),{}
    spec={'id':'writing-stackexchange',**prior,'tags':['essay','clarity'],'previous_tags':['essay'],
          'api':'https://api.stackexchange.com/2.3/search/advanced'}
    _,status=community(spec,Reader(),tmp_path,1)
    assert 'page=4' in seen[0]
    assert status['query_checkpoints']['essay']['next_page']==5


def test_ellipse_keeps_scores_and_split_without_deriving_demographics():
    import csv
    data=io.StringIO();w=csv.DictWriter(data,fieldnames=['text_id_kaggle','full_text','prompt','gender',*TRAITS])
    w.writeheader();w.writerow({'text_id_kaggle':'writer1','full_text':'A complete argument.','prompt':'A question',
                               'gender':'Female',**{k:'3.5' for k in TRAITS}})
    row=next(read_rows(io.BytesIO(data.getvalue().encode()),'test'))
    assert row['source_split']=='test'
    assert row['scores']['Cohesion']==3.5
    assert 'gender' not in row


def test_publisher_chapter_does_not_double_count_complete_book():
    raw=b'''<p>Open the book: <a href="/docs/books/writingspaces1.pdf">In PDF Format</a></p>
    <p><a href="/docs/books/writingspaces1/writing.pdf">How to write</a> by An Author</p>'''
    found=chapters(raw,'https://wacclearinghouse.org/books/writingspaces/writingspaces1/')
    assert len(found)==1 and found[0]['author_as_printed']=='An Author'


def test_bad_pdf_does_not_discard_remaining_archive(tmp_path):
    raw=archive({'repo/proposal.pdf':b'%PDF-invalid', 'repo/README.md':'# Guidance\n'+('Readable guidance. '*8)})
    spec={'id':'gsoc-test','include':['*.pdf','README.md'],'repo':'publisher/proposals','genre':'proposal',
          'role':'application_text','scope':'test','_held_members':[]}
    docs,_=archive_members(raw,spec,tmp_path,'fixed')
    assert len(docs)==1 and docs[0]['member']=='README.md'
    assert spec['_held_members'][0]['reason']=='pdf_text_extraction_failed'


def test_same_site_native_ids_are_isolated_between_communities(tmp_path):
    class Reader:
        def read(self,url):
            if '/search/advanced' in url:
                data={'items':[{'question_id':1,'body':'<p>A writing question</p>'}],'has_more':False}
            else:
                data={'items':[{'answer_id':2,'question_id':1,'body':'<p>An answer</p>'}],'has_more':False}
            return json.dumps(data).encode(),{}
    base={'tags':['writing'],'nottagged':'fiction','api':'https://api.stackexchange.com/2.3/search/advanced'}
    a,_=community({'id':'writing-stackexchange','site':'writing',**base},Reader(),tmp_path,1)
    b,_=community({'id':'academia-writing-stackexchange','site':'academia',**base},Reader(),tmp_path,1)
    answer=next(d for d in b if d['unit']=='answer')
    assert answer['url']=='https://academia.stackexchange.com/a/2'
    assert answer['original_question']['document_id'].startswith('academia-writing-stackexchange-')
    assert not {d['document_id'] for d in a}&{d['document_id'] for d in b}


def quota_receipt(root,reset):
    from hashlib import sha256
    reader=PublicRead(root,2);url='https://api.github.com/repos/example/source'
    meta={'url':url,'status':403,'headers':{'X-RateLimit-Remaining':'0','X-RateLimit-Reset':str(reset)},
          **store_source_bytes(b'quota exhausted',root/'blobs')}
    (root/'receipts'/(sha256(url.encode()).hexdigest()+'.json')).write_text(json.dumps(meta))
    return reader,url


def test_provider_quota_hold_uses_explicit_reset_without_request(tmp_path,monkeypatch):
    monkeypatch.setattr('collect_benefitlens_writing_foundations.time.time',lambda:1000)
    reader,url=quota_receipt(tmp_path,1100)
    with pytest.raises(ValueError,match='Provider quota exhausted'):
        reader.read(url)
    assert reader.calls==0
    assert reader.holds['api.github.com']['resume_after']==1100


def test_expired_quota_receipt_can_resume_while_preserving_denial(tmp_path,monkeypatch):
    monkeypatch.setattr('collect_benefitlens_writing_foundations.time.time',lambda:1000)
    reader,url=quota_receipt(tmp_path,900)
    class Response:
        status_code=200;headers={}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def iter_content(self,*args):yield b'public response'
    reader.session.get=lambda *args,**kwargs:Response()
    raw,_=reader.read(url)
    assert raw==b'public response' and reader.calls==1
    assert len(list((tmp_path/'receipts/history').glob('*.json')))==1


def test_sentence_and_document_revision_units_are_not_conflated():
    assert pair_key('before','after','sentence')!=pair_key('before','after','document')
    assert pair_key('before','after','sentence')!=pair_key('after','before','sentence')


def archive(files):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        for path,text in files.items():z.writestr(path,text)
    return b.getvalue()


def test_book_aggregate_and_leaf_do_not_both_count(tmp_path):
    raw=archive({'repo/s01-whole-chapter.html':'<p>'+('Parent content. '*10)+'</p>',
                 'repo/s01-01-leaf-section.html':'<p>'+('Leaf content. '*10)+'</p>'})
    spec={'id':'writing-for-success','include':['*.html'],'repo':'publisher/book','genre':'composition','role':'principle','scope':'test'}
    docs,_=archive_members(raw,spec,tmp_path,'revision')
    assert len(docs)==1 and docs[0]['member']=='s01-01-leaf-section.html'


def test_zip_parent_path_is_rejected(tmp_path):
    spec={'id':'test','include':['*.md']}
    with pytest.raises(ValueError,match='Unsafe'):
        archive_members(archive({'repo/../../outside.md':'x'*100}),spec,tmp_path,'revision')


def test_utf8_markdown_archive_member_is_preserved(tmp_path):
    spec={'id':'guide','include':['guide/*.md'],'repo':'publisher/guide','genre':'style','role':'principle','scope':'test'}
    raw=archive({'repo/guide/paragraphs.md':'\ufeff# Paragraph unity\n'+('Use one controlling idea. '*6)})
    docs,_=archive_members(raw,spec,tmp_path,'fixed-commit')
    assert len(docs)==1 and docs[0]['title']=='Paragraph unity'
    assert docs[0]['revision']=='fixed-commit'


def test_image_only_answer_does_not_block_remaining_answer_page(tmp_path):
    class Reader:
        def read(self,url):
            if '/search/advanced' in url:
                data={'items':[{'question_id':1,'body':'<p>How can I support a claim?</p>','title':'Supporting claims','link':'https://writing.stackexchange.com/q/1'}],'has_more':False}
            else:
                data={'items':[{'answer_id':2,'question_id':1,'body':'<img src="example.png">'},
                               {'answer_id':3,'question_id':1,'body':'<p>Describe a specific observation as evidence.</p>'}],'has_more':False}
            return json.dumps(data).encode(),{}
    spec={'id':'writing-stackexchange','site':'writing','tags':['essay'],'nottagged':'fiction',
          'api':'https://api.stackexchange.com/2.3/search/advanced'}
    docs,status=community(spec,Reader(),tmp_path,1)
    assert len(docs)==2 and status['answers']==1 and status['held_body_records']==1
    checkpoint=json.loads((tmp_path/'community-checkpoint.json').read_text())
    assert all(s['done'] for s in checkpoint['answer_batches'].values())
