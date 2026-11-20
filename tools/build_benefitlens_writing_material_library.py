"""Build a source-reading library with distinct materials and a separate 3:1 audit.

Uses existing captured source bytes. No network, DB promotion or model calls.
Candidate materials, reviewed examples and people are separate counting units.
"""
from collections import Counter
from datetime import datetime
from hashlib import sha256
from html import escape
import argparse
import json
from pathlib import Path
import re
import shutil
import unicodedata
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from benefitlens_question_evidence import locate_quote
from benefitlens_source_storage import read_source_bytes, store_source_bytes
from run_benefitlens_campaign import CACHE, KST, atomic_json

REPO = Path(__file__).resolve().parents[1]
GROUPS = {'writing_advice': '작성 조언과 후기',
          'requirements_and_format': '요구사항과 형식',
          'actual_writing': '공개된 실제 작성본', 'reviewer_feedback': '심사 의견'}
ROLE = {'application_bundle': 'actual_writing', 'application_statement': 'actual_writing',
        'personal_statement': 'actual_writing', 'research_statement': 'actual_writing',
        'official_guidance': 'requirements_and_format', 'reviewer_guidance': 'requirements_and_format',
        'writing_guide': 'requirements_and_format', 'reviewer_feedback': 'reviewer_feedback'}
ALIASES = {'chevening': 'chevening-scholarships'}


def canonical_url(url):
    p = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.startswith('utm_') and k != 'usp']
    return urlunsplit(('https', p.netloc.lower(), unquote(p.path).rstrip('/'), urlencode(query), ''))


def text_key(text):
    normalized = ' '.join(unicodedata.normalize('NFKC', text).split())
    return sha256(normalized.encode()).hexdigest()


def deduplicate(rows):
    """Union URL and complete-text identity, including transitive aliases."""
    parents = list(range(len(rows)))
    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]; i = parents[i]
        return i
    seen = {}
    for i, row in enumerate(rows):
        for key in [('url', canonical_url(row['url'])), ('text', row['normalized_text_sha256'])]:
            if key in seen:
                a, b = find(i), find(seen[key]); parents[a] = b
            else:
                seen[key] = i
    groups = {}
    for i, row in enumerate(rows):
        groups.setdefault(find(i), []).append(row)
    return list(groups.values())


def verified(ref):
    if isinstance(ref, str):
        return read_source_bytes(ref)
    raw = read_source_bytes(ref['path'], storage=ref.get('storage'))
    if sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('Decoded source hash mismatch: ' + ref['path'])
    return raw


def document(ref):
    return json.loads(verified(ref))


def load_candidates():
    rows = []; exclusions = []
    review = document(str(CACHE / 'question-knowledge-v1/relevance-sample-review.json'))
    excluded_questions = {r['question_id']: r for r in review['reviews'] if r['decision'] == 'exclude'}
    def add(key, url, kind, ref, *, programs=(), raw=None, title=None, original_question=None, scope=None,
            author=None, content_license=None):
        body = verified(ref)
        text = body.decode('utf8')
        rows.append({'source_key': key, 'url': url, 'kind': kind, 'text_ref': ref,
                     'normalized_text_sha256': text_key(text), 'text_sha256': sha256(body).hexdigest(),
                     'program_ids': [ALIASES.get(p, p) for p in programs if p],
                     'raw_ref': raw, 'title': title or unquote(urlsplit(url).path.rsplit('/', 1)[-1]),
                     'text': text, 'original_question': original_question, 'source_scope': scope,
                     'author': author, 'content_license': content_license})
    status = document(str(CACHE / 'writing-corpus-expansion-v1/status.json'))
    corpus = document(status['corpus'])
    for d in corpus['source_documents']:
        if d.get('role') in ROLE:
            add(d['document_id'], d['url'], ROLE[d['role']], d['text'],
                programs=[d.get('program_id')], raw=d['raw'], scope=d.get('document_finality'),
                author=sorted({x['person_name'] for x in d.get('case_bindings',[]) if x.get('person_name')}))
    for d in document(str(CACHE / 'experience-application-expansion-v1/ucr-application-samples/index.json')):
        if d['state'] == 'body_staged':
            add('ucr:' + d['id'], d['url'], 'actual_writing', d['text'],
                programs=[d['program_id']], raw=d['original'], title=d['title'], scope=d['submission_scope'],
                author=d['person_name'])
    for d in document(str(CACHE / 'question-knowledge-v1/records.json')):
        if d['body_scope'] == 'full_answer' and str(d['question_id']) in excluded_questions:
            exclusions.append({'source_key':d['document_id'],'question_id':str(d['question_id']),
                               'reason':excluded_questions[str(d['question_id'])]['reason']})
            continue
        if d['body_scope'] == 'full_answer' or d['source_role'] == 'official_policy':
            kind = 'writing_advice' if d['body_scope'] == 'full_answer' else 'requirements_and_format'
            add(d['document_id'], d['url'], kind, d['text_ref'], programs=[d.get('program_id')],
                title=d['title'], original_question=d.get('original_question'), scope=d.get('guidance_edition'),
                author=d.get('author'), content_license=d.get('content_license'))
    bundle = document(str(CACHE / 'question-evidence-v1/bundle.json'))
    docs = {d['document_id']: d for d in bundle['source_documents']}
    for row in bundle['question_evidence']:
        d = docs[row['document_id']]
        kind = 'requirements_and_format' if row['source_role'] == 'official_policy' else 'writing_advice'
        add(row['evidence_id'], d['url'], kind,
            {'path': d['text_ref'], 'sha256': d['text_sha256']}, original_question=row['original_question'])
    return rows, exclusions


def select_examples(rows, specs):
    index = {r['source_key']: r for r in rows}; selected = []
    for spec in specs:
        row = index[spec['key']]
        if len(spec['quote'].split()) > 20:
            raise ValueError('Showcase quote exceeds twenty words')
        locator = locate_quote(row['text'], spec['quote'])
        selected.append({**row, **spec, 'locator': locator, 'review_state': 'agent_reviewed_source_utility',
                         'program_ids': spec.get('program_ids', row['program_ids']),
                         'review_scope': 'Source supports stated reading purpose; paragraph arrangement is editorial.'})
    if len(deduplicate(selected)) != len(selected):
        raise ValueError('Showcase contains duplicate original materials')
    return selected


def material_coverage(notice_candidates, reviewed_count, ratio):
    if type(ratio) is not int or ratio < 3:
        raise ValueError('Writing material ratio must be at least three')
    target = notice_candidates * ratio
    return {'planning_notice_candidates': notice_candidates, 'target_materials': target,
            'reviewed_showcase_materials': reviewed_count, 'remaining_against_showcase': max(0, target-reviewed_count),
            'global_ratio_met': bool(notice_candidates) and reviewed_count >= target,
            'per_notice_relevance_verified': False, 'completion_met': False,
            'denominator_scope': 'Planning inventory, not verified currently-open deduplicated notices.',
            'review_scope': 'This reviewed showcase only; no completed review count inferred for the rest of the corpus.'}


def preview(row, root):
    path = root / 'previews' / (row['text_sha256'] + '.txt')
    path.parent.mkdir(exist_ok=True)
    path.write_text(row['text'])
    if row['kind'] == 'actual_writing' and row.get('raw_ref'):
        raw = verified(row['raw_ref'])
        if raw.startswith(b'%PDF-'):
            path = root / 'previews' / (sha256(raw).hexdigest() + '.pdf')
            path.write_bytes(raw)
    return path


STYLE = '''body{font:17px/1.6 system-ui,sans-serif;color:#18314a;background:#f4f6fa;max-width:1120px;margin:32px auto;padding:0 20px}h1{font-size:30px}h2{font-size:23px}p{margin:8px 0}nav{position:sticky;top:0;padding:12px;background:#f4f6fa;z-index:1}button,input{font:inherit;padding:8px;margin:3px;border:1px solid #abbccc;border-radius:7px}article{background:white;border:1px solid #dbe2eb;border-radius:12px;padding:19px;margin:16px 0}a{color:#175bb5}small{color:#476078}blockquote{border-left:3px solid #8aa9cb;margin:12px 0;padding-left:14px}li{margin:12px 0}'''


def write_html(path, title, body):
    text = '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + escape(title) + '</title><style>' + STYLE + '</style><main>' + body + '</main></html>'
    if len(text) > 10000 or '\u00b7' in text:
        raise ValueError('Generated reading document violates document rules: ' + path.name)
    path.write_text(text)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=CACHE / 'writing-material-library-v1')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--coverage', type=Path, required=True)
    args = p.parse_args()
    args.root.resolve().relative_to(CACHE.resolve())
    if shutil.disk_usage(CACHE).free < 512*1024**2:
        raise ValueError('Shared cache reserve')
    args.root.mkdir(parents=True, exist_ok=True); args.output.parent.mkdir(parents=True, exist_ok=True)
    policy = json.loads((REPO / 'pipeline/config/writing_material_coverage_v1.json').read_text())
    specs = json.loads((REPO / 'pipeline/config/writing_material_showcase_v1.json').read_text())
    rows, exclusions = load_candidates(); groups = deduplicate(rows)
    selected = select_examples(rows, specs)
    index = {}; assets = []
    for group in groups:
        asset = max(group, key=lambda r: len(r['text']))
        aid = sha256(canonical_url(asset['url']).encode()).hexdigest()[:20]
        item = {k:v for k,v in asset.items() if k != 'text'}
        item.update(material_id=aid, source_keys=[r['source_key'] for r in group],
                    snapshot_text_hashes=sorted({r['text_sha256'] for r in group}),
                    review_state='candidate', independent_applicants=None)
        assets.append(item)
        for row in group: index[row['source_key']] = item
    for row in selected:
        item = index[row['source_key']]
        item['review_state'] = row['review_state']
        item['showcase_title'] = row['title']
        item['preview'] = str(preview(row, args.root))
        item['reviewed_source_key'] = row['source_key']
        item['reviewed_program_ids'] = row['program_ids']
    sample_assets = [a for a in assets if a['kind'] == 'actual_writing']
    preview_dir = args.output.parent / (args.output.stem + '_저장본')
    if preview_dir.is_symlink():
        if preview_dir.resolve() != (args.root / 'previews').resolve():
            raise ValueError('Existing reader preview link points to another dataset')
    elif preview_dir.exists():
        raise ValueError('Reader preview destination is an existing directory')
    else:
        preview_dir.symlink_to(args.root / 'previews', target_is_directory=True)
    for a in sample_assets:
        if not a.get('preview'):
            source = next(r for r in rows if r['source_key'] == a['source_key'])
            a['preview'] = str(preview(source, args.root))
    all_pages = []
    for start in range(0, len(sample_assets), 12):
        path = args.output.with_name(args.output.stem + f'_작성본{start//12+1}.html')
        entries = []
        for a in sample_assets[start:start+12]:
            label = a.get('showcase_title') or a['title'] or a['program_ids'][0]
            local = preview_dir.name + '/' + Path(a['preview']).name
            entries.append('<li><a target="_blank" rel="noopener" href="'+escape(a['url'],quote=True)+'">'+escape(label)+'</a> / <a href="'+escape(local,quote=True)+'">저장본</a><br><small>'+escape(', '.join(a['program_ids']))+' | '+('대표 검토 사례' if a['review_state'] != 'candidate' else '작성본 후보, 의미 검토 대기')+'</small></li>')
        write_html(path, '공개 작성본 전체 목록', '<h1>공개 작성본 '+str(len(sample_assets))+'개</h1><p>문서 단위 목록이다. 한 사람의 여러 문서와 묶음에 포함된 문서는 독립 지원자 수로 합산하지 않는다.</p><ol start="'+str(start+1)+'">'+''.join(entries)+'</ol>')
        all_pages.append(path)
    cov = json.loads(args.coverage.read_text())
    coverage = material_coverage(cov['planning_notice_candidates'], len(selected), policy['minimum_materials_per_notice'])
    cards = []; short_cards = []; category_pages = []
    for i, row in enumerate(selected, 1):
        a = index[row['source_key']]
        local = preview_dir.name + '/' + Path(a['preview']).name
        category_path = args.output.with_name(args.output.stem+'_'+row['kind']+'.html')
        full='<article id="'+a['material_id']+'" data-kind="'+row['kind']+'"><small>'+GROUPS[row['kind']]+'</small><h2>'+str(i)+'. '+escape(row['title'])+'</h2><p>'+escape(row['note'])+'</p><blockquote lang="en">'+escape(row['quote'])+'</blockquote><small>'+escape(row['scope'])+'</small><p><a target="_blank" rel="noopener" href="'+escape(row['url'],quote=True)+'">원문 열기</a> / <a href="'+escape(local,quote=True)+'">저장한 PDF 또는 본문</a></p></article>'
        cards.append((row['kind'],full))
        short_cards.append('<article data-kind="'+row['kind']+'"><small>'+GROUPS[row['kind']]+'</small><p>'+str(i)+'. <a href="'+escape(category_path.name,quote=True)+'#'+a['material_id']+'">'+escape(row['title'])+'</a> / <a href="'+escape(local,quote=True)+'">저장본</a></p></article>')
    for kind in dict.fromkeys(r['kind'] for r in selected):
        path=args.output.with_name(args.output.stem+'_'+kind+'.html')
        write_html(path,GROUPS[kind],'<p><a href="'+escape(args.output.name,quote=True)+'">전체 목록</a></p><h1>'+GROUPS[kind]+'</h1>'+''.join(c for k,c in cards if k==kind))
        category_pages.append(str(path))
    links=' / '.join('<a href="'+escape(path.name,quote=True)+'">'+str(i)+'쪽</a>' for i,path in enumerate(all_pages,1))
    intro='<h1>작성 자료 원문 18개</h1><p>조언 6개, 요구사항과 형식 6개, 실제 작성본 6개를 직접 비교한다. 인용은 원문에서 확인했고 설명은 팀의 해석이다.</p><p>공개 작성본 전체 '+str(len(sample_assets))+'개: '+links+'</p><p>공고 후보 '+format(coverage['planning_notice_candidates'],',')+'개 기준 3배수 계획은 '+format(coverage['target_materials'],',')+'개다. 이 18개는 대표 열람 묶음이며 전체 목표는 미달이다.</p><nav><button data-filter="">전체</button><button data-filter="writing_advice">조언</button><button data-filter="requirements_and_format">요구사항과 형식</button><button data-filter="actual_writing">실제 작성본</button><input aria-label="자료 검색" placeholder="문항이나 프로그램 검색"></nav>'
    js='''<script>let kind='';function show(){let q=document.querySelector('input').value.toLowerCase();document.querySelectorAll('article').forEach(a=>a.hidden=!!((kind&&a.dataset.kind!==kind)||!a.textContent.toLowerCase().includes(q)))}document.querySelectorAll('button').forEach(b=>b.onclick=()=>{kind=b.dataset.filter;show()});document.querySelector('input').oninput=show;</script>'''
    write_html(args.output, 'BenefitLens 작성 자료 원문 보기', intro+''.join(short_cards)+js)
    sanitized_selected=[{k:v for k,v in r.items() if k != 'text'} for r in selected]
    corpus=store_source_bytes(json.dumps({'assets':assets,'selected':sanitized_selected,'out_of_scope':exclusions},ensure_ascii=False).encode(),args.root/'blobs',suffix='.json')
    summary={'at_kst':datetime.now(KST).isoformat(),'input_source_rows':len(rows),'distinct_material_candidates':len(assets),
             'removed_duplicate_source_rows':len(rows)-len(assets),'excluded_answer_documents':len(exclusions),
             'candidate_roles':dict(Counter(a['kind'] for a in assets)),
             'reviewed_showcase':dict(Counter(r['kind'] for r in selected)), 'reviewed_showcase_total':len(selected),
             'independent_applicants':None,'coverage':coverage,'corpus':corpus,'reader':str(args.output),'all_sample_readers':[str(p) for p in all_pages],
             'category_readers':category_pages,'network_called':False,'database_written':False,'model_batch_called':False}
    atomic_json(args.root/'status.json',summary)
    print(json.dumps(summary,ensure_ascii=False))


if __name__ == '__main__': main()
