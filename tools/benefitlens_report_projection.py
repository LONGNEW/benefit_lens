"""Extract report text while retaining document and archive boundaries."""
import io
import re
import xml.etree.ElementTree as ET
import zipfile

from collect_benefitlens_crossborder import project_original
from run_benefitlens_korea import Held, soup
from scale_benefitlens_experiences import body_text

MAX_XML_BYTES = 32 * 1024**2
MAX_ARCHIVE_MEMBERS = 2048


def _paragraphs(raw):
    if re.search(br'<!\s*(?:DOCTYPE|ENTITY)\b', raw, re.I):
        raise Held('Report XML contains an unsupported document declaration')
    document = ET.fromstring(raw)
    paragraphs = []
    for paragraph in document.iter():
        if paragraph.tag.rsplit('}', 1)[-1] != 'p':
            continue
        text = ''.join(node.text or '' for node in paragraph.iter()
                       if node.tag.rsplit('}', 1)[-1] in ('t', 'Text'))
        if text.strip():
            paragraphs.append(text)
    return '\n'.join(paragraphs)


def project_report(raw, root):
    """Return kind and text; an arbitrary ZIP is never one person's report."""
    if raw.startswith(b'%PDF-'):
        return 'pdf', project_original(raw, root)[1]
    if raw.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        return 'hwp', project_original(raw, root)[1]
    if zipfile.is_zipfile(io.BytesIO(raw)):
        with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
            members = bundle.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise Held('Report container exceeds member ceiling')
            names = [member.filename for member in members]
            if len(names) != len(set(names)):
                raise Held('Report container has ambiguous duplicate members')
            sections = [member for member in members
                        if re.fullmatch(r'Contents/section\d+\.xml', member.filename)]
            if sections:
                kind = 'hwpx'
                sections.sort(key=lambda member: int(re.search(r'(\d+)\.xml$', member.filename)[1]))
            elif 'word/document.xml' in names:
                kind = 'docx'
                sections = [bundle.getinfo('word/document.xml')]
            else:
                raise Held('Archive bundle needs member-level document provenance')
            if sum(member.file_size for member in sections) > MAX_XML_BYTES:
                raise Held('Report XML exceeds decoded byte ceiling')
            if any(member.flag_bits & 1 for member in sections):
                raise Held('Encrypted report container requires publisher-provided access')
            try:
                return kind, '\n'.join(_paragraphs(bundle.read(member)) for member in sections)
            except (ET.ParseError, zipfile.BadZipFile) as error:
                raise Held('Report container text cannot be parsed') from error
    doc = soup(raw)
    node = doc.select_one('.entry-content, .post-content, article, main, #content') or doc
    text = body_text(node)
    title = doc.title.get_text(' ', strip=True) if doc.title else ''
    if doc.select_one('input[type="password"]') and (
            re.search(r'log[ -]?in|sign[ -]?in|single sign|로그인|통합.*인증|認証', title, re.I)
            or len(text.strip()) < 300):
        raise Held('Publisher login page is not a readable report original')
    return 'html', text
