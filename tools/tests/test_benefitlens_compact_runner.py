"""Provider-limit regression: never spend a second call after a known quota stop."""
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
runner=importlib.import_module('run_benefitlens_compact')


class ProviderBudgetTests(unittest.TestCase):
    def test_quota_stops_the_remaining_kind_and_subsequent_invocations(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)/'compact'
            with patch.object(runner,'BASE',Path(tmp)):
                cx=runner.connect(root)
            rep={'document_id':'document-a','char_start':0,'char_end':10}
            group={'group_id':'notice-a','representative':rep,'occurrence_count':1,'source_refs':[]}
            cx.execute("INSERT INTO groups(group_id,kind,group_key,representative_json) VALUES('notice-a','notice','key','{}')")
            cx.execute("INSERT INTO groups(group_id,kind,group_key,representative_json) VALUES('experience-a','experience','key','{}')")
            cx.commit()
            module=SimpleNamespace(build_batch=lambda _:dict(input_sha256='input',request={'system':'test','user':'{}'}),result_schema=lambda:{'type':'object'})
            quota={'api_error_status':429,'api_error':'usage_limit_reached','result':'Provider limit',
                   'modelUsage':{},'usage':{'input_tokens':0,'output_tokens':0},'is_error':True}
            response=subprocess.CompletedProcess([],1,json.dumps(quota),'')
            with patch.object(runner,'select_groups',return_value=([group],{})),patch.object(runner.importlib,'import_module',return_value=module),patch.object(runner.subprocess,'run',return_value=response) as call:
                runner.normalize(cx,root,['notice','experience'],2,40)
                runner.normalize(cx,root,['notice','experience'],2,40)
                self.assertEqual(call.call_count,1)
            self.assertEqual(cx.execute("SELECT state FROM groups WHERE group_id='notice-a'").fetchone()[0],'waiting_provider_limit')
            self.assertEqual(cx.execute("SELECT state FROM groups WHERE group_id='experience-a'").fetchone()[0],'pending')
            state=runner.status(cx,root)
            self.assertEqual(state['cli_attempts'],1)
            self.assertEqual(state['actual_model_calls'],0)


if __name__=='__main__':unittest.main()
