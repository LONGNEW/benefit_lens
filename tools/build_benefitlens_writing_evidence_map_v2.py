"""Map preserved writing evidence by source role, genre and evidence strength.

Default execution reads local corpora only. --collect-primary explicitly adds
public publisher texts. Automated inventory classification is not semantic
review, quality calibration, a learned writing pattern or an applicant count.
"""
import argparse
from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from urllib.parse import urljoin, urlsplit
import zlib

from benefitlens_source_storage import read_source_bytes, store_source_bytes
from import_benefitlens_full_inventory import SharedEvidence
from run_benefitlens_campaign import CACHE, KST, atomic_json, checked_root, single_writer
from run_benefitlens_korea import soup

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO/'pipeline/config/writing_evidence_framework_v2.json'


def digest(raw):
    return sha256(raw.encode() if isinstance(raw, str) else raw).hexdigest()


def load(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default


def blob(ref):
    raw = read_source_bytes(ref['path'], storage=ref.get('storage'))
    if digest(raw) != ref['sha256']:
        raise ValueError('Preserved source checksum mismatch')
    return raw


def open_ro(path):
    return sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True)


def genres(tags):
    tags = set(tags or [])
    output = []
    for target, words in [
        ('application', {'resume', 'cv', 'cover-letter', 'statement-of-purpose', 'graduate-admissions', 'applications', 'application'}),
        ('scientific_academic', {'academic-writing', 'scientific-writing', 'papers', 'publications', 'thesis', 'research'}),
        ('literary_fiction', {'fiction', 'novel', 'characters', 'plot', 'creative-writing', 'fantasy', 'poetry'}),
        ('general_language', {'grammar', 'punctuation', 'style', 'word-choice', 'paragraphs', 'clarity', 'structure'})]:
        if tags & words:
            output.append(target)
    return output or ['undetermined']


def canonical_post(row):
    host = urlsplit(row.get('url', '')).hostname or ''
    if host.endswith('stackexchange.com') and row.get('native_id') is not None:
        site = host.split('.')[0]
        return 'se:' + ('writing' if site == 'writers' else site) + ':' + str(row['native_id'])
    return None


def form_kind(unit, role):
    if (role or '').startswith('official'):
        return 'official_program_guidance', 'official_requirement_candidate'
    if unit in ('question',) or role == 'question_context':
        return 'community_question', 'community_advice'
    if unit in ('answer',) or role == 'public_advice':
        return 'community_answer', 'community_advice'
    if unit in ('textbook_section', 'guidance_file', 'template_guide', 'genre_template', 'authoring_process'):
        return unit, 'scholarly_recommendation_candidate'
    if unit in ('application_text', 'application_proposal', 'recommendation_letter'):
        return unit, 'example_text'
    if unit == 'scoring_rubric':
        return unit, 'human_scoring_standard'
    return unit or role or 'unresolved', 'reference_metadata_or_context'


def collect_primary(root, config):
    ledger = SharedEvidence(root/'transport', datetime.now(KST).date().isoformat(), 100)
    sizes = {row['url']: row.get('byte_limit', 8*1024**2) for row in config['primary_sources']}
    ledger.response_byte_limit = lambda url: sizes.get(url, 8*1024**2)
    rows = {row['source_id']: row for row in load(root/'primary-records.json', [])}
    before = ledger.network_requests
    for spec in config['primary_sources']:
        if rows.get(spec['id'], {}).get('state') == 'body_preserved':
            continue
        result = dict(spec, source_id=spec['id'], semantic_review='needs_content_review')
        try:
            url = spec['url']
            visited = set()
            for _ in range(5):
                if url in visited:
                    raise ValueError('Publisher redirect loop')
                visited.add(url)
                raw, receipt = ledger.request(url, redirect_metadata=True)
                if receipt['status'] == 200:
                    break
                destination = urljoin(url, receipt['headers'].get('location', ''))
                if urlsplit(destination).hostname != urlsplit(spec['url']).hostname:
                    raise ValueError('Redirect leaves the registered publisher host')
                url = destination
            else:
                raise ValueError('Publisher redirect ceiling')
            if raw.startswith(b'%PDF'):
                text = subprocess.run(['pdftotext', '-layout', '-', '-'], input=raw,
                    capture_output=True, check=True, timeout=90).stdout.decode()
                kind = 'pdf'
            else:
                doc = soup(raw)
                for node in doc.select('script,style,nav,footer,header,noscript'):
                    node.decompose()
                body = doc.select_one('.article-text, .article-body, #artText, main, article, body') or doc
                text = '\n'.join(t.strip() for t in body.get_text('\n').splitlines() if t.strip())
                kind = 'html'
                if re.search(r'captcha|access denied|verify you are human|checking your browser', text[:2000], re.I):
                    raise ValueError('Access challenge is not a primary writing source')
            if len(text.strip()) < 400:
                raise ValueError('Usable source body is absent')
            result.update(state='body_preserved', raw=receipt,
                text=store_source_bytes(text.encode(), root/'blobs', suffix='.txt'),
                body_characters=len(text), kind=kind, collected_at=datetime.now(KST).isoformat())
        except Exception as error:
            result.update(state='held', reason=type(error).__name__ + ': ' + str(error)[:250])
        rows[spec['id']] = result
        atomic_json(root/'primary-records.json', list(rows.values()))
        print(json.dumps({'primary_source': spec['id'], 'state': result['state'],
                          'body_characters': result.get('body_characters'), 'reason': result.get('reason')}, ensure_ascii=False), flush=True)
    return ledger.network_requests - before


def audit_eight(root, legacy):
    rows = {row['document_id']: row for row in legacy}
    result = []
    for principle in load(REPO/'pipeline/config/writing_principles_v1.json', []):
        source = rows.get(principle['source_id'])
        review = dict(principle, editorial_origin='local_application_adaptation_of_source_guidance',
                      universal_validity_established=False)
        if source and source.get('text'):
            text = blob(source['text']).decode()
            pattern = r'\s+'.join(re.escape(word) for word in principle['quote'].split())
            matches = list(re.finditer(pattern, text))
            review.update(source_url=source['url'], source_document_id=source['document_id'],
                source_text_ref=source['text'], quote_matches=len(matches),
                quote_verified=bool(matches),
                exact_spans=[{'char_start': m.start(), 'char_end': m.end(), 'exact_quote': text[m.start():m.end()]}
                             for m in matches])
        else:
            review.update(quote_verified=False, reason='source_record_not_resolved')
        result.append(review)
    atomic_json(root/'legacy-eight-audit.json', result)
    return result


class Map:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript('''CREATE TABLE IF NOT EXISTS evidence_record(
            record_id TEXT PRIMARY KEY, canonical_id TEXT NOT NULL, family TEXT NOT NULL,
            unit TEXT NOT NULL, source_form TEXT NOT NULL, evidence_kind TEXT NOT NULL,
            target_genres TEXT NOT NULL, review_state TEXT NOT NULL, body_sha256 TEXT,
            metadata TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS evidence_canonical ON evidence_record(canonical_id);
            CREATE INDEX IF NOT EXISTS evidence_family ON evidence_record(family,unit);''')
        self.families = {}
        self.current_family = None

    def add(self, family, native, unit, form, kind, target, *, canonical=None,
            text_sha=None, metadata=None, review='auto_source_role_classified'):
        if self.current_family != family:
            self.db.commit()
            self.db.execute('DELETE FROM evidence_record WHERE family=?', (family,))
            self.current_family = family
        self.db.execute('INSERT OR REPLACE INTO evidence_record VALUES(?,?,?,?,?,?,?,?,?,?)',
            (family+':'+str(native), canonical or family+':'+str(native), family, unit, form,
             kind, json.dumps(target), review, text_sha, json.dumps(metadata or {}, ensure_ascii=False)))
        self.families[family] = self.families.get(family, 0) + 1

    def done(self):
        self.db.commit()
        return {family: {'mapped_rows': count} for family, count in self.families.items()}


def build_map(root):
    m = Map(root/'evidence-map.sqlite3')
    base = CACHE/'writing-foundations-v1'
    legacy = load(base/'open-guides/records.json', [])
    eight = audit_eight(root, legacy)
    for family, path in [('open_guides', base/'open-guides/records.json'),
                          ('question_knowledge', CACHE/'question-knowledge-v1/records.json')]:
        for row in load(path, []):
            form, kind = form_kind(row.get('unit') or row.get('body_scope'), row.get('role') or row.get('source_role'))
            ref = row.get('text') or row.get('text_ref')
            target = genres(row.get('tags')) if canonical_post(row) else [row.get('genre') or 'undetermined']
            m.add(family, row['document_id'], 'source_document', form, kind, target,
                  canonical=canonical_post(row), text_sha=(ref or {}).get('sha256'),
                  metadata={'url': row.get('url'), 'source_id': row.get('source_id') or row.get('source'),
                            'source_role': row.get('role') or row.get('source_role'),
                            'text_ref': ref, 'source_record_path': str(path),
                            'scope': row.get('scope'), 'declared_genre': row.get('genre')})
    print('mapped old source documents', flush=True)
    bulk = CACHE/'community-bulk-20261119'
    selected = {row['document_id']: row for row in load(bulk/'records.json', [])}
    question_tags = {}
    with open_ro(bulk/'corpus.sqlite3') as db:
        for site, ident, kind, parent, title, chosen, attrs in db.execute(
                'SELECT site,id,type,parent,title,selected,attrs FROM posts ORDER BY type,id'):
            data = json.loads(zlib.decompress(attrs))
            tags = re.findall(r'<([^>]+)>', data.get('Tags', ''))
            if kind == 1:
                question_tags[(site, ident)] = tags
            else:
                tags = question_tags.get((site, parent), [])
            native = site+':'+str(ident)
            role = 'question' if kind == 1 else 'answer'
            selected_row = selected.get('stackexchange:'+site+':'+role+':'+str(ident))
            m.add('community_bulk', native, 'post', 'community_'+role, 'community_advice', genres(tags),
                canonical='se:'+site+':'+str(ident), text_sha=(selected_row or {}).get('text_sha256'),
                metadata={'source_path': str(bulk/'corpus.sqlite3'), 'table': 'posts',
                    'source_key': {'site': site, 'id': ident}, 'parent_id': parent,
                    'selected_candidate': selected_row is not None,
                    'question_selected_by_source_filter': bool(chosen), 'tags': tags,
                    'html_body_sha256': digest(data.get('Body', '')),
                    'selection_scope': (selected_row or {}).get('evidence_scope'),
                    'source_form_is_not_target_genre': True})
    print('mapped all preserved community posts', flush=True)
    for family, rel in [('persuade', 'persuade/persuade-test.sqlite'), ('ellipse', 'ellipse/ellipse.sqlite')]:
        with open_ro(base/rel) as db:
            extra = 'holistic_score,prompt_name' if family == 'persuade' else 'scores_json,prompt'
            for key, native, text_sha, split, score, prompt in db.execute(
                    'SELECT text_key,native_id,text_sha256,source_split,'+extra+' FROM essay'):
                m.add(family, key, 'essay', 'human_scored_student_essay', 'human_scored_observation',
                    ['school_argument' if family == 'persuade' else 'language_learner_writing'],
                    canonical='normalized-essay:'+key, text_sha=text_sha,
                    metadata={'source_path': str(base/rel), 'table': 'essay', 'text_key': key,
                              'native_id': native, 'source_split': split, 'original_score': score,
                              'prompt': prompt, 'evaluation_use': 'development_data_already_examined'})
            if family == 'persuade':
                for row, key, role, effectiveness, a, b, span_basis, hierarchy, parent_role in db.execute(
                        'SELECT source_row,text_key,discourse_type,effectiveness,verified_start,verified_end,span_basis,hierarchical_id,hierarchical_label FROM discourse'):
                    m.add('persuade_annotations', row, 'annotation', 'human_discourse_annotation',
                        'human_scored_observation', ['school_argument'],
                        metadata={'essay_key': key, 'role': role, 'effectiveness': effectiveness,
                                  'char_start': a, 'char_end': b, 'span_basis': span_basis,
                                  'source_hierarchical_id': hierarchy, 'source_hierarchical_label': parent_role,
                                  'hierarchy_link_status': 'preserved_not_reinterpreted',
                                  'warrant_annotation_available': False,
                                  'source_path': str(base/rel), 'table': 'discourse'},
                        review='auto_source_role_classified' if a is not None else 'needs_span_review')
    print('mapped scored essays and discourse annotations', flush=True)
    with open_ro(base/'revisions/revisions.sqlite') as db:
        for key, level, before, after in db.execute('SELECT pair_key,granularity,before_sha256,after_sha256 FROM revision'):
            m.add('revision_pairs', key, 'revision_pair', level+'_revision_pair', 'revision_pair', ['genre_requires_source_context'],
                metadata={'source_path': str(base/'revisions/revisions.sqlite'), 'table': 'revision',
                          'pair_key': key, 'before_sha256': before, 'after_sha256': after,
                          'improvement_established': False})
        for rowid, corpus, variant, split, row, key, docid, intent, origin in db.execute(
                'SELECT rowid,corpus,variant,source_split,source_row,pair_key,doc_id,edit_intent,label_origin FROM observation'):
            evidence_kind = {'human_edit_intent_annotation': 'human_revision_intent',
                             'human_revision_reference': 'human_revision_reference'}.get(
                                 origin, 'model_predicted_revision_intent')
            m.add('revision_labels', rowid, 'label_observation',
                origin, evidence_kind,
                ['genre_requires_source_context'], metadata={'pair_key': key, 'corpus': corpus,
                    'variant': variant, 'source_split': split, 'source_row': row, 'sqlite_rowid': rowid,
                    'doc_id': docid, 'edit_intent': intent, 'label_origin': origin})
    print('mapped all revision pairs and annotation origins', flush=True)
    status = load(CACHE/'writing-corpus-expansion-v1/status.json', {})
    if status.get('corpus'):
        corpus = json.loads(blob(status['corpus']))
        for row in corpus.get('source_documents', []):
            role = row.get('role', 'undetermined')
            kind = 'official_requirement_candidate' if role.startswith('official') else 'example_text'
            m.add('application_corpus', row['document_id'], 'source_document', role, kind, ['application'],
                text_sha=(row.get('text') or {}).get('sha256'), metadata={'url': row.get('url'),
                    'program_id': row.get('program_id'), 'text_ref': row.get('text'),
                    'document_finality': row.get('document_finality'), 'case_bindings': row.get('case_bindings')})
    for source_row, row in enumerate(load(CACHE/'participant-documents-20261119/index.json', [])):
        if row.get('state') != 'body_staged':
            continue
        role = row.get('document_role', 'unresolved')
        m.add('participant_documents', source_row, 'source_document', role, 'example_text',
              ['application' if role == 'application_proposal' else 'participation_log'],
              canonical=row['url'], text_sha=row['text']['sha256'],
              metadata={'url': row['url'], 'text_ref': row['text'], 'source_row': source_row,
                'program_candidates': row.get('program_candidates'), 'author': row.get('author'),
                'submission_status': 'unconfirmed', 'document_finality': row.get('document_finality')})
    for row in load(root/'primary-records.json', []):
        if row.get('state') != 'body_preserved':
            continue
        m.add('new_primary_sources', row['source_id'], 'source_document', row['source_form'],
              row['evidence_kind'], row['target_genres'], text_sha=row['text']['sha256'],
              metadata={'url': row['url'], 'text_ref': row['text'], 'raw_ref': row['raw'],
                        'source_id': row['source_id'], 'effect_scope': row.get('effect_scope')})
    m.done()
    for family, expected in m.families.items():
        actual = m.db.execute('SELECT count(*) FROM evidence_record WHERE family=?', (family,)).fetchone()[0]
        if actual != expected:
            raise ValueError('Source row coverage mismatch: ' + family)
    counts = m.db.execute('SELECT family,unit,count(*),count(DISTINCT canonical_id) FROM evidence_record GROUP BY family,unit').fetchall()
    forms = m.db.execute('SELECT family,source_form,count(*) FROM evidence_record GROUP BY family,source_form').fetchall()
    evidence = m.db.execute('SELECT evidence_kind,count(*) FROM evidence_record GROUP BY evidence_kind').fetchall()
    union_essays = m.db.execute("SELECT count(DISTINCT canonical_id) FROM evidence_record WHERE family IN ('persuade','ellipse')").fetchone()[0]
    review_counts = m.db.execute('SELECT review_state,count(*) FROM evidence_record GROUP BY review_state').fetchall()
    source_reviews = load(root/'manual-content-reviews.json', {}).get('reviews', [])
    verified_reviews = []
    for review in source_reviews:
        text = blob(review['source_text_ref']).decode()
        for span in review['support_spans']:
            if (span['text_sha256'] != review['source_text_ref']['sha256'] or
                    not 0 <= span['char_start'] < span['char_end'] <= len(text)):
                raise ValueError('Content review source span is invalid')
        verified_reviews.append(review)
    genre_counts = dict(m.db.execute('SELECT j.value,count(*) FROM evidence_record,json_each(target_genres) j GROUP BY j.value'))
    summary = {'version': 'writing-evidence-map-v2', 'at_kst': datetime.now(KST).isoformat(),
        'database': str(root/'evidence-map.sqlite3'), 'mapped_rows': m.db.execute('SELECT count(*) FROM evidence_record').fetchone()[0],
        'families': [{'family': f, 'unit': u, 'rows': n, 'canonical_records_within_unit': d} for f,u,n,d in counts],
        'source_forms': [{'family': f, 'source_form': s, 'count': n} for f,s,n in forms],
        'evidence_kinds': dict(evidence), 'classification_review_states': dict(review_counts),
        'target_genres_overlap': genre_counts,
        'scored_unique_essays_across_two_corpora': union_essays,
        'community_exported_candidate_posts': len(selected),
        'legacy_eight': {'principles': len(eight), 'quote_verified': sum(x['quote_verified'] for x in eight),
                         'writing_for_success_principles': sum(x['source_id'].startswith('writing-for-success:') for x in eight),
                         'distinct_writing_for_success_sections': len({x['source_id'] for x in eight if x['source_id'].startswith('writing-for-success:')}),
                         'microsoft_style_principles': sum(x['source_id'].startswith('microsoft-writing-style:') for x in eight),
                         'distinct_microsoft_files': len({x['source_id'] for x in eight if x['source_id'].startswith('microsoft-writing-style:')}),
                         'role': 'small_editorial_adaptation_not_complete_writing_theory'},
        'semantic_review_of_all_records_performed': False, 'quality_scores_assigned': 0,
        'agent_source_section_reviews': len(verified_reviews),
        'independent_human_content_reviews': sum(r.get('independent_human_review') is True for r in verified_reviews),
        'source_row_coverage_checks': 'all_mapped_families_match_input_row_counts',
        'new_patterns_approved': 0, 'model_called': False,
        'classification_method': 'source form, original annotation provenance, declared tags and stored metadata',
        'manual_content_review_path': str(root/'manual-content-reviews.json'),
        'cautions': ['Rows mix posts, essays, annotations and revision pairs; do not sum as people or complete applications.',
                     'Fiction-topic advice is not automatically application guidance.',
                     'Existing upstream test essays have already been analyzed; treat them as development data.']}
    atomic_json(root/'evidence-summary.json', summary)
    m.db.close()
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=CACHE/'team-production-20261119/writing')
    parser.add_argument('--collect-primary', action='store_true')
    args = parser.parse_args()
    root = checked_root(args.root)
    (root/'transport').mkdir(exist_ok=True)
    with single_writer(root):
        config = load(CONFIG)
        requests = collect_primary(root, config) if args.collect_primary else 0
        summary = build_map(root)
        summary['network_requests_this_run'] = requests
        atomic_json(root/'evidence-summary.json', summary)
        print(json.dumps({k: summary[k] for k in ('at_kst','mapped_rows','families','scored_unique_essays_across_two_corpora',
            'legacy_eight','semantic_review_of_all_records_performed','network_requests_this_run')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
