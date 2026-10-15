"""Loopback-only, read-only HTTP preview of the actual PostgreSQL demo tables."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .db import connect

ASSETS = Path(__file__).resolve().parents[3] / 'web/pipeline-demo'


def query_api(path, query):
    with connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        if path == '/api/pipeline/stats':
            return {
                'as_of': '2026-10-15', 'storage': 'PostgreSQL',
                'source_records': conn.execute('SELECT count(*) FROM benefitlens.support_discovery_record').fetchone()[0],
                'normalized_records': conn.execute('SELECT count(*) FROM benefitlens.opportunities').fetchone()[0],
                'reviewed_demo': conn.execute("SELECT count(*) FROM benefitlens.opportunities WHERE extractor IN ('assistant_source_reading_review_v1','public_demo_fixture_v1')").fetchone()[0],
                'public_sample': conn.execute("SELECT count(*) FROM benefitlens.opportunities WHERE extractor='public_demo_fixture_v1'").fetchone()[0],
                'work_items': dict(conn.execute('SELECT status,count(*) FROM benefitlens.pipeline_work_item GROUP BY status').fetchall()),
                'source_document_versions':conn.execute('SELECT count(*) FROM benefitlens.pipeline_document').fetchone()[0],
                'knowledge_programs':conn.execute('SELECT count(*) FROM benefitlens.program_knowledge').fetchone()[0],
                'experience_cases':conn.execute('SELECT coalesce(sum(jsonb_array_length(experience_cases)),0) FROM benefitlens.program_knowledge').fetchone()[0],
                'categories':dict(conn.execute('SELECT category,count(*) FROM benefitlens.opportunities GROUP BY category').fetchall()),
                'search_waves':[r[0] for r in conn.execute('SELECT payload FROM benefitlens.pipeline_search_wave ORDER BY run_id,wave')],
            }
        if path == '/api/pipeline/detail':
            key = query.get('id', [''])[0][:200]
            row = conn.execute('''SELECT o.payload,o.version_hash,k.payload
              FROM benefitlens.opportunities o LEFT JOIN benefitlens.program_knowledge k USING(program_id)
              WHERE opportunity_id=%s''',(key,)).fetchone()
            if not row:
                raise KeyError('공고를 찾을 수 없습니다.')
            return {'data':row[0], 'version_hash':row[1], 'knowledge':row[2]}
        if path == '/api/pipeline/opportunities':
            q = query.get('q',[''])[0][:100]
            category = query.get('category',['all'])[0]
            scope = query.get('scope',['reviewed'])[0]
            try:
                page = max(1,min(10000,int(query.get('page',['1'])[0])))
            except ValueError:
                page = 1
            where, params = [], []
            if scope != 'all':
                where.append("extractor IN ('assistant_source_reading_review_v1','public_demo_fixture_v1')")
            if category != 'all':
                where.append('category=%s'); params.append(category)
            if q:
                where.append("(title ILIKE %s OR issuer->>'name' ILIKE %s)")
                params.extend(['%' + q + '%']*2)
            clause = ' AND '.join(where) or 'TRUE'
            total = conn.execute('SELECT count(*) FROM benefitlens.opportunities WHERE ' + clause,params).fetchone()[0]
            page = min(page,max(1,(total+11)//12))
            rows = conn.execute('''SELECT payload,version_hash FROM benefitlens.opportunities WHERE '''+clause+'''
               ORDER BY (extractor='assistant_source_reading_review_v1') DESC,title,opportunity_id LIMIT 12 OFFSET %s''',params+[(page-1)*12]).fetchall()
            return {'data':[dict(r[0],version_hash=r[1]) for r in rows],
                    'meta':{'total':total,'page':page,'page_size':12,'storage':'PostgreSQL','scope':scope}}
    raise KeyError('알 수 없는 경로입니다.')


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        try:
            if url.path.startswith('/api/pipeline/'):
                body = json.dumps(query_api(url.path,parse_qs(url.query)),ensure_ascii=False).encode()
                mime = 'application/json; charset=utf-8'
            else:
                name = 'index.html' if url.path in {'/','/benefitlens','/benefitlens/','/benefitlens/pipeline'} else url.path.removeprefix('/assets/') if url.path.startswith('/assets/') else ''
                path = (ASSETS / name).resolve()
                if not name or not path.is_relative_to(ASSETS.resolve()) or not path.is_file():
                    raise KeyError('페이지가 없습니다.')
                body = path.read_bytes()
                mime = (mimetypes.guess_type(path)[0] or 'application/octet-stream') + '; charset=utf-8'
            self.send_response(200)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers(); self.wfile.write(body)
        except KeyError as error:
            self.send_error(404,'Not found',explain=str(error))
        except Exception:
            self.send_error(503,'Database unavailable',explain='DB 조회 실패. 실행 로그와 연결 상태를 확인하세요.')

    def log_message(self,fmt,*args):
        # Do not log search inputs or potential profile information.
        pass


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--port',type=int,default=5179)
    args = p.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'Local: http://127.0.0.1:{args.port}/benefitlens',flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
