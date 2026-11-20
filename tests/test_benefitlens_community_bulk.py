"""Bulk corpus identities and provenance must survive cross-site imports."""
from hashlib import md5, sha1
import gzip
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import collect_benefitlens_community_bulk as bulk


@pytest.fixture
def config():
    return json.loads(bulk.CONFIG.read_text())


def question(ident=1, title='How should I connect my statement of purpose paragraphs?'):
    return {'Id':str(ident), 'PostTypeId':'1', 'Title':title,
            'Body':'<p>Describe the opening and the next paragraph.</p>',
            'Tags':'<graduate-admissions><statement-of-purpose>', 'OwnerUserId':'4',
            'ContentLicense':'CC BY-SA 4.0', 'AcceptedAnswerId':'2'}


def answer(ident=2, parent=1):
    return {'Id':str(ident), 'PostTypeId':'2', 'ParentId':str(parent),
            'Body':'<p>State the research question.</p><p>Connect it to evidence.</p>',
            'OwnerUserId':'5', 'ContentLicense':'CC BY-SA 4.0'}


def test_scope_is_candidate_not_all_community_posts(config):
    reasons, intents = bulk.question_scope('academia', question(), config)
    assert reasons and 'paragraph_logic' in intents
    reasons, _ = bulk.question_scope('writing', {
        'Title':'Can my dragon be a wizard?', 'Tags':'<fantasy><characters>',
        'Body':'<p>I am developing a fictional world.</p>'}, config)
    assert reasons == []
    reasons, _ = bulk.question_scope('writing', {
        'Title':'How should the final paragraph link to the first?',
        'Tags':'<paragraphs>', 'Body':'<p>Advice on paragraphs.</p>'}, config)
    assert reasons == ['registered_writing_tag']
    reasons, _ = bulk.question_scope('academia', {
        'Title':'Should I notify authors of missed citations?',
        'Body':'<p>I presume that the omission is in good faith.</p>',
        'Tags':'<citations>'}, config)
    assert reasons == []


def test_ids_include_site_and_parent_context(tmp_path):
    site_a = {'id':'academia','domain':'academia.stackexchange.com'}
    site_w = {'id':'writing','domain':'writing.stackexchange.com'}
    records = [bulk.make_record(tmp_path, site, answer(), question(), ['scope'], ['paragraph_logic'],
                               {'DisplayName':'Writer'}, {'sha256':'archive'})
               for site in [site_a, site_w]]
    assert records[0]['document_id'] != records[1]['document_id']
    assert records[0]['original_question']['document_id'] == 'stackexchange:academia:question:1'
    assert records[0]['community_answer_accepted'] is True
    assert records[0]['content_license'] == 'CC BY-SA 4.0'
    assert records[0]['accepted_experience'] is False
    assert records[0]['approved_pattern'] is False
    assert records[0]['author']['link'] == 'https://academia.stackexchange.com/users/5'


def test_checksum_verifies_size_and_both_published_checksums(tmp_path):
    file = tmp_path/'archive.7z'; file.write_bytes(b'archive fixture')
    expected = {'size':str(file.stat().st_size), 'md5':md5(file.read_bytes()).hexdigest(),
                'sha1':sha1(file.read_bytes()).hexdigest()}
    assert len(bulk.verify_archive(file, expected)['sha256']) == 64
    with pytest.raises(ValueError, match='md5'):
        bulk.verify_archive(file, dict(expected, md5='0'*32))
    with pytest.raises(ValueError, match='size'):
        bulk.verify_archive(file, dict(expected, size='1'))


def test_all_posts_retained_selected_answers_join_by_parent_and_restart_safe(tmp_path, monkeypatch, config):
    monkeypatch.setattr(bulk, 'ROOT_FOR_STATUS', tmp_path)
    def rows(_archive, member):
        if member == 'Users.xml':
            yield {'Id':'4','DisplayName':'Question Writer'}
            yield {'Id':'5','DisplayName':'Answer Writer'}
        else:
            # Answer preceding the question must still be linked after import.
            yield answer()
            yield question()
            yield question(3, title='How to house a dragon?') | {
                'Tags':'<fantasy>', 'Body':'<p>Build a fictional castle.</p>'}
    monkeypatch.setattr(bulk, 'archive_rows', rows)
    db = bulk.connect(tmp_path)
    receipt = {'sha256':'a'*64}
    site = config['sites'][0]
    result = bulk.ingest(db,site,tmp_path/'dummy.7z',receipt,config)
    assert result['posts'] == 3
    assert db.execute('SELECT COUNT(*) FROM posts').fetchone()[0] == 3
    assert db.execute('SELECT COUNT(*) FROM posts WHERE selected=1').fetchone()[0] == 1
    assert bulk.ingest(db,site,tmp_path/'dummy.7z',receipt,config)['reused_completed_import']
    monkeypatch.setattr(bulk, 'CACHE', tmp_path/'empty-prior-cache')
    result = bulk.export(tmp_path,db,config,{'academia':receipt})
    assert result['retained_all_posts'] == 3
    assert result['counts']['selected_documents'] == 2
    assert result['counts']['full_question'] == 1
    assert result['counts']['full_answer'] == 1
    assert result['counts']['application_document_advice_candidate'] == 2
    assert result['counts']['new_against_prior_libraries'] == 2
    assert result['accepted_experiences_added'] == 0
    saved = json.loads((tmp_path/'records.json').read_text())
    assert saved[1]['original_question']['document_id'] == saved[0]['document_id']
    monkeypatch.setattr(bulk, 'make_record', lambda *a,**k: pytest.fail('Completed export should be reused'))
    assert bulk.export(tmp_path,db,config,{'academia':receipt})['reused_verified_export'] is True
    with pytest.raises(ValueError, match='new root'):
        bulk.ingest(db,site,tmp_path/'dummy.7z',{'sha256':'b'*64},config)


def test_empty_body_is_not_a_selected_document(tmp_path):
    assert bulk.make_record(tmp_path, {'id':'academia','domain':'academia.stackexchange.com'},
                            dict(answer(),Body='<p><img src="x.png"></p>'), question(), [], [], {}, {}) is None


def test_existing_ids_normalize_old_writers_domain(tmp_path):
    path = tmp_path/'old.json'
    path.write_text(json.dumps([{'url':'https://writers.stackexchange.com/a/8', 'native_id':'8',
                                 'text':{'sha256':'hash'}}]))
    ids, hashes = bulk.existing_keys([path])
    assert ('writing.stackexchange.com','8') in ids
    assert hashes == {'hash'}


def test_unknown_7z_member_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='Unexpected XML'):
        list(bulk.archive_rows(tmp_path/'dummy.7z','../../secret'))


def test_policy_changes_reclassify_without_redownloading(tmp_path, config):
    db = bulk.connect(tmp_path)
    item = question() | {'Title':'Citation question', 'Body':'<p>I presume good faith.</p>', 'Tags':'<citations>'}
    db.execute('INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?)',
               ('academia',1,1,None,4,item['Title'],1,bulk.packed(item),'["old-scope"]','[]'))
    db.commit()
    bulk.rescope(tmp_path,db,config)
    assert db.execute('SELECT selected FROM posts').fetchone()[0] == 0
    assert json.loads((tmp_path/'scope-policy.json').read_text())['questions_reclassified'] == 1


def test_new_only_export_carries_preexisting_parent_context(tmp_path):
    site = {'id':'academia','domain':'academia.stackexchange.com'}
    q = bulk.make_record(tmp_path,site,question(),question(),[],[],{}, {'sha256':'archive'})
    a = bulk.make_record(tmp_path,site,answer(),question(),[],[],{}, {'sha256':'archive'})
    q['prior_library_overlap'] = {'same_site_post_id':True,'same_text_hash':False}
    a['prior_library_overlap'] = {'same_site_post_id':False,'same_text_hash':False}
    result = bulk.export_new_records(tmp_path,[q,a])
    with gzip.open(result['path'],'rt') as stream:
        new = [json.loads(line) for line in stream]
    with gzip.open(result['required_existing_parent_contexts']['path'],'rt') as stream:
        parents = [json.loads(line) for line in stream]
    assert result['records'] == 1
    assert len(parents) == 1
    assert new[0]['original_question']['document_id'] == parents[0]['document_id']
    assert new[0]['accepted_experience'] is False
