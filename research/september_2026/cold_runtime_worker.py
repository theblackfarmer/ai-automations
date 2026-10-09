"""One prefix, one fresh Python process; no market source file is available to code.

The audit hook is a deterministic dependency guard for trusted research code, not
a security sandbox for hostile Python. Captured code cannot use ordinary file,
network or subprocess IO. Standard-library module loading is the sole file-read
exception. Inputs and captured code bytes arrive through stdin before the guard.
"""
import builtins
# Preload the small, explicitly reviewed dependency set before installing import
# restrictions. Captured code cannot expand it to native storage/IO extensions.
import argparse
import collections
import copy
import csv
import datetime
import hashlib
import io
import json
import math
import os
import sys
import sysconfig
import zoneinfo
from pathlib import Path


def install_dependency_guard():
    roots = {Path(sysconfig.get_path(k)).resolve() for k in ('stdlib','platstdlib')}
    stdlib_zips = {Path(p).resolve() for p in sys.path if p.endswith('.zip') and 'python' in Path(p).name}
    allowed_imports = {'argparse','collections','copy','csv','datetime','hashlib','io',
                       'json','math','os','pathlib','zoneinfo'}
    real_import = builtins.__import__
    resolving_path=False

    def resolved(path):
        nonlocal resolving_path
        previous=resolving_path;resolving_path=True
        try: return Path(os.fsdecode(path)).resolve()
        finally: resolving_path=previous

    def standard_path(p):
        return (p in stdlib_zips or any(p.is_relative_to(r) for r in roots)) and not any(x in ('site-packages','dist-packages') for x in p.parts)

    def guarded_import(name,*args,**kwargs):
        if name.split('.')[0] not in allowed_imports:
            raise RuntimeError('COLD_RUNTIME_DEPENDENCY_DENIED: import '+name)
        return real_import(name,*args,**kwargs)

    def check_metadata(path):
        if resolving_path: return
        if not isinstance(path,(str,bytes,os.PathLike)) or not standard_path(resolved(path)):
            raise RuntimeError('COLD_RUNTIME_DEPENDENCY_DENIED: filesystem metadata')

    # Native metadata queries do not consistently emit Python audit events.
    # Restrict their ordinary os/pathlib entry points as well as file contents.
    def metadata_function(original):
        def checked(path,*args,**kwargs):
            check_metadata(path)
            return original(path,*args,**kwargs)
        return checked
    for name in ('stat','lstat','access','readlink'):
        setattr(os,name,metadata_function(getattr(os,name)))
    for name in ('stat','lstat'):
        setattr(Path,name,metadata_function(getattr(Path,name)))

    def hook(event,args):
        if event == 'import':
            if args[0].split('.')[0] not in allowed_imports:
                raise RuntimeError('COLD_RUNTIME_DEPENDENCY_DENIED: native/indirect import '+args[0])
        elif event == 'open':
            path,mode,flags = args
            if not isinstance(path,(str,bytes,os.PathLike)):
                raise RuntimeError('COLD_RUNTIME_DEPENDENCY_DENIED: file descriptor open')
            p = resolved(path)
            # Importing a standard module can read .py/.pyc/native library bytes.
            # Site packages and arbitrary data files under Python are not inputs.
            standard = standard_path(p)
            standard = standard and p.suffix in ('.py','.pyc','.so','.dylib','.zip')
            writes = (isinstance(mode,str) and any(c in mode for c in 'wax+')) or bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
            if not standard or writes:
                raise RuntimeError('COLD_RUNTIME_DEPENDENCY_DENIED: '+str(p))
        elif event in ('os.listdir','os.scandir'):
            if not args or not isinstance(args[0],(str,bytes,os.PathLike)) or not standard_path(resolved(args[0])):
                raise RuntimeError('COLD_RUNTIME_DEPENDENCY_DENIED: '+event+' '+str(args[0] if args else ''))
        elif event.startswith(('socket.','subprocess.','ctypes.','sqlite3.')) or event in (
                'os.system','os.exec','os.posix_spawn','os.fork','os.forkpty',
                'os.remove','os.rename','os.rmdir','os.mkdir','os.chmod','os.chown',
                'os.link','os.symlink','os.truncate','os.utime'):
            raise RuntimeError('COLD_RUNTIME_DEPENDENCY_DENIED: '+event)
    builtins.__import__ = guarded_import
    sys.addaudithook(hook)


def load_source(source,name,filename):
    namespace={'__name__':name,'__file__':filename}
    exec(compile(source,filename,'exec'),namespace)
    return namespace


def main():
    request=json.loads(sys.stdin.read())
    try:
        install_dependency_guard()
        engine=load_source(request['engine_source'],'cold_engine',request['engine_filename'])['Engine']
        runner=load_source(request['runner_source'],'cold_runner',request['runner_filename'])
        mode=request.get('mode','replay')
        if mode == 'replay':
            events,snapshot,head=runner['replay'](engine,request['spec'],request['plan'],out=None)
            result=dict(ok=True,events=events,snapshot=snapshot,head=head)
        elif mode == 'assemble':
            # Rows are passed as a list to retain integer minute identities after
            # JSON transport. Caller already truncated them at this decision.
            rows={b['time']:b for b in request['rows']}
            plan,coverage=runner['assemble'](rows,request['start'],request['end'],request['source_hash'])
            result=dict(ok=True,plan=plan,coverage=coverage)
        else:
            raise ValueError('unknown worker mode')
    except Exception as exc:
        result=dict(ok=False,error_type=type(exc).__name__,detail=str(exc))
    sys.stdout.write(json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False))
    return 0 if result['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
