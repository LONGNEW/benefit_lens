"""Validate reviewed Korean opportunity evidence and publish a daily local handoff."""
from __future__ import annotations

import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

from benefitlens_opportunity_groups import norm
from benefitlens_source_storage import read_source_bytes, read_source_text
from run_benefitlens_campaign import KST, atomic_json
from run_benefitlens_korea import ROOT, korean_dates, normalized_link, project_text, soup


def review_dates(period, context, now):
    dates = korean_dates(period)
    if not context:
        return dates
    pattern = r'(?<!\d)(\d{1,2})\s*[./월]\s*(\d{1,2})\s*\(([월화수목금토일])\)'
    if not re.search(fr'(?<!\d){now.year}\s*학년도',context):
        raise ValueError('Year omitted from period without an explicit current academic year')
    matches = re.findall(pattern,period)
    if len(matches) != 2:
        raise ValueError('Yearless period must show two dated weekdays')
    inferred = []
    for month,day,weekday in matches:
        value = datetime(now.year,int(month),int(day)).date()
        if '월화수목금토일'[value.weekday()] != weekday:
            raise ValueError('Yearless period weekday disagrees with original')
        inferred.append(value)
    if inferred[0] > inferred[1]:
        raise ValueError('Yearless period crosses calendar years')
    return dates | set(inferred)


def validate_review(decision, row, selector, now):
    meta, text = row['detail'], row['text']
    if decision['original_sha256'] != meta['sha256'] or meta['status'] != 200:
        raise ValueError('Review original digest or response status did not match')
    if datetime.fromisoformat(meta['at']).astimezone(KST).date() != now.date():
        raise ValueError('Review requires a current KST original')
    raw = read_source_bytes(meta['path'], storage=meta['storage'])
    if sha256(raw).hexdigest() != meta['sha256']:
        raise ValueError('Original publisher bytes changed')
    stored = read_source_text(text['path'], storage=text['storage'])
    projected = project_text(raw,selector)
    if projected != stored or sha256(stored.encode()).hexdigest() != text['sha256']:
        raise ValueError('Text projection does not match original')
    material = {'original':projected}
    related = None
    file_action = row.get('file_action')
    if file_action or any(s.get('document') == 'listing' for s in decision['spans'].values()):
        related = row.get('listing')
        if not related or related['status'] != 200 or not row.get('curated_locator'):
            raise ValueError('Related evidence requires an official attachment listing')
        if (datetime.fromisoformat(related['at']).astimezone(KST).date() != now.date()
                or decision.get('listing_sha256') != related['sha256']):
            raise ValueError('Related evidence capture must match the reviewed day and hash')
        page = read_source_bytes(related['path'],storage=related['storage'])
        anchors = {a['href'] for a in soup(page).select('a[href]')}
        if sha256(page).hexdigest() != related['sha256']:
            raise ValueError('Related listing bytes changed')
        if file_action:
            if file_action not in anchors or meta['method'] != 'POST' or row['detail_form'] != meta['request_data']:
                raise ValueError('Related listing did not authorize the recorded attachment request')
        elif meta['url'] not in {normalized_link(related['url'],href) for href in anchors}:
            raise ValueError('Related listing did not link the original attachment')
        if decision.get('listing_selector'):
            material['listing'] = project_text(page,decision['listing_selector'])
    closed = {'접수마감','모집마감','신청마감','접수 종료'}
    if any(x in closed for text in material.values() for x in text.splitlines()):
        raise ValueError('Publisher marks this opportunity closed')
    required = {'eligibility','period','application'}
    if decision['type'] == 'paid_internship':
        required.add('payment')
    elif decision['type'] not in ('scholarship','loan','student_support'):
        raise ValueError('Unrecognized student opportunity type')
    if 'period_year_context' in decision['spans']:
        required.add('period_year_context')
    if set(decision['spans']) != required:
        raise ValueError('Required eligibility, period, application or payment evidence missing')
    for key, evidence in decision['spans'].items():
        lines = material[evidence.get('document','original')].splitlines()
        start, end = evidence['lines']
        quote = evidence['quote']
        if not 1 <= start <= end <= len(lines) or not quote or quote not in '\n'.join(lines[start-1:end]):
            raise ValueError('Evidence has no exact source span: '+key)
    closing = datetime.fromisoformat(decision['closes_at'])
    opening = datetime.fromisoformat(decision['opens_at']) if decision.get('opens_at') else None
    if closing.tzinfo is None or not now < closing or opening and (opening.tzinfo is None or now < opening):
        raise ValueError('Verified application period is not current')
    actual_dates = review_dates(decision['spans']['period']['quote'],
                                decision['spans'].get('period_year_context',{}).get('quote'),now)
    if closing.astimezone(KST).date() not in actual_dates:
        raise ValueError('Closing is absent from its quoted text')
    if opening and opening.astimezone(KST).date() not in actual_dates:
        raise ValueError('Opening is absent from its quoted text')
    if not opening and not decision.get('opening_status_note'):
        raise ValueError('An unstated start needs an explicit review note')
    if not all(decision.get(k) for k in ('issuer','role','review_note','application_channel')):
        raise ValueError('Missing normalized issuer, role, application channel or review decision')
    official_url = related['url'] if file_action else row['url']
    record = {**decision,'official_url':official_url,'original':meta,'text_projection':text}
    if related:
        record['related_listing'] = related
    return record


def validate_visual_review(decision, now):
    main, image = decision['official_html'], decision['official_png']
    for capture in (main,image):
        if (capture['status'] != 200 or
                datetime.fromisoformat(capture['at']).astimezone(KST).date() != now.date()):
            raise ValueError('Visual review requires a current publisher capture')
    page = read_source_bytes(main['path'],storage=main['storage'])
    content = read_source_bytes(image['path'],storage=image['storage'])
    if (sha256(page).hexdigest() != main['sha256'] or
            sha256(content).hexdigest() != image['sha256'] or
            not content.startswith(b'\x89PNG\r\n\x1a\n')):
        raise ValueError('Visual review capture bytes changed')
    if image['url'] not in {normalized_link(main['url'],element['src'])
                            for element in soup(page).select('img[src]')}:
        raise ValueError('Poster missing from the official recruitment page')
    evidence = decision['evidence']
    if not all(evidence.get(field) for field in ('period','eligibility','payment','application')):
        raise ValueError('Visual review lacks required evidence')
    dates = korean_dates(evidence['period'])
    opening = datetime.fromisoformat(decision['opens_at']).astimezone(KST)
    closing = datetime.fromisoformat(decision['closes_at']).astimezone(KST)
    if not (opening <= now < closing and opening.date() in dates and closing.date() in dates
            and '지원하기' in project_text(page,'body')):
        raise ValueError('Visual review application is not current')
    return {**decision, 'original':image, 'related_listing':main}


def publish(root, review, specs, now, *, candidate_roots=None):
    if review['as_of_kst'] != now.date().isoformat():
        raise ValueError('Review date does not match the current KST day')
    if not review.get('reviewed_by'):
        raise ValueError('Named reviewer required')
    candidate_roots = candidate_roots or {}
    items = {}
    for directory in {root, *candidate_roots.values()}:
        with sqlite3.connect((directory/'korea.sqlite').as_uri()+'?mode=ro',uri=True) as db:
            db.execute('BEGIN')
            for source,native,row in db.execute(
                    'SELECT source,native,data FROM candidates WHERE day=?',(review['as_of_kst'],)):
                if candidate_roots.get(source,root) == directory:
                    items[source,native] = json.loads(row)
    accepted, duplicates, parents = [], [], set()
    for decision in review['decisions']:
        source, native = decision['source'], decision['native']
        if decision.get('evidence_method') == 'publisher_poster_visual':
            record = validate_visual_review(decision,now)
        else:
            row = items[source,native]
            selector = row.get('selector', specs[source].get('content','main,body'))
            record = validate_review(decision,row,selector,now)
        key = (norm(decision['issuer']),norm(decision['role']))
        if key in parents:
            duplicates.append({'source':source,'native':native,'key':key})
        else:
            parents.add(key)
            accepted.append(record)
    additions = [row for row in accepted if not row.get('existing_reference')]
    receipt = {'as_of_kst':review['as_of_kst'],'reviewed_by':review['reviewed_by'],
               'checks':'same day publisher originals, exact quotations, deadline, normalized issuer and role',
               'rows':accepted,'new_count':len(additions),
               'existing_count':len(accepted)-len(additions),
               'duplicates':duplicates,'production_database_writes':0}
    digest = sha256(json.dumps(receipt,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
    out = root/'verified'/review['as_of_kst']
    out.mkdir(parents=True,exist_ok=True)
    path = out/f'{digest}.json'
    atomic_json(path,receipt)
    atomic_json(out/'summary.json',{'latest_receipt':str(path.resolve()),
                'current_verified':len(accepted),'new_count':receipt['new_count'],
                'existing_count':receipt['existing_count'],'duplicates':len(duplicates)})
    return path,len(accepted),len(duplicates)


if __name__=='__main__':
    from run_benefitlens_korea import CONFIG, available_sources
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review',type=Path,required=True)
    parser.add_argument('--extra-config',action='append',type=Path,default=[])
    parser.add_argument('--candidate-root',action='append',nargs=2,default=[],metavar=('SOURCE','PATH'))
    args = parser.parse_args()
    review = json.loads(args.review.read_text())
    specs = {s['id']:s for config in (CONFIG, *args.extra_config)
             for s in available_sources(ROOT, config)}
    roots = {source:Path(path).resolve() for source,path in args.candidate_root}
    if not all(p.is_relative_to(ROOT.parent.resolve()) for p in roots.values()):
        parser.error('Candidate roots must be inside the shared BenefitLens dataset root')
    print(*publish(ROOT,review,specs,datetime.now(KST),candidate_roots=roots))
