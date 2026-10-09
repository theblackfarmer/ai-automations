"""Run only synthetic package checks; deny ordinary socket network access."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
sys.dont_write_bytecode = True


def deny_network(event, args):
    if event in {'socket.connect','socket.connect_ex','socket.getaddrinfo','socket.gethostbyname','socket.gethostbyaddr'}:
        raise RuntimeError('Offline verification forbids network: '+event)


def main():
    sys.addaudithook(deny_network)
    import acquire_month_v4 as a
    inventory = json.loads((ROOT/'SOURCE_MANIFEST.json').read_bytes())
    for name,entry in inventory['files'].items():
        actual = hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
        if actual != entry['sha256']:
            raise ValueError('Packaged source changed: '+name)
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder)/'not-created'
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            code = a.main(['--protocol',str(ROOT/'FROZEN_MONTH_PROTOCOL.json'),'--target',str(target),
                           '--route','direct-https','--max-requests','0'])
        dry = json.loads(captured.getvalue())
        assert code == 0 and dry['dry_run'] and dry['network_requests'] == 0
        assert not target.exists()
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name)
                               for name in ('test_legacy_codec','test_portable_v4','test_portable_lineage'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps(dict(tests_run=result.testsRun,failures=len(result.failures),errors=len(result.errors),
                         dry_run_no_target_created=True,network_access_denied=True,production_bytes_unchanged=True)))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
