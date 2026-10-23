"""Bounded resumable wrapper around unchanged hash-frozen V4 acquisition.

Deadline stops new requests; an in-flight request finishes and persists before
shutdown. V4 retains its lock, identity, spacing, retries and shared cooldown.
An optional --request-timeout changes only each transport wait (default 20,
maximum 120 seconds); it does not change validation or retry budgets.
Network requires --execute. This wrapper never resets external retry budgets.
"""
import argparse
import json
import math
from pathlib import Path
import signal
import time
import acquire_month_v4 as acquisition


def validate_budgets(max_requests, wall_seconds):
    if type(max_requests) is not int or max_requests < 0:
        raise ValueError('max_requests must be a nonnegative integer')
    if type(wall_seconds) not in (int,float) or not math.isfinite(wall_seconds) or wall_seconds < 0:
        raise ValueError('wall_seconds must be finite and nonnegative')


def validate_timeout(value):
    if type(value) not in (int,float) or not math.isfinite(value) or not 0 < value <= 120:
        raise ValueError('request_timeout must be finite, positive and at most 120 seconds')


def run_bounded(target,p,raw,psha,start,end,max_requests,wall_seconds,route='canonical',
                *,request_timeout=20,monotonic=time.monotonic,**run_options):
    validate_budgets(max_requests,wall_seconds)
    validate_timeout(request_timeout)
    if 'fetcher' not in run_options:
        run_options['fetcher']=lambda epoch:acquisition.fetch(epoch,timeout=request_timeout,route=route)
    if 'stop_requested' in run_options:
        raise ValueError('stop_requested belongs to wrapper')
    deadline=monotonic()+wall_seconds
    requested=[False];previous={}
    def request_stop(signum,frame):requested[0]=True
    try:
        for signum in (signal.SIGINT,signal.SIGTERM):
            previous[signum]=signal.signal(signum,request_stop)
        return acquisition.run(target,p,raw,psha,start,end,[],max_requests,route=route,
            stop_requested=lambda:requested[0] or monotonic()>=deadline,**run_options)
    finally:
        for signum,handler in previous.items():signal.signal(signum,handler)


class ErrorParser(argparse.ArgumentParser):
    def error(self,message):
        # Exit 2 is reserved for a successfully published incomplete manifest.
        self.print_usage()
        self.exit(1,'argument error: '+message+'\n')


def main(argv=None):
    parser=ErrorParser(description=__doc__)
    parser.add_argument('--protocol',required=True,type=Path)
    parser.add_argument('--target',required=True,type=Path)
    parser.add_argument('--route',choices=sorted(acquisition.ROUTES),default='canonical')
    parser.add_argument('--max-requests',type=int,default=24)
    parser.add_argument('--max-seconds','--wall-seconds',dest='wall_seconds',type=float,default=900)
    parser.add_argument('--request-timeout',type=float,default=20,help='Transport wait seconds: >0 and <=120; default 20')
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args(argv)
    validate_budgets(args.max_requests,args.wall_seconds)
    validate_timeout(args.request_timeout)
    acquisition.verify_codec()
    p,raw,psha,start,end=acquisition.load_protocol(args.protocol)
    if not args.execute:
        print(json.dumps(dict(dry_run=True,network_requests=0,protocol_sha256=psha,
            max_requests=args.max_requests,wall_seconds=args.wall_seconds,request_timeout=args.request_timeout,wire_route=args.route,
            execution_allowed=False)))
        return 0
    manifest=run_bounded(args.target,p,raw,psha,start,end,args.max_requests,args.wall_seconds,args.route,
                         request_timeout=args.request_timeout)
    print(json.dumps({key:manifest[key] for key in ('network_requests_this_run','stop_reason',
        'processed_hours','expected_hours','hour_states','all_hours_validated_or_explicit_empty_200')}))
    return 0 if manifest['all_hours_validated_or_explicit_empty_200'] else 2


if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,OSError) as error:
        print(json.dumps(dict(state='STOPPED',error_type=type(error).__name__,reason=str(error))))
        raise SystemExit(1)
