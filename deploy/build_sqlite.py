"""Build the pinned SQLite runtime library with the upstream WAL-reset fix.

SQLite is public domain: https://www.sqlite.org/copyright.html
The source hash below is published in the official 3.53.1 release notes.
This builder is run inside the Docker build stage, never at application startup.
"""

import ctypes
import hashlib
import io
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

VERSION = '3.53.1'
ARCHIVE_URL = 'https://www.sqlite.org/2026/sqlite-amalgamation-3530100.zip'
ARCHIVE_SHA256 = '36ad6e7f38540a3b21a2ac36340833f0a9e426bc1c752751c3ba669466827eae'
SOURCE_SHA3_256 = '414432ae5719f6cdc485f3927e12c7ad107e2b8c6b434e5df2eadb5312bfabb5'
RELEASE_URL = 'https://www.sqlite.org/releaselog/3_53_1.html'


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: build_sqlite.py OUTPUT_DIRECTORY')
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(ARCHIVE_URL, timeout=60) as response:
        payload = response.read()
    if hashlib.sha256(payload).hexdigest() != ARCHIVE_SHA256:
        raise SystemExit('SQLite archive checksum does not match the pinned release')
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        source = archive.read('sqlite-amalgamation-3530100/sqlite3.c')
    if hashlib.sha3_256(source).hexdigest() != SOURCE_SHA3_256:
        raise SystemExit('SQLite C source checksum does not match the official release')

    library = output / 'libsqlite3.so.0'
    with tempfile.TemporaryDirectory(prefix='starwave-sqlite-build-') as directory:
        source_path = Path(directory) / 'sqlite3.c'
        source_path.write_bytes(source)
        subprocess.run([
            'gcc', '-O2', '-fPIC', '-shared', '-DSQLITE_THREADSAFE=1',
            '-DSQLITE_ENABLE_COLUMN_METADATA', '-DSQLITE_ENABLE_FTS5',
            '-DSQLITE_ENABLE_RTREE', '-DSQLITE_ENABLE_MATH_FUNCTIONS',
            '-Wl,-soname,libsqlite3.so.0', str(source_path),
            '-o', str(library), '-lm', '-ldl', '-pthread',
        ], check=True)
    compiled = ctypes.CDLL(str(library))
    compiled.sqlite3_libversion.restype = ctypes.c_char_p
    if compiled.sqlite3_libversion().decode() != VERSION:
        raise SystemExit('Compiled SQLite version does not match the pinned release')
    (output / 'SQLITE_PROVENANCE.txt').write_text(
        f'SQLite {VERSION}; upstream software is in the public domain.\n'
        'https://www.sqlite.org/copyright.html\n'
        f'Release: {RELEASE_URL}\nArchive: {ARCHIVE_URL}\n'
        f'Archive SHA256: {ARCHIVE_SHA256}\nSource SHA3-256: {SOURCE_SHA3_256}\n'
        'Includes the WAL-reset fix documented at https://www.sqlite.org/wal.html#walreset\n'
    )
    print(f'Built SQLite {VERSION}; verified archive and upstream source checksums', flush=True)


if __name__ == '__main__':
    main()
