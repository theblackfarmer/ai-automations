"""Optional historical software parity check on separate archived LOCAL 4,153 cohort.

The archive is user-owned and supplied; this does NOT assert native results.
Requires the exact October 28 local archive; only reads it and generates a
separate bounded report. Zero price or trading-rule changes.
"""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import zipfile

from native_funnel_capture_v1 import NativeFunnelCensus, capture_return

MEMBER='source/astra_integrated_work/full_history_calendar_verified/transitions.jsonl.gz'
SUMMARY='source/astra_integrated_work/full_history_calendar_verified/summary.json'


def run(path):
    audit=NativeFunnelCensus(2)
    counts={}
    zones={};candidates={}
    rows=0
    stream_hash=hashlib.sha256()
    previous_known=None
    with zipfile.ZipFile(path) as z:
        summary=json.loads(z.read(SUMMARY))
        namespace=summary['source_namespace_sha256']
        with gzip.GzipFile(fileobj=z.open(MEMBER)) as stream:
            for line in stream:
                rows+=1
                stream_hash.update(line)
                item=json.loads(line)
                event_known=item.get("event",{}).get("known_at")
                if event_known is not None and previous_known is not None and event_known < previous_known:
                    raise AssertionError("future event appeared before earlier-known event")
                if event_known is not None: previous_known=event_known
                e=item.get('event') or {}
                kind=e.get('kind')
                counts[kind]=counts.get(kind,0)+1
                if kind=='COVERAGE_GAP' and e.get('discovery_reset') is True:
                    zones.clear()
                elif kind=='TARGET_ZONE_CREATED':
                    q=copy.deepcopy(e['zone']);q.setdefault('target_valid',q['valid'])
                    zones[q['zone_id']]=q
                elif kind=='TARGET_ZONE_INVALIDATED':
                    zid=e['zone_id']
                    if zid not in zones: raise AssertionError('unknown zone invalidated')
                    zones[zid]['valid']=False
                    # Old local far-wick target policy exactly shared source geometry.
                    if 'target_valid' in zones[zid]: zones[zid]['target_valid']=False
                elif kind=='TARGET_CATALOG_INVALIDATED':
                    zid=e['zone_id']
                    if zid not in zones:raise AssertionError('unknown target catalog ID')
                    zones[zid]['target_valid']=False
                elif kind=='CANDIDATE_READY':
                    candidates[e['candidate_id']]=e['packet']
                elif kind=='CANDIDATE_RETURN':
                    cid=e['candidate_id']
                    if cid not in candidates:raise AssertionError('return has no registered candidate')
                    context=dict(e['context'])
                    # JSON object keys are strings, while native in-process state keys are ints.
                    context['support_states']={int(k):v for k,v in context['support_states'].items()}
                    row=capture_return(source_namespace=namespace,event=e,context=context,
                         packet=candidates[cid],catalog=list(zones.values()))
                    audit.observe(row)
    result=audit.report(expected_total=4153, expected_missing=3298, expected_targets=855)
    if stream_hash.hexdigest()!=summary['transition_content_sha256']:
        raise AssertionError("archived transition content does not match source receipt")
    result.update(archived_local_stream_rows=rows,
                  archived_local_transition_sha256=summary['transition_content_sha256'],
                  local_source_hash=namespace,
                  was_quantconnect_native_3069_cohort=False,
                  cloud_execution=False,
                  separate_archived_local_replay_only=True)
    return result

if __name__=='__main__':
    import argparse
    a=argparse.ArgumentParser()
    a.add_argument('--archive',required=True,type=Path)
    a.add_argument('--output',required=True,type=Path)
    args=a.parse_args()
    result=run(args.archive)
    args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps({k:result[k] for k in ('return_observations','missing_target',
          'with_target','distinct_candidates','stage_counts','m1_m5_joint_counts','source_decision_sha256')},indent=2))
