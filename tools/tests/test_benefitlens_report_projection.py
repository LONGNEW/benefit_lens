import io
from pathlib import Path
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_report_projection import project_report
from run_benefitlens_korea import Held


def container(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries:
            archive.writestr(name, body)
    return stream.getvalue()


def test_hwpx_preserves_paragraph_and_numeric_section_order(tmp_path):
    def section(text):
        return f'<sec xmlns:h="urn:h"><h:p><h:run><h:t>{text}</h:t></h:run></h:p></sec>'
    raw = container([('Contents/section10.xml', section('Conclusion')),
                     ('Contents/section2.xml', section('Evidence')),
                     ('Contents/section0.xml', section('Introduction'))])
    assert project_report(raw, tmp_path) == ('hwpx', 'Introduction\nEvidence\nConclusion')


def test_docx_retains_paragraph_boundaries_and_text_runs(tmp_path):
    raw = container([('word/document.xml', '<w:document xmlns:w="urn:w"><w:body>'
        '<w:p><w:r><w:t>My </w:t></w:r><w:r><w:t>experience</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>Next paragraph</w:t></w:r></w:p></w:body></w:document>')])
    assert project_report(raw, tmp_path) == ('docx', 'My experience\nNext paragraph')


def test_bundle_of_people_is_not_concatenated_into_one_report(tmp_path):
    raw = container([('Alice/report.txt', 'My application'), ('Bob/report.txt', 'My application')])
    with pytest.raises(Held, match='member-level'):
        project_report(raw, tmp_path)


def test_container_xml_declarations_are_rejected(tmp_path):
    raw = container([('word/document.xml', '<!DOCTYPE foo [<!ENTITY x "repeated">]><p><t>&x;</t></p>')])
    with pytest.raises(Held, match='declaration'):
        project_report(raw, tmp_path)


def test_login_html_returned_instead_of_pdf_is_not_writing_material(tmp_path):
    raw = ('<title>University Login</title><main><form><input type="password"></form>'
           + 'Terms of the student portal. ' * 50 + '</main>').encode()
    with pytest.raises(Held, match='login page'):
        project_report(raw, tmp_path)
