"""Describe writing-pattern evidence without treating labels as admissions effects."""
from collections import Counter,defaultdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import statistics

from collect_benefitlens_writing_foundations import ROOT
from run_benefitlens_campaign import KST,atomic_json

OUTPUT=ROOT/'pattern-analysis-v1'
ROLES=('Lead','Position','Claim','Evidence','Counterclaim','Rebuttal','Concluding Statement')


def fraction(n,d):
    return {'count':n,'denominator':d,'percent':round(100*n/d,2) if d else None}


def ranks(values):
    ordered=sorted(enumerate(values),key=lambda x:x[1]);result=[0.0]*len(values);i=0
    while i<len(ordered):
        j=i+1
        while j<len(ordered) and ordered[j][1]==ordered[i][1]:j+=1
        rank=(i+1+j)/2
        for k in range(i,j):result[ordered[k][0]]=rank
        i=j
    return result


def db(path):
    cx=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True);cx.row_factory=sqlite3.Row
    return cx


def fingerprint(path):
    digest=sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(8*1024**2),b''):digest.update(chunk)
    return {'path':str(path),'bytes':path.stat().st_size,'sha256':digest.hexdigest()}


def essay_analysis():
    cx=db(ROOT/'persuade/persuade-test.sqlite')
    essays={r['text_key']:dict(r) for r in cx.execute('SELECT text_key,native_id,prompt_name,full_text,holistic_score FROM essay')}
    segments=defaultdict(list)
    for r in cx.execute("SELECT text_key,discourse_type,verified_start,verified_end,effectiveness FROM discourse WHERE verified_start IS NOT NULL AND discourse_type!='Unannotated' ORDER BY verified_start,verified_end"):
        segments[r['text_key']].append(dict(r))
    by_group={};role_counts=Counter();first=Counter();last=Counter();transitions=Counter();overlap=0
    for key,e in essays.items():
        segs=segments[key];types={s['discourse_type'] for s in segs};e['roles']=types
        role_counts.update(types)
        if not segs:continue
        first[segs[0]['discourse_type']]+=1;last[segs[-1]['discourse_type']]+=1
        if any(a['verified_end']>b['verified_start'] for a,b in zip(segs,segs[1:])):overlap+=1
        for a,b in zip(segs,segs[1:]):
            if a['verified_end']<=b['verified_start']:
                transitions[(a['discourse_type'],b['discourse_type'])]+=1
    for name,predicate in [('all',lambda e:True),('score_5_or_6',lambda e:e['holistic_score']>=5),('score_1_or_2',lambda e:e['holistic_score']<=2)]:
        chosen=[e for e in essays.values() if predicate(e)]
        by_group[name]={'essays':len(chosen),'median_words':statistics.median(len(e['full_text'].split()) for e in chosen),
                        'role_presence':{role:fraction(sum(role in e['roles'] for e in chosen),len(chosen)) for role in ROLES}}
    # First/last labeled roles are source labels, not inferred rhetorical quality.
    result={'essays':len(essays),'groups':by_group,'first_labeled_role_counts':dict(first),
            'last_labeled_role_counts':dict(last),'essays_with_overlapping_verified_role_spans':overlap,
            'non_overlapping_adjacent_role_counts':[{'from':a,'to':b,'count':n} for (a,b),n in transitions.most_common(20)],
            'quality_by_role':{},'interpretation':'Descriptive source-label counts. Score groups differ in length and prompt; not an effect estimate.'}
    for r in cx.execute("SELECT discourse_type,effectiveness,count(*) n FROM discourse WHERE verified_start IS NOT NULL AND effectiveness!='' GROUP BY discourse_type,effectiveness"):
        result['quality_by_role'].setdefault(r['discourse_type'],{})[r['effectiveness']]=r['n']
    cx.close();return result


def proficiency_analysis():
    cx=db(ROOT/'ellipse/ellipse.sqlite')
    rows=[json.loads(r['scores_json']) for r in cx.execute('SELECT scores_json FROM essay')]
    traits=('Cohesion','Syntax','Vocabulary','Phraseology','Grammar','Conventions');correlations={}
    for trait in traits:
        pairs=[(r['Overall'],r[trait]) for r in rows if r.get('Overall') is not None and r.get(trait) is not None]
        correlations[trait]={'n':len(pairs),'spearman_rho':round(statistics.correlation(ranks([p[0] for p in pairs]),ranks([p[1] for p in pairs])),4)}
    cx.close();return {'essays':len(rows),'overall_trait_correlations':correlations,
                      'interpretation':'Within-corpus descriptive associations among language-proficiency scores, not calibrated application-rubric weights.'}


def label_agreement(pairs):
    labels=sorted({x for p in pairs for x in p});metrics={}
    for label in labels:
        tp=sum(a==label and b==label for a,b in pairs)
        fp=sum(a!=label and b==label for a,b in pairs)
        fn=sum(a==label and b!=label for a,b in pairs)
        metrics[label]={'human_support':tp+fn,'predicted_count':tp+fp,
                        'f1':round(2*tp/(2*tp+fp+fn),4) if 2*tp+fp+fn else None}
    return {'matched_pairs':len(pairs),'agreement':round(sum(a==b for a,b in pairs)/len(pairs),4) if pairs else None,'per_label':metrics}


def revision_analysis():
    cx=db(ROOT/'revisions/revisions.sqlite')
    labels={};human=defaultdict(set);test=defaultdict(set);automatic=defaultdict(set)
    for r in cx.execute("SELECT pair_key,variant,source_split,edit_intent FROM observation WHERE corpus='iterater' AND variant IN ('human_sent_level','full_sent_level')"):
        label=json.loads(r['edit_intent']);label=json.dumps(label,ensure_ascii=False) if not isinstance(label,str) else label
        if r['variant']=='human_sent_level':
            human[r['pair_key']].add(label)
            if r['source_split']=='test':test[r['pair_key']].add(label)
        else:automatic[r['pair_key']].add(label)
    comparisons={}
    for name,source in [('all_human_exact_pair_overlap',human),('human_test_exact_pair_overlap',test)]:
        common=source.keys()&automatic.keys();clean=[k for k in common if len(source[k])==len(automatic[k])==1]
        comparisons[name]={**label_agreement([(next(iter(source[k])),next(iter(automatic[k]))) for k in clean]),
                           'overlap_pairs':len(common),'ambiguous_label_pairs_excluded':len(common)-len(clean)}
    for origin,intent,n in cx.execute('SELECT label_origin,edit_intent,count(*) FROM observation GROUP BY label_origin,edit_intent'):
        labels.setdefault(origin,{})[json.loads(intent) if json.loads(intent) is not None else 'document_edit_actions']=n
    cx.close()
    return {'annotation_label_counts':labels,'comparisons':comparisons,
            'interpretation':'Agreement on exact matching released pairs only. Human labels are edit intentions, not proof of improvement. FULL training provenance and subset selection prevent treating this as a fresh model benchmark.'}


def main():
    OUTPUT.mkdir(exist_ok=True)
    result={'at_kst':datetime.now(KST).isoformat(),'persuade':essay_analysis(),'ellipse':proficiency_analysis(),
            'revision':revision_analysis(),'model_called':False,'semantic_classification_of_all_applications_performed':False,
            'databases':[fingerprint(ROOT/p) for p in ('persuade/persuade-test.sqlite','ellipse/ellipse.sqlite','revisions/revisions.sqlite')]}
    atomic_json(OUTPUT/'analysis.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='databases'},ensure_ascii=False))


if __name__=='__main__':main()
