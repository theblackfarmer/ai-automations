"""Bounded, decision-time ASTRA native target funnel audit (research only).

This is a passive observer called on the SAME completed return event at which
NativeSignalSession currently evaluates the frozen return-entry policy.  It
reads the in-process target catalog as of that event and never writes to it.

No independent owner/ancestor label is inferred: historical zone records do
not carry sufficient binding authority. No output authorizes a trade, makes a
fill assertion or forecasts an outcome. Safe for the archived QC free-plan
log cap: aggregate counts and a few decision-time samples, no raw bars.
"""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
import math

FRAME_SECONDS = (900, 3600, 14400)
STAGES = ('NO_OPPOSING_ZONE_KNOWN', 'ALL_KNOWN_OPPOSING_INVALID',
          'VALID_OPPOSING_NOT_AHEAD', 'TARGET_AVAILABLE')


def _int(x, label):
    if type(x) is not int or x < 0:
        raise ValueError(f'{label}: nonnegative integer clock required')
    return x


def _positive(x, label):
    if type(x) not in (int, float) or not math.isfinite(x) or x <= 0:
        raise ValueError(f'{label}: positive finite number required')
    return x


def _status(state, expected_parent, expected_contact, direction, cutoff):
    if state is None:
        return 'UNKNOWN'
    if not isinstance(state, dict):
        raise ValueError('structure state must be an object or null')
    known = _int(state['known_at'], 'structure.known_at')
    if known > cutoff:
        raise ValueError('structure was future-known')
    if state['parent_id'] != expected_parent or state['contact_id'] != expected_contact:
        return 'LINEAGE_MISMATCH'
    return 'ALIGNED' if state['direction'] == direction else 'OPPOSED'


def capture_return(*, source_namespace, event, context, packet, catalog):
    """One auditable snapshot, before any subsequent completed minute is seen.

    `event` is a CANDIDATE_RETURN emitted from pipeline.step; `context` is that
    exact event's context, `packet` is the bound candidate setup, `catalog` is
    the in-memory current pipeline.zones. All are read-only input records.
    """
    if not isinstance(source_namespace, str) or len(source_namespace) != 64 or any(
            c not in '0123456789abcdef' for c in source_namespace):
        raise ValueError('source namespace hash required')
    if event.get('kind') != 'CANDIDATE_RETURN':
        raise ValueError('only original CANDIDATE_RETURN event may be sampled')
    cutoff = _int(event['known_at'], 'event.known_at')
    if event['bar']['known_at'] != cutoff or event['bar']['time'] + 60 != cutoff:
        raise ValueError('event minute was not fully closed at cutoff')
    if context.get('asof') != cutoff:
        raise ValueError('context must be frozen at return event')
    if not isinstance(catalog, list):
        raise ValueError('zone catalog must be list')
    candidate = event['candidate_id']
    parent = packet['parent']
    contact = packet['contact']
    direction = parent['direction']
    if direction not in ('long','short') or not candidate or not parent['parent_id']:
        raise ValueError('invalid original candidate and parent identity')
    g = packet['formation_result']['candidate']
    proximal = _positive(g['proximal'], 'entry proximal')
    mark = _positive(event['bar']['close'], 'return close')
    # MarketContextHooks._target uses last completed 1M close and proximal.
    entry = max(proximal, mark) if direction == 'long' else min(proximal, mark)
    expected_opposite = 'short' if direction == 'long' else 'long'
    opposites = []
    catalog_ids = set()
    future_known = 0
    for z in catalog:
        if not isinstance(z, dict) or type(z.get('known_at')) is not int:
            raise ValueError('catalog missing known time')
        zid = z['zone_id']
        if not isinstance(zid, str) or not zid or zid in catalog_ids:
            raise ValueError('duplicate or empty zone identity')
        catalog_ids.add(zid)
        at = _int(z['known_at'], 'zone known_at')
        if at > cutoff:
            future_known += 1
            continue
        if z['direction'] != expected_opposite:
            continue
        if type(z.get('target_valid', z.get('valid'))) is not bool:
            raise ValueError('status must be a recorded boolean')
        if z['timeframe_seconds'] not in FRAME_SECONDS:
            raise ValueError('unexpected catalog timeframe')
        low, high = _positive(z['low'],'zone low'), _positive(z['high'],'zone high')
        if low >= high:
            raise ValueError('degenerate catalog zone')
        opposites.append(z)
    valid = [z for z in opposites if z.get('target_valid',z['valid'])]
    ahead = [z for z in valid if (z['low'] > entry if direction=='long'
                                   else z['high'] < entry)]
    if ahead:
        # Exact ranking of the saved native MarketContextHooks._target.
        chosen = min(ahead, key=lambda z:(
            abs((z['low'] if direction=='long' else z['high']) - entry),
            -z['timeframe_seconds'], -z['known_at'], z['zone_id']))
        stage='TARGET_AVAILABLE'
    elif not opposites:
        chosen=None;stage='NO_OPPOSING_ZONE_KNOWN'
    elif not valid:
        chosen=None;stage='ALL_KNOWN_OPPOSING_INVALID'
    else:
        chosen=None;stage='VALID_OPPOSING_NOT_AHEAD'
    actual = context.get('target')
    actual_id = actual.get('zone_id') if isinstance(actual, dict) else None
    if actual_id != (chosen['zone_id'] if chosen else None):
        raise ValueError('selector parity violation; do not relabel actual trade')
    states=context['support_states']
    m1=_status(states.get(60),parent['parent_id'],contact['contact_id'],direction,cutoff)
    m5=_status(states.get(300),parent['parent_id'],contact['contact_id'],direction,cutoff)
    joint=sorted(set(context.get('reasons',[])))
    # Different physical candidates can return on every minute. Count only
    # the first return for a distinct original candidate once per source namespace.
    return {
        'namespace':source_namespace, 'candidate_id':candidate,
        'parent_id':parent['parent_id'], 'return_source_id':event['bar']['source_id'],
        'decision_at':cutoff,'stage':stage,'selected_zone_id':actual_id,
        'm1':m1,'m5':m5,'joint_reasons':joint,
        'opposing_known':len(opposites),'opposing_valid':len(valid),'valid_ahead':len(ahead),
        'invalid_opposing':len(opposites)-len(valid),'future_known_skipped':future_known,
        'parent_relevance':'UNVERIFIED_FROM_CATALOG',
        'candidate_entry_hypothesis_not_execution':True
    }


class NativeFunnelCensus:
    """Accumulate all closed decisions without retaining raw price/history."""
    def __init__(self, samples_per_stage=2):
        if type(samples_per_stage) is not int or not 0<=samples_per_stage<=10:
            raise ValueError('invalid per-stage sample budget')
        self.samples_per_stage=samples_per_stage
        self.observations=set()
        self.first={}
        self.first_missing_then_target=set()
        self.stages=Counter();self.state_pairs=Counter();self.reasons=Counter()
        self.distinct_by_stage={x:set() for x in STAGES}
        self.samples={x:[] for x in STAGES}
        self._fingerprint=sha256()

    def observe(self, row):
        if row['stage'] not in STAGES:
            raise ValueError('unknown classification')
        key=(row['namespace'],row['candidate_id'],row['decision_at'],row['return_source_id'])
        if key in self.observations:
            raise ValueError('duplicate source observation')
        self.observations.add(key)
        candidate=(row['namespace'],row['candidate_id'])
        first=self.first.setdefault(candidate,row['stage'])
        if first != 'TARGET_AVAILABLE' and row['stage']=='TARGET_AVAILABLE':
            self.first_missing_then_target.add(candidate)
        self.stages[row['stage']]+=1
        self.distinct_by_stage[row['stage']].add(candidate)
        self.state_pairs[row['m1']+'|'+row['m5']]+=1
        for reason in row['joint_reasons']:self.reasons[reason]+=1
        if len(self.samples[row['stage']])<self.samples_per_stage:
            self.samples[row['stage']].append({k:row[k] for k in
                ('candidate_id','return_source_id','decision_at','stage','m1','m5',
                 'opposing_known','invalid_opposing','valid_ahead','selected_zone_id')})
        self._fingerprint.update((json.dumps(row,sort_keys=True,separators=(',',':'))+'\n').encode())

    def report(self, expected_total=None, expected_missing=None, expected_targets=None):
        total=len(self.observations)
        missing=sum(self.stages[s] for s in STAGES if s!='TARGET_AVAILABLE')
        with_target=self.stages['TARGET_AVAILABLE']
        if expected_total is not None and total!=expected_total:
            raise ValueError(f'INCOMPLETE_NATIVE_POPULATION expected {expected_total}, got {total}')
        if expected_missing is not None and missing!=expected_missing:
            raise ValueError(f'NATIVE_MISSING_COUNT_MISMATCH expected {expected_missing}, got {missing}')
        if expected_targets is not None and with_target!=expected_targets:
            raise ValueError(f'NATIVE_TARGET_COUNT_MISMATCH expected {expected_targets}, got {with_target}')
        return {
            'scope':'READ_ONLY_NATIVE_TARGET_FUNNEL_CENSUS',
            'return_observations':total,'missing_target':missing,'with_target':with_target,
            'distinct_candidates':len(self.first),
            'distinct_candidates_by_stage':{k:len(v) for k,v in self.distinct_by_stage.items()},
            'first_return_no_target_candidates':sum(s!='TARGET_AVAILABLE' for s in self.first.values()),
            'first_missing_later_target_candidates':len(self.first_missing_then_target),
            'stage_counts':{k:self.stages[k] for k in STAGES},
            'm1_m5_joint_counts':dict(sorted(self.state_pairs.items())),
            'joint_issue_counts':dict(sorted(self.reasons.items())),
            'per_stage_samples':deepcopy(self.samples),
            'source_decision_sha256':self._fingerprint.hexdigest(),
            'source_catalog_parent_relevance_validated':False,
            'trade_admission':False,'performance_test':False,'orders_changed':False
        }
