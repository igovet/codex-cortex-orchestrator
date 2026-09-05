#!/usr/bin/env python3
"""Explicit installation of packaged Cortex profiles into Codex's native registry."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import tomllib


def sha(body):
    return hashlib.sha256(body).hexdigest()


def safe_path(path):
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('symlink in native profile path')
    if path.exists():
        info=path.stat()
        if info.st_uid!=os.getuid() or (not stat.S_ISDIR(info.st_mode) and
                                       (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1)):
            raise ValueError('unsafe native profile ownership or file type')


def atomic_write(path,body):
    fd,temporary=tempfile.mkstemp(prefix='.cortex-setup-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(body);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def register(plugin,home,install=False):
    home=Path(home).expanduser().absolute()
    if '..' in home.parts:raise ValueError('noncanonical Codex home')
    directory=home/'agents';receipt=home/'cortex-native-profiles.json'
    safe_path(directory);safe_path(receipt)
    previous=json.loads(receipt.read_text()) if receipt.exists() else {}
    expected={}
    profiles=json.loads((plugin/'profiles.json').read_text())['profiles']
    if len(profiles)!=22:raise ValueError('incomplete profile catalogue')
    for profile in profiles:
        name=profile['filename']
        if Path(name).name!=name or not name.endswith('.toml'):raise ValueError('invalid profile filename')
        body=(plugin/'agents'/name).read_bytes()
        parsed=tomllib.loads(body.decode())
        if parsed['name']!=profile['name'] or not parsed.get('developer_instructions'):
            raise ValueError('invalid native profile')
        expected[name]=body
    if len(expected)!=22:raise ValueError('duplicate profile filenames')
    missing=[];stale=[];conflicts=[]
    for name,body in expected.items():
        target=directory/name;safe_path(target)
        if not target.exists():missing.append(name)
        elif target.read_bytes()!=body:
            if previous.get(name)==sha(target.read_bytes()):stale.append(name)
            else:conflicts.append(name)
    result=dict(profiles=22,missing=missing,stale=stale,conflicts=conflicts,installed=False)
    if install and not conflicts:
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        for name in missing+stale:atomic_write(directory/name,expected[name])
        atomic_write(receipt,(json.dumps({name:sha(body) for name,body in expected.items()},sort_keys=True)+'\n').encode())
        for name,body in expected.items():
            if (directory/name).read_bytes()!=body:raise ValueError('profile verification failed')
        result.update(missing=[],stale=[],installed=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install',action='store_true',help='Explicitly register profiles; default only checks.')
    args=parser.parse_args()
    plugin=Path(__file__).resolve().parents[1]
    home=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
    try:result=register(plugin,home,args.install)
    except (OSError,ValueError,KeyError):
        raise SystemExit('Cortex profile setup failed; no private contents displayed.') from None
    print(json.dumps(result))
    if result['conflicts'] or result['missing'] or result['stale']:raise SystemExit(1)


if __name__=='__main__':main()
