"""ASTRA 6: read-only causal screen of saved local liquidity-map return snapshots.

Not the QuantConnect native 3069 return cohort. This script does NOT authorize
entries, targets, fills, risk, or PnL. Its output is only an upper bound of
possible *observed-since-enrollment* pivot destinations. Historical pre-enrollment
freshness, owner binding, complete source minutes and resting liquidity are
NOT authenticated by this archive.

Usage: python astra_archived_liquidity_target_screen.py --archive PATH --output PATH
"""
from __future__ import annotations
from argparse import ArgumentParser
from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile
import gzip
import json

try:
    import orjson
    decode = orjson.loads
except ImportError:
    decode = json.loads

MEMBER = 'source/astra_refinement_work/full_history_target_close/transitions.jsonl.gz'
TIMEFRAMES = (1,5,15,60,240)
EXPECTED = {'return_observations':4111,'with_original_zone_target':828,
            'missing_original_zone_target':3283}


def support(context):
    states=context.get('support_states') or {}
    parent=context.get('parent') or {}; contact=context.get('contact') or {}
    direction=parent.get('direction'); pid=parent.get('parent_id');cid=contact.get('contact_id')
    def cls(tf):
        state=states.get(str(tf)) or states.get(tf)
        if not state:return 'UNKNOWN'
        if state.get('parent_id')!=pid or state.get('contact_id')!=cid:return 'LINEAGE_MISMATCH'
        return 'ALIGNED' if state.get('direction')==direction else 'OPPOSED'
    return cls(60),cls(300)


def eligible_observer_pools(event):
    """As-of frozen *observer* pools; not verified usable liquidity targets."""
    context=event.get('context') or {}
    m=context.get('liquidity_map') or {}; cutoff=event['known_at']
    direction=(context.get('parent') or {}).get('direction')
    entry=m.get('decision_entry_reference')
    if direction not in ('short','long') or type(entry) not in (int,float):
        return [],'UNREADABLE_SIDE_OR_ENTRY'
    if (m.get('known_at')!=cutoff or m.get('asof')!=cutoff or
        m.get('parent_id')!=(context.get('parent') or {}).get('parent_id') or
        m.get('candidate_id')!=event.get('candidate_id')):
        return [],'MAP_CUTOFF_OR_ID_MISMATCH'
    pools=m.get('buy_side' if direction=='long' else 'sell_side') or []
    valid=[]
    for p in pools:
        price=p.get('price')
        if type(price) not in (int,float):continue
        if not (price>entry if direction=='long' else price<entry):continue
        if (p.get('not_crossed_since_enrollment') is not True or
            p.get('crossing_status')!='NOT_CROSSED_SINCE_ENROLLMENT'):
            continue
        if p.get('known_at') is None or p['known_at']>cutoff or p.get('latest_enrollment_at',float('inf'))>cutoff:
            continue
        refs=p.get('references') or []
        if not refs:continue
        tfs=[]
        for ref in refs:
            tf=ref.get('timeframe_seconds')
            if tf not in {x*60 for x in TIMEFRAMES}:continue
            if (ref.get('market_known_at',float('inf'))>cutoff or
                ref.get('registered_at',float('inf'))>cutoff or
                ref.get('observed_through')!=cutoff or
                ref.get('history_complete') is not True or
                ref.get('first_strict_breach') is not None or
                not ref.get('source_id')):continue
            tfs.append(tf//60)
        if not tfs:continue
        if sorted(set(tfs))!=sorted(set(t//60 for t in p.get('timeframes_seconds',[]) if t//60 in TIMEFRAMES)):
            # Do not silently accept unsupported or unproven group memberships.
            continue
        valid.append({'pool_id':p['pool_id'],'price':price,'distance_points':abs(price-entry),
                      'timeframes_minutes':sorted(set(tfs)),'known_at':p['known_at'],
                      'pre_enrollment_freshness':p.get('pre_enrollment_freshness','UNKNOWN'),
                      'parent_ownership_authenticated':False,
                      'history_scope':'FROM_OBSERVER_ENROLLMENT_ONLY'})
    valid.sort(key=lambda p:(p['distance_points'],p['pool_id']))
    return valid,'SINCE_ENROLLMENT_ONLY'


def analyze_archive(archive:Path,member:str=MEMBER):
    """Streaming single-pass source analyzer. No future decision or PnL inspected."""
    counts=Counter(); pair=Counter(); families=Counter();aligned_families=Counter()
    first_return={};all_candidates=set();missing_with_some_proxy=set()
    aligned_missing_ids=set();m15_aligned_ids=set();m15_without_child_ids=set()
    samples=[];special_samples=[];seen_sample=set();seen_special=set()
    dedupe=set();source_hash=sha256();start=__import__('time').monotonic()
    with ZipFile(archive) as z, gzip.GzipFile(fileobj=z.open(member)) as stream:
        for raw in stream:
            if b'"kind":"CANDIDATE_RETURN"' not in raw:continue
            outer=decode(raw);ev=outer.get('event') or {}
            if ev.get('kind')!='CANDIDATE_RETURN':continue
            c=ev.get('context') or {}; b=ev.get('bar') or {}
            cutoff=ev['known_at'];cid=ev['candidate_id'];key=(cid,cutoff,b.get('source_id'))
            if key in dedupe:raise ValueError('Duplicate complete source return identity')
            dedupe.add(key);source_hash.update(raw)
            if c.get('asof')!=cutoff or b.get('known_at')!=cutoff:
                raise ValueError('Return asof/bar cutoff mismatch')
            counts['return_observations']+=1
            all_candidates.add(cid)
            pair[support(c)]+=1
            with_zone=c.get('target') is not None
            first_return.setdefault(cid,with_zone)
            if with_zone:
                counts['with_original_zone_target']+=1;continue
            counts['missing_original_zone_target']+=1
            aligned=support(c)==('ALIGNED','ALIGNED')
            if aligned:counts['missing_aligned_both_m1_m5']+=1;aligned_missing_ids.add(cid)
            levels,status=eligible_observer_pools(ev)
            if status!='SINCE_ENROLLMENT_ONLY':
                counts['map_unreadable_or_mismatched']+=1
                continue
            if not levels:
                counts['missing_no_observed_ahead_proxy']+=1
                continue
            counts['missing_has_observed_ahead_proxy']+=1
            missing_with_some_proxy.add(cid)
            seen={tf for l in levels for tf in l['timeframes_minutes']}
            for tf in seen:
                families[tf]+=1
                if aligned:aligned_families[tf]+=1
            if 15 in seen:
                counts['missing_has_observed_m15_proxy']+=1
                if aligned:
                    counts['missing_aligned_has_observed_m15_proxy']+=1
                    m15_aligned_ids.add(cid)
            no_children=not any(tf in seen for tf in (1,5))
            if 15 in seen and no_children:
                counts['missing_has_m15_no_m1_m5_observed_proxy']+=1
                if aligned:
                    counts['missing_aligned_has_m15_no_m1_m5_observed_proxy']+=1
                    m15_without_child_ids.add(cid)
            if 15 in levels[0]['timeframes_minutes']:
                counts['missing_nearest_pool_contains_m15']+=1
                if aligned:counts['missing_aligned_nearest_pool_contains_m15']+=1
            for l in levels:
                if l['pre_enrollment_freshness']!='PROVEN_UNTOUCHED':
                    counts['proxy_repeated_memberships_pre_enrollment_not_proven']+=1
            if aligned and len(samples)<12 and cid not in seen_sample:
                seen_sample.add(cid)
                samples.append({'candidate_id':cid,'decision_utc':datetime.fromtimestamp(cutoff,timezone.utc).isoformat(),
                    'return_source_id':key[2],'selected_parent_id':(c.get('parent') or {}).get('parent_id'),
                    'direction':(c.get('parent') or {}).get('direction'),
                    'entry_reference':(c.get('liquidity_map') or {}).get('decision_entry_reference'),
                    'nearest_observer_proxy':levels[0],
                    'm15_observer_ahead':15 in seen,
                    'status':'UNVERIFIED_PROXY_NOT_EXECUTABLE'})
            if aligned and 15 in seen and no_children and len(special_samples)<10 and cid not in seen_special:
                seen_special.add(cid)
                lp=next(l for l in levels if 15 in l['timeframes_minutes'])
                special_samples.append({'candidate_id':cid,
                    'decision_utc':datetime.fromtimestamp(cutoff,timezone.utc).isoformat(),
                    'return_source_id':key[2],
                    'selected_parent_id':(c.get('parent') or {}).get('parent_id'),
                    'side':(c.get('parent') or {}).get('direction'),
                    'entry_reference':(c.get('liquidity_map') or {}).get('decision_entry_reference'),
                    'm15_pool':lp,
                    'parent_ownership_authenticated':False})
    for key,expected in EXPECTED.items():
        if counts[key]!=expected:raise ValueError(f'Historical source mismatch {key}: {counts[key]} != {expected}')
    report={
      'research_scope':'ARCHIVED_LOCAL_TARGET_CLOSE_DECISION_SNAPSHOT_SCREEN_NOT_QUANTCONNECT_NATIVE',
      'source_archive':archive.name,'source_archive_member':member,
      'exact_original_return_line_sha256':source_hash.hexdigest(),
      'frozen_original_cohort_counts':{x:counts[x] for x in EXPECTED},
      'distinct_candidates':len(all_candidates),
      'first_return_missing_original_zone_candidates':sum(not x for x in first_return.values()),
      'missing_zone_return_observations_both_structures_aligned':counts['missing_aligned_both_m1_m5'],
      'possible_pivot_fallback_screen':{
        'missing_with_any_observed_not_crossed_ahead':counts['missing_has_observed_ahead_proxy'],
        'missing_without_any_observed_not_crossed_ahead':counts['missing_no_observed_ahead_proxy'],
        'missing_with_m15_observed_not_crossed_ahead':counts['missing_has_observed_m15_proxy'],
        'missing_with_m15_and_no_m1_m5_observed_proxy':counts['missing_has_m15_no_m1_m5_observed_proxy'],
        'missing_nearest_pool_contains_m15':counts['missing_nearest_pool_contains_m15'],
        'm1_m5_aligned_missing_with_observed_m15_ahead':counts['missing_aligned_has_observed_m15_proxy'],
        'm1_m5_aligned_missing_m15_but_no_child_proxy':counts['missing_aligned_has_m15_no_m1_m5_observed_proxy'],
        'm1_m5_aligned_missing_nearest_pool_contains_m15':counts['missing_aligned_nearest_pool_contains_m15'],
        'distinct_missing_candidates_with_any_observer_proxy':len(missing_with_some_proxy),
        'distinct_aligned_missing_candidates':len(aligned_missing_ids),
        'distinct_aligned_missing_candidates_with_m15_observer_proxy':len(m15_aligned_ids),
        'distinct_aligned_missing_candidates_with_m15_but_no_m1_m5_proxy':len(m15_without_child_ids),
        'nonexclusive_any_proxy_by_timeframe_minutes':{str(k):v for k,v in sorted(families.items())},
        'nonexclusive_aligned_proxy_by_timeframe_minutes':{str(k):v for k,v in sorted(aligned_families.items())},
        'repeated_proxy_memberships_without_proven_pre_enrollment_freshness':counts['proxy_repeated_memberships_pre_enrollment_not_proven'],
        'unreadable_or_mismatched_map_count':counts['map_unreadable_or_mismatched'],
      },
      'structure_state_pairs_all_returns':{'|'.join(k):v for k,v in sorted(pair.items())},
      'examples_of_aligned_missing_original_target':samples,
      'examples_with_m15_proxy_but_no_m1_m5_proxy':special_samples,
      'provider_session_liquidity_validation':{
          'pdh_pdl':'NOT_IN_ARCHIVED_DECISION_MAP',
          'pwh_pwl':'NOT_IN_ARCHIVED_DECISION_MAP',
          'previous_month_high_low':'NOT_IN_ARCHIVED_DECISION_MAP',
          'premarket_0300_0800':'NO_COMPLETE_SOURCE_BARS_IN_ARCHIVED_EVENT_STREAM',
      },
      'hard_limits':[
        'This is the local 4,111-return archived research cohort, not the 3,069 native QuantConnect cohort with 2,054 missing targets.',
        'Only after-enrollment non-strict-crossing evidence is verified by these snapshots. Exact touches may be unlogged; no proof of untouched history before enrollment.',
        'These reference records do not authenticate selected-parent ownership, even when entry structure aligns.',
        'No full source-minute coverage or source/dated-contract continuity proof in the preserved event archive; session and premarket highs/lows cannot be safely calculated here.',
        'No native fill, stop/target executable order, PnL or additional profitable trade has been established.',
        'Counts are repeated return observations; distinct candidates are reported separately.',
      ],
      'elapsed_seconds':round(__import__('time').monotonic()-start,2)
    }
    return report


def main():
    arg=ArgumentParser(description=__doc__)
    arg.add_argument('--archive',required=True,type=Path)
    arg.add_argument('--output',required=True,type=Path)
    arg.add_argument('--member',default=MEMBER)
    a=arg.parse_args()
    result=analyze_archive(a.archive,a.member)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'totals':result['frozen_original_cohort_counts'],
      'aligned_missing':result['missing_zone_return_observations_both_structures_aligned'],
      'fallback':result['possible_pivot_fallback_screen'],'output':str(a.output)},indent=2))

if __name__=='__main__':main()
