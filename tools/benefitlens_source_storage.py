"""Lossless source blobs. Content hashes always describe decoded source bytes."""
from __future__ import annotations

import gzip
from hashlib import sha256
import io
import os
from pathlib import Path
import tempfile
import zlib

MAX_DECODED_BYTES = 256 * 1024 * 1024


def source_fetched_at(reference):
    """Use a recorded retrieval time, including the receipt of a bundle member."""
    for key in ('at', 'at_kst', 'fetched_at'):
        if reference.get(key):
            return reference[key]
    for key in ('archive_receipt', 'api_receipt', 'feed_receipt'):
        if isinstance(reference.get(key), dict):
            try:
                return source_fetched_at(reference[key])
            except ValueError:
                pass
    raise ValueError('Source retrieval timestamp is absent')


def read_source_bytes(path, *, storage=None, max_decoded_bytes=MAX_DECODED_BYTES):
    """Read plain or .gz source without changing its original byte sequence."""
    path = Path(path)
    stored = path.read_bytes()
    codec = 'gzip' if path.suffix == '.gz' else 'identity'
    if storage is not None:
        if storage.get('codec') != codec:
            raise ValueError('Source storage codec mismatch')
        if storage.get('sha256') != sha256(stored).hexdigest():
            raise ValueError('Source storage hash mismatch')
        if storage.get('stored_bytes') != len(stored):
            raise ValueError('Source stored byte count mismatch')
    try:
        if codec == 'gzip':
            with gzip.GzipFile(fileobj=io.BytesIO(stored)) as stream:
                content = stream.read(max_decoded_bytes + 1)
        else:
            content = stored
    except (OSError, EOFError, zlib.error) as exc:
        raise ValueError('Invalid or truncated gzip source') from exc
    if len(content) > max_decoded_bytes:
        raise ValueError('Source exceeds decoded byte limit')
    if storage is not None and storage.get('decoded_bytes') != len(content):
        raise ValueError('Source decoded byte count mismatch')
    return content


def read_source_text(path, *, storage=None):
    return read_source_bytes(path, storage=storage).decode('utf-8')


def source_suffix(path):
    path = Path(path)
    return Path(path.stem).suffix if path.suffix == '.gz' else path.suffix


def store_source_bytes(content, directory, *, suffix='.raw', compression_level=6):
    """Atomically deduplicate exact original bytes; never overwrite another blob."""
    if not isinstance(content, bytes):
        raise TypeError('Source content must be bytes')
    if len(content) > MAX_DECODED_BYTES:
        raise ValueError('Source exceeds decoded byte limit')
    if suffix not in {'.raw', '.html', '.json', '.xml', '.txt', '.js', '.pdf'}:
        raise ValueError('Unsupported source suffix')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    checksum = sha256(content).hexdigest()
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode='wb', filename='',
                       compresslevel=compression_level, mtime=0) as stream:
        stream.write(content)
    packed = buffer.getvalue()
    # Already compressed PDFs and tiny files can grow. Preserve the cheaper codec.
    codec = 'gzip' if len(packed) < len(content) else 'identity'
    stored = packed if codec == 'gzip' else content
    path = directory / (checksum + suffix + ('.gz' if codec == 'gzip' else ''))
    fd, temporary = tempfile.mkstemp(prefix='.source-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(stored)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            existing = path.read_bytes()
            if existing != stored:
                if read_source_bytes(path) != content:
                    raise ValueError('Immutable compressed source conflict')
                # Keep a valid first encoding across gzip level/library changes.
                stored = existing
    finally:
        Path(temporary).unlink(missing_ok=True)
    storage = {'codec': codec, 'sha256': sha256(stored).hexdigest(),
               'stored_bytes': len(stored), 'decoded_bytes': len(content)}
    if sha256(read_source_bytes(path, storage=storage)).hexdigest() != checksum:
        raise ValueError('Compressed source round-trip mismatch')
    return {'path': str(path.absolute()), 'sha256': checksum, 'storage': storage}
