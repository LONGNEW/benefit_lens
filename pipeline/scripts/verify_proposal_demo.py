"""Read-only checks plus idempotent replay of this demo's own new tables."""
import json
from pathlib import Path

from data_platform.db import connect
from data_platform.proposal_pipeline import ROOT, normalize_corpus, load_annotations, store_search_waves


def snapshot():
    with connect() as conn:
        return {
            'source_records':conn.execute('SELECT count(*) FROM benefitlens.support_discovery_record').fetchone()[0],
            'source_observations':conn.execute('SELECT count(*) FROM benefitlens.support_discovery_observation').fetchone()[0],
            'legacy_candidates':conn.execute('SELECT count(*) FROM benefitlens.latest_candidate_opportunity').fetchone()[0],
            'opportunities':conn.execute('SELECT count(*) FROM benefitlens.opportunities').fetchone()[0],
            'revisions':conn.execute('SELECT count(*) FROM benefitlens.opportunity_revision').fetchone()[0],
            'work_items':conn.execute('SELECT count(*) FROM benefitlens.pipeline_work_item').fetchone()[0],
            'cases':conn.execute('SELECT sum(jsonb_array_length(experience_cases)) FROM benefitlens.program_knowledge').fetchone()[0],
            'waves':conn.execute('SELECT count(*) FROM benefitlens.pipeline_search_wave').fetchone()[0],
        }


def main():
    output=ROOT/'data/proposal_demo/2026-10-15'
    before=snapshot()
    normalize_corpus(output)
    loaded=load_annotations(output,ROOT/'config/proposal_demo_annotations.json')
    store_search_waves(output)
    after=snapshot()
    assert before==after,(before,after)
    assert not loaded['errors']
    with connect() as conn:
        unresolved=conn.execute('''SELECT count(*) FROM benefitlens.opportunities o,
          jsonb_array_elements(o.source_refs) r WHERE o.extractor='assistant_source_reading_review_v1'
          AND NOT EXISTS(SELECT 1 FROM benefitlens.pipeline_document d WHERE d.document_id=r->>'document_id')''').fetchone()[0]
        assert unresolved==0
    report={'before':before,'after':after,'idempotent':True,'reviewed_source_refs_unresolved':unresolved,
            'scope':'new demo tables only; existing source registry and 124 legacy candidates preserved'}
    (output/'idempotence-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
