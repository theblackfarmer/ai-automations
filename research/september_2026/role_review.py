"""Bounded frozen-spec role accounting. No strategy detector or role attestations.

Runtime reads a sealed plan/event journal; it never executes captured engine code,
parses live prices, chooses parameters, calculates PnL, or claims teacher fidelity.
"""
import argparse
import copy
import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

VERSION = 'ROLE_SEQUENCE_ACCOUNTING_V1'
BRANCHES = {'A_OPPOSITE_RUN', 'B_WHOLE_BASE'}
PRIMARY = {'MAJOR_BOS', 'HTF_LINK', 'HTF_TAP', 'M1_TRANSITION_BOS', 'ZONE_CANDIDATE',
           'CHILD_CANDIDATE', 'FORMATION_REJECTED', 'CHILD_FORMATION_REJECTED',
           'CHILD_DEPARTURE_OBSERVED', 'RETURN_OBSERVED', 'RESPECT_OBSERVED',
           'ENTRY_REVIEW', 'ENTRY_REVIEW_BLOCKED', 'AUTHORITY_REVOKED', 'LINK_REVOKED',
           'PARENT_ZONE_BREACHED', 'CHILD_ZONE_BREACHED', 'CHILD_PERMISSION_REVOKED',
           'CHILD_COVERAGE_UNKNOWN', 'UNCLASSIFIED_COVERAGE_GAP'}
OHLC = ('open', 'high', 'low', 'close')


def canon(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(x):
    return hashlib.sha256(x if isinstance(x, bytes) else canon(x).encode()).hexdigest()


def require(ok, text):
    if not ok:
        raise ValueError(text)


def clocks(t):
    return None if t is None else {label: datetime.fromtimestamp(t, ZoneInfo(zone)).isoformat()
        for label, zone in [('UTC','UTC'), ('CT','America/Chicago'), ('ET','America/New_York')]}


def epoch(s):
    return int(datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())


def jl(data):
    return [json.loads(line) for line in data.splitlines() if line.strip()]


def source_bars(plan):
    sources, tfs, bars = {}, {}, defaultdict(list)
    for known, gaps, batch in plan:
        for tf, b in batch:
            require(tf in (240,60,1) and type(tf) is int and b.get('complete') is True,
                    'Unsupported/incomplete plan candle')
            require(b['known_at'] == known == b['time']+tf*60, 'Plan source clock differs')
            require(b['source_id'] not in sources, 'Duplicate plan source identity')
            sources[b['source_id']], tfs[b['source_id']] = b, tf
            bars[tf].append(b)
    for tf in bars:
        bars[tf].sort(key=lambda b:b['known_at'])
    return sources, tfs, bars


def load_sealed(folder):
    """Independent artifact envelope only; not a substitute for causal audit."""
    root=Path(folder).resolve()
    def read(name):
        p=(root/name).resolve()
        require(p.is_relative_to(root), 'Sealed path escapes run')
        return p.read_bytes()
    raw_completion=read('COMPLETION.json');completion=json.loads(raw_completion)
    require(completion.get('status')=='SAVED_OUTPUT_RECONCILED'
            and completion.get('execution_allowed') is False, 'Unreconciled/nonresearch run')
    held={}
    for name in ('MANIFEST.json','sealed-plan.jsonl','events.jsonl','input-audit.jsonl',
                 'SNAPSHOT.json','RESULT.json','coverage.jsonl'):
        held[name]=read(name)
        require(digest(held[name])==completion['output_hashes'].get(name), 'Changed sealed '+name)
    manifest=json.loads(held['MANIFEST.json']);captures={}
    for key,meta in manifest['captures'].items():
        raw=read(meta['path'])
        require(digest(raw)==meta['sha256']==manifest[key+'_sha256']
                ==completion['captured_input_hashes'][key], 'Changed captured '+key)
        if key in ('spec','clarifications','acquisition'):captures[key]=json.loads(raw)
    events=jl(held['events.jsonl']);plan=jl(held['sealed-plan.jsonl'])
    snapshot=json.loads(held['SNAPSHOT.json']);result=json.loads(held['RESULT.json'])
    require(snapshot['event_count']==len(events) and snapshot['event_head']==digest(events), 'Event journal head differs')
    require(snapshot['spec_hash']==digest(captures['spec']), 'Event/spec binding differs')
    require(captures['clarifications']['spec_sha256']==manifest['spec_sha256'], 'Clarification/spec binding differs')
    require(captures['acquisition']['csv']['sha256']==manifest['source_sha256'], 'Acquisition/source binding differs')
    inputs=jl(held['input-audit.jsonl']);head='0'*64;ids=[]
    require(len(inputs)==len(plan), 'Input plan count differs')
    for item,action in zip(inputs,plan):
        item=dict(item);actual=item.pop('record_hash');known,gaps,batch=action
        require(item['previous_hash']==head and item['known_at']==known
                and item['input_sha256']==digest([gaps,batch]) and actual==digest(item), 'Input chain differs')
        head=actual;ids.extend(item['event_ids'])
    require(head==result['last_input_hash'] and ids==[e['id'] for e in events], 'Input/event lineage differs')
    return events,plan,captures['spec'],dict(run=str(root),completion_sha256=digest(raw_completion),
        captures=completion['captured_input_hashes'],source_status=result.get('status'),
        complete_month_claim=result.get('complete_month_claim',False),
        event_journal_sha256=digest(held['events.jsonl']),plan_sha256=digest(held['sealed-plan.jsonl']))


def role_sources(path):
    path=Path(path).resolve();raw=path.read_bytes();catalog=json.loads(raw)
    for source in catalog.get('sources', []):
        held=(path.parent/source['capture_path']).resolve()
        require(held.is_relative_to(path.parent), 'Role snapshot path escapes bundle')
        require(digest(held.read_bytes())==source['sha256'], 'Changed role/spec snapshot '+source['path'])
    return catalog,digest(raw)


def review(events, plan, spec, rules):
    """Check recorded handoffs against fixed rules, returning violations as data."""
    require(digest(spec.get('mechanics'))==rules['mechanics_sha256'], 'Unfrozen mechanics; review refused')
    require(spec.get('execution_allowed') is False, 'Execution authority unsupported')
    require(all(spec['scope'].get(k)==v for k,v in rules['instrument'].items())
            and spec.get('sequence')==rules['sequence'], 'Frozen instrument/timeframe sequence differs')
    events=copy.deepcopy(list(events));plan=copy.deepcopy(list(plan))
    sources,tfs,bars=source_bars(plan)
    previous={}
    for tf,seq in bars.items():
        for a,b in zip(seq,seq[1:]):previous[b['source_id']]=a
    authorities={};active={240:None,60:None};bosses={};links={};zones={};taps={};children={}
    pivots={};chains=defaultdict(list);seen_ids=set();members_seen={};physical=defaultdict(list)
    rows=[];all_rejections=[];prior_known=-1;run_violations=[];invalid_events=set();invalid_pivots=set()
    start,end=epoch(spec['scope']['study_start']),epoch(spec['scope']['end_exclusive'])
    def known_pivot(p,tf,available,strict=False):
        actual=pivots.get(p.get('id')) if isinstance(p,dict) else None
        if actual is None or p.get('id') in invalid_pivots:return False
        fields=('id','kind','time','known_at','price','label','source_id')
        return (all(p.get(k)==actual.get(k) for k in fields) and tfs.get(actual['source_id'])==tf
                and (actual['known_at']<available if strict else actual['known_at']<=available))
    def valid_owner(aid,tf,at):
        a=authorities.get(aid)
        return bool(a and a['initial_event_id'] not in invalid_events and a['tf']==tf and a['known_at']<=at
                    and (a['revoked_at'] is None or (a.get('revocation_reason')=='OWNER_CLOSE_THROUGH_PROTECTED_PIVOT' and a['revoked_at']>at))
                    and bosses.get(a['latest_bos_id'],{}).get('id') not in invalid_events)
    def linked_context(link_id,at,direction):
        link=links.get(link_id)
        if not link or link['id'] in invalid_events:return False
        return (link['known_at']<=at and link['direction']==direction
                and (link['revoked_at'] is None or link['revoked_at']>at)
                and valid_owner(link['h4_authority_id'],240,at)
                and valid_owner(link['h1_authority_id'],60,at)
                and authorities[link['h4_authority_id']]['direction']==direction
                and authorities[link['h1_authority_id']]['direction']==direction)
    def cross_ok(event,bar):
        ref=event.get('reference',{});before=previous.get(event.get('source_id'))
        if not bar or not before or 'price' not in ref:return False
        return (before['close']<=ref['price']<bar['close'] if event['direction']=='long'
                else before['close']>=ref['price']>bar['close'])
    def overlap(b,z):return b['low']<=z['zone_high'] and b['high']>=z['zone_low']
    def breach(b,z):return b['low']<z['zone_low'] if z['direction']=='long' else b['high']>z['zone_high']
    def departure(b,z):return (b['close']>z['zone_high'] if z['direction']=='long' else b['close']<z['zone_low']) and not breach(b,z)
    def comparison(p,direction):
        c=p.get('comparator') or {}
        return c and (p['price']>c['price'] if direction=='long' else p['price']<c['price'])
    for event in events:
        kind,tf,known=event['kind'],event.get('timeframe'),event['known_at']
        reasons=[];checks=[];evidence={};unknowns=[]
        def check(name,ok):
            checks.append(dict(rule=name,pass_check=bool(ok)))
            if not ok:reasons.append(name)
        check('EVENT_ID_AND_CLOCK',event['id'] not in seen_ids and type(known) is int and known>=prior_known)
        check('FROZEN_SPEC_AND_RESEARCH_SCOPE',event.get('spec_hash')==digest(spec) and event.get('execution_allowed') is False)
        seen_ids.add(event['id']);prior_known=known
        b=sources.get(event.get('source_id'));at=event.get('bar_start',known)
        if event.get('source_id') is not None:
            check('SOURCE_TIMEFRAME_KNOWN_CLOCK',b is not None and tfs.get(event['source_id'])==tf
                  and b['known_at']==known and ('bar_start' not in event or b['time']==at))
        if kind=='PIVOT_CONFIRMED':
            p=event['pivot'];origin=sources.get(p.get('source_id'))
            check('PIVOT_SOURCE_AND_CONFIRMATION',origin is not None and tfs.get(p['source_id'])==tf
                  and p['kind'] in ('high','low') and p['time']==origin['time']
                  and origin['known_at']<=p['known_at']==known and p['price']==origin[p['kind']])
            comp=p.get('comparator')
            if comp:
                check('PIVOT_COMPARATOR_IDENTITY',known_pivot(comp,tf,p['known_at']))
                expected=('HH' if p['price']>comp['price'] else 'LH' if p['price']<comp['price'] else 'EQH') if p['kind']=='high' else ('HL' if p['price']>comp['price'] else 'LL' if p['price']<comp['price'] else 'EQL')
                check('PIVOT_LABEL_STRICT_COMPARISON',p['label']==expected)
            pivots[p['id']]=p;chains[tf].append(p)
            aid=active.get(tf);a=authorities.get(aid)
            if a and p['kind']==('high' if a['direction']=='long' else 'low'):
                if p['price']>a['external']['price'] if a['direction']=='long' else p['price']<a['external']['price']:
                    a['external']=p
        elif kind in ('AMBIGUOUS_TURN','LOCAL_STRUCTURE_RESET'):
            chains[tf]=[]
        elif kind=='UNCLASSIFIED_COVERAGE_GAP':
            for owner_tf in event['timeframes']:
                chains[owner_tf]=[]
                a=authorities.get(active.get(owner_tf))
                if a:a['revoked_at']=known;a['revocation_reason']='UNCLASSIFIED_COVERAGE_GAP'
                if owner_tf==1:
                    for tap in taps.values():tap['coverage_lost_at']=known
            evidence['meaning']='Coverage uncertainty; not a market reversal.'
        elif kind=='MAJOR_BOS':
            direction=event['direction'];ref=event['reference'];pull=event['pullback'];aid=event['authority_id']
            check('MAJOR_OWNER_4H_OR_1H',tf in (240,60))
            check('REFERENCE_AND_PULLBACK_KNOWN_BEFORE_BOS',known_pivot(ref,tf,at,True) and known_pivot(pull,tf,at,True)
                  and ref['time']<pull['time'] and ref['kind']==('high' if direction=='long' else 'low')
                  and pull['kind']==('low' if direction=='long' else 'high'))
            check('STRICT_COMPLETED_CLOSE_CROSS',cross_ok(event,b))
            if event.get('bos_type')=='EXTERNAL_CONTINUATION':
                a=authorities.get(aid)
                check('MAJOR_EXTERNAL_REFERENCE_NOT_INTERNAL',a is not None and valid_owner(aid,tf,at)
                      and a['direction']==direction and a['external']['id']==ref['id'])
                if a:a['protected']=pull;a['latest_bos_id']=event['bos_id'];a['broken_refs'].append(ref['id'])
            else:
                check('BOOTSTRAP_PAIRED_PROGRESS',comparison(ref,direction) and comparison(pull,direction)
                      and ref.get('comparator',{}).get('kind')==ref['kind'] and pull.get('comparator',{}).get('kind')==pull['kind'])
                check('NEW_AUTHORITY_NOT_CHILD_FLIP',event.get('bos_type')=='BOOTSTRAP_OR_REESTABLISHMENT'
                      and not (active.get(tf) and valid_owner(active[tf],tf,at)))
                authorities[aid]=dict(tf=tf,direction=direction,known_at=known,initial_bos_id=event['bos_id'],initial_event_id=event['id'],
                    latest_bos_id=event['bos_id'],protected=pull,external=ref,revoked_at=None,broken_refs=[ref['id']])
                active[tf]=aid
            bosses[event['bos_id']]=event
            evidence.update(reference=ref,pullback=pull,protected_pivot=pull,
                            formation_required_for_structural_BOS=False,
                            wick_cross=(b['high']>ref['price'] if direction=='long' else b['low']<ref['price']) if b else None,
                            close_cross=(b['close']>ref['price'] if direction=='long' else b['close']<ref['price']) if b else None)
            unknowns.append('Major swing definition is a frozen research encoding, not independent teacher approval.')
        elif kind=='HTF_LINK':
            a4,a1=authorities.get(event['h4_authority_id']),authorities.get(event['h1_authority_id'])
            check('ACTUAL_4H_AND_1H_MAJOR_AUTHORITIES',a4 is not None and a1 is not None
                  and valid_owner(event['h4_authority_id'],240,known) and valid_owner(event['h1_authority_id'],60,known)
                  and a4['direction']==a1['direction']==event['direction'])
            links[event['link_id']]=dict(event,revoked_at=None)
            evidence.update(h4_bos_id=a4['initial_bos_id'] if a4 else None,h1_bos_id=a1['initial_bos_id'] if a1 else None,
                h4_known_at=a4['known_at'] if a4 else None,h1_known_at=a1['known_at'] if a1 else None,
                relationship='Same-direction context; no geometric containment required.',
                existing_h1_context_allowed=True)
        elif kind in ('ZONE_CANDIDATE','CHILD_CANDIDATE'):
            members=event.get('source_ids',[]);origins=[sources[s] for s in members if s in sources]
            check('WHOLE_FORMATION_MEMBER_BOUNDS',bool(members) and len(origins)==len(members)
                  and event['member_times']==sorted(set(event['member_times']))==[x['time'] for x in origins]
                  and all(tfs[s]==tf and sources[s]['known_at']<=at for s in members)
                  and event['zone_low']==min((x['low'] for x in origins),default=None)
                  and event['zone_high']==max((x['high'] for x in origins),default=None)
                  and event['selected_known_at']==known and event['origin_start']==event['member_times'][0]
                  and event['origin_end']==event['member_times'][-1])
            check('FROZEN_FORMATION_HYPOTHESES',bool(event.get('branch_names')) and set(event['branch_names'])<=BRANCHES)
            bos=bosses.get(event.get('bos_id'))
            check('FORMATION_BOUND_TO_SPECIFIC_BOS',bos is not None and bos['id'] not in invalid_events and bos['known_at']==known
                  and bos.get('timeframe')==tf and bos['direction']==event['direction'])
            route=(tf,event['direction'],event.get('bos_id'),event.get('tap_id'),tuple(event['member_times']))
            check('SAME_ROUTE_EQUAL_MEMBERS_DEDUPLICATED',route not in members_seen)
            members_seen[route]=event['id']
            physical[(tf,event['direction'],tuple(event['member_times']))].append(event)
            if kind=='ZONE_CANDIDATE':
                check('FORMATION_OWNER_IDENTITY',bos is not None and bos.get('authority_id')==event.get('authority_id'))
                zones[event['zone_id']]=dict(event,retired_at=None)
            else:
                tap=taps.get(event.get('tap_id'));parent=zones.get(event.get('parent_zone_id'))
                check('CHILD_MATCHES_TAPPED_PARENT_AND_LINK',tap is not None and parent is not None and tap['id'] not in invalid_events and parent['id'] not in invalid_events
                      and tap.get('coverage_lost_at') is None and tap['zone_id']==event['parent_zone_id'] and tap['link_id']==event.get('link_id')
                      and tap['direction']==event['direction'] and tap['known_at']<=at
                      and linked_context(event.get('link_id'),at,event['direction']))
                check('CHILD_PARENT_ZONE_STILL_AVAILABLE',parent is not None and
                      parent['retired_at'] is None)
                check('CHILD_MATCHES_TRANSITION_EVIDENCE',bos is not None and bos['kind']=='M1_TRANSITION_BOS'
                      and bos.get('tap_id')==event.get('tap_id') and bos.get('reference')==event.get('reference')
                      and bos.get('pullback')==event.get('pullback'))
                children[event['child_id']]=dict(event,departure_at=known if b and departure(b,event) else None,
                    return_at=None,respect_at=None,retired_at=None)
                link=links.get(event.get('link_id'))
                ancestors={}
                for label,key in [('4H','h4_authority_id'),('1H','h1_authority_id')]:
                    a=authorities.get((link or {}).get(key))
                    ancestors[label]=None if a is None else dict(authority_id=link[key],initial_bos_id=a['initial_bos_id'],
                        known_at=a['known_at'],known_clocks=clocks(a['known_at']),known_before_tap=bool(tap and a['known_at']<=tap['bar_start']))
                evidence.update(parent_authorities=ancestors,parent_zone_id=event.get('parent_zone_id'),tap_event_id=tap['id'] if tap else None,
                                transition_event_id=bos['id'] if bos else None,stop_edge=event['zone_low'] if event['direction']=='long' else event['zone_high'])
            evidence.update(formation_role='Demand' if event['direction']=='long' else 'Supply',
                            origin_members=event['member_times'],source_ids=members,branch_names=event['branch_names'])
            unknowns.append('Formation membership is a preregistered hypothesis; responsible teacher formation remains unverified.')
        elif kind=='HTF_TAP':
            z=zones.get(event.get('zone_id'));link=links.get(event.get('link_id'))
            check('PARENTS_KNOWN_BEFORE_TAP',linked_context(event.get('link_id'),at,event['direction']))
            check('TAP_MATCHES_LINKED_OWNER_ZONE',z is not None and link is not None and z['id'] not in invalid_events
                  and z['authority_id'] in (link['h4_authority_id'],link['h1_authority_id'])
                  and z['direction']==event['direction'] and z['known_at']<=at
                  and z['retired_at'] is None)
            check('ACTUAL_OVERLAP_WITHOUT_FAR_BREACH',b is not None and z is not None and overlap(b,z) and not breach(b,z))
            check('PRE_TAP_PIVOT_IDENTITIES',all(known_pivot(p,1,known) and p['time']<=at for p in event.get('pre_tap_pivots',[])))
            taps[event['tap_id']]=dict(event,coverage_lost_at=None)
            before=[p for p in event.get('pre_tap_pivots',[]) if p['known_at']<=at]
            evidence.update(pre_tap_pivots=before,tap_close_pivot_observations=[p for p in event.get('pre_tap_pivots',[]) if p['known_at']>at],
                            parent_zone_id=event['zone_id'],h4_authority_id=link['h4_authority_id'] if link else None,
                            h1_authority_id=link['h1_authority_id'] if link else None)
        elif kind=='M1_TRANSITION_BOS':
            tap=taps.get(event.get('tap_id'));direction=event['direction'];comp=event.get('comparison_chains',{})
            check('LOCAL_BOS_NOT_MAJOR_REPLACEMENT',tf==1 and 'authority_id' not in event)
            check('POST_TAP_LINKED_LOCAL_SEQUENCE',tap is not None and tap['id'] not in invalid_events and tap['known_at']<=at
                  and tap['bar_start']<at and tap.get('coverage_lost_at') is None and linked_context(event.get('link_id'),at,direction))
            valid=True;last=[]
            for side,label in [('high','HH' if direction=='long' else 'LH'),('low','HL' if direction=='long' else 'LL')]:
                chain=comp.get(side,[])
                if len(chain)!=3:valid=False;continue
                valid &= all(known_pivot(p,1,at,True) and p['kind']==side for p in chain)
                valid &= all(n['label']==label and n.get('comparator',{}).get('id')==p['id']
                             and (n['price']>p['price'] if direction=='long' else n['price']<p['price'])
                             for p,n in zip(chain,chain[1:]))
                valid &= bool(tap and all(p['time']>tap['bar_start'] for p in chain[-2:]))
                last.extend(chain[-2:])
            check('TWO_POST_TAP_STRICT_PAIRS_WITH_COMPARATORS',valid)
            check('STRUCTURE_KNOWN_STRICTLY_BEFORE_BOS',bool(last) and event.get('structure_known_at')==max(p['known_at'] for p in last)<at)
            ref,pull=event['reference'],event['pullback'];known_chain=[p for p in chains[1] if p['known_at']<at]
            refs=[p for p in known_chain if p['kind']==('high' if direction=='long' else 'low')]
            pulls=[p for p in known_chain if p['kind']==('low' if direction=='long' else 'high')]
            check('LATEST_REFERENCE_AND_INTERVENING_PULLBACK',bool(refs and pulls) and refs[-1]['id']==ref['id']
                  and pulls[-1]['id']==pull['id'] and ref['time']<pull['time']
                  and known_pivot(ref,1,at,True) and known_pivot(pull,1,at,True))
            check('STRICT_COMPLETED_CLOSE_CROSS',cross_ok(event,b))
            bosses[event['bos_id']]=event
            prior=[p for p in (tap or {}).get('pre_tap_pivots',[]) if p['known_at']<=(tap or {}).get('bar_start',-1)]
            opposing_labels={'LH','LL'} if direction=='long' else {'HH','HL'}
            counter=[p for p in prior if p['label'] in opposing_labels]
            evidence.update(comparison_chains=comp,reference=ref,pullback=pull,pre_tap_history=prior,
                            prior_countertrend_comparisons=counter,structure_known_at=event.get('structure_known_at'))
            if not opposing_labels<=set(p['label'] for p in counter):
                unknowns.append('Prior countertrend pair not fully evidenced at tap opening; frozen post-tap rule does not add an automatic pre-tap filter.')
        elif kind=='AUTHORITY_REVOKED':
            a=authorities.get(event.get('authority_id'))
            check('OWNER_REVOCATION_SCOPE',a is not None and a['tf']==tf)
            if event.get('reason')=='OWNER_CLOSE_THROUGH_PROTECTED_PIVOT':
                check('OWNER_PROTECTED_CLOSE_NOT_CHILD_WICK',a is not None and b is not None and
                      (b['close']<a['protected']['price'] if a['direction']=='long' else b['close']>a['protected']['price']))
            elif event.get('reason')!='UNCLASSIFIED_COVERAGE_GAP':
                check('FROZEN_OWNER_REVOCATION_REASON',False)
            if a:a['revoked_at']=known;a['revocation_reason']=event.get('reason');active[tf]=None
            evidence['meaning']='Owner permission ends; opposite authority requires its own major sequence.'
        elif kind=='LINK_REVOKED':
            if event.get('link_id') in links:links[event['link_id']]['revoked_at']=known
        elif kind=='PARENT_ZONE_BREACHED':
            z=zones.get(event.get('zone_id'))
            check('PARENT_WICK_FAILURE_NOT_OWNER_DIRECTION_FLIP',z is not None and b is not None and breach(b,z))
            if z:z['retired_at']=known
            evidence['owner_direction_changed_by_this_event']=False
        elif kind in ('CHILD_DEPARTURE_OBSERVED','RETURN_OBSERVED','RESPECT_OBSERVED','ENTRY_REVIEW','ENTRY_REVIEW_BLOCKED',
                      'CHILD_ZONE_BREACHED','CHILD_PERMISSION_REVOKED','CHILD_COVERAGE_UNKNOWN'):
            c=children.get(event.get('child_id'))
            check('LIFECYCLE_ORIGINAL_CHILD_IDENTITY',c is not None and c['id'] not in invalid_events)
            if c:
                evidence.update(original_selection_id=c['id'],parent_zone_id=c['parent_zone_id'],
                    zone_low=c['zone_low'],zone_high=c['zone_high'],child_failure_does_not_flip_parent=True)
                if kind=='CHILD_DEPARTURE_OBSERVED':
                    check('FULL_ZONE_DEPARTURE',b is not None and departure(b,c) and c['selected_known_at']<=at)
                    c['departure_at']=known
                elif kind=='RETURN_OBSERVED':
                    check('RETURN_AFTER_SELECTION_AND_DEPARTURE',b is not None and overlap(b,c)
                          and c['selected_known_at']<=at and c['departure_at'] is not None and c['departure_at']<=at
                          and c['return_at'] is None and (c['retired_at'] is None or c['retired_at']>=known))
                    c['return_at']=known
                    evidence['same_bar_failure']=event.get('failed_same_bar',False)
                elif kind=='RESPECT_OBSERVED':
                    check('SEPARATE_RETURN_THEN_RESPECT',b is not None and c['return_at'] is not None
                          and c['return_at']<=at and departure(b,c) and c['respect_at'] is None
                          and (c['retired_at'] is None or c['retired_at']>at))
                    c['respect_at']=known
                    unknowns.append('Separate near-edge close is assumed research respect, not a sourced universal entry trigger or fill.')
                elif kind in ('ENTRY_REVIEW','ENTRY_REVIEW_BLOCKED'):
                    check('REVIEW_FOLLOWS_RECORDED_RESPECT',c['respect_at']==known)
                    if kind=='ENTRY_REVIEW':
                        check('RESEARCH_ONLY_KNOWN_UNREACHED_TARGET',event.get('target_known') is True
                              and event.get('target_already_reached') is False and event.get('target') is not None
                              and event['target']['known_at']<=at)
                elif kind=='CHILD_ZONE_BREACHED':
                    check('CHILD_FAR_EDGE_BREACH',b is not None and breach(b,c))
                    c['retired_at']=known
                else:c['retired_at']=known
        if kind in ('FORMATION_REJECTED','CHILD_FORMATION_REJECTED'):
            evidence.update(recorded_rejection_reason=event.get('reason'),branch_names=event.get('branch_names'))
            all_rejections.append(dict(event_id=event['id'],known_at=known,reason=event.get('reason')))
        if reasons:
            invalid_events.add(event['id'])
            if kind=='PIVOT_CONFIRMED':invalid_pivots.add(event['pivot']['id'])
            run_violations.extend(dict(event_id=event['id'],kind=kind,rule=r) for r in reasons)
        if kind in PRIMARY or reasons:
            roles=['Market Structure','B.O.B'] if kind in ('MAJOR_BOS','M1_TRANSITION_BOS') else ['Sequence Coordinator']
            if kind=='MAJOR_BOS':roles.insert(0,'4H' if tf==240 else '1H')
            if kind in ('ZONE_CANDIDATE','CHILD_CANDIDATE'):roles=['Demand' if event['direction']=='long' else 'Supply','Sequence Coordinator']
            if kind in ('RETURN_OBSERVED','RESPECT_OBSERVED','ENTRY_REVIEW','ENTRY_REVIEW_BLOCKED'):roles=['Weave','1M']
            if kind=='HTF_LINK':roles=['4H','1H','Sequence Coordinator']
            if kind=='HTF_TAP':roles=['4H','1H','1M','Sequence Coordinator']
            row=dict(row_id='role:'+digest([VERSION,event['id']])[:24],event_id=event['id'],kind=kind,timeframe=tf,
                direction=event.get('direction'),known_at=known,known_clocks=clocks(known),bar_start=event.get('bar_start'),
                bar_clocks=clocks(event.get('bar_start')),phase='STUDY' if start<known<=end else 'WARMUP_OR_OUTSIDE',
                roles=roles,status='SPEC_CONFLICT' if reasons else 'RECORDED_REJECTION' if kind in ('FORMATION_REJECTED','CHILD_FORMATION_REJECTED') else 'SPEC_ACCOUNTING_ONLY',
                reasons=reasons,checks=checks,unresolved=unknowns,evidence=evidence,
                identities={k:event[k] for k in ('authority_id','bos_id','link_id','zone_id','parent_zone_id','tap_id','child_id','physical_origin_id') if k in event},
                source_id=event.get('source_id'),teacher_verified=False,execution_allowed=False)
            rows.append(row)
    groups=[]
    for (tf,direction,members),items in sorted(physical.items()):
        ids={x.get('physical_origin_id') for x in items}
        if len(ids)!=1 or None in ids:
            run_violations.append(dict(event_id=items[0]['id'],kind='PHYSICAL_ORIGIN_GROUP',rule='SAME_MEMBERS_DIFFERENT_PHYSICAL_IDENTITIES'))
        groups.append(dict(group_id='physical-review:'+digest([tf,direction,members])[:24],timeframe=tf,direction=direction,
            member_times=list(members),claimed_physical_ids=sorted(str(x) for x in ids),
            proposal_ids=[x['id'] for x in items],route_count=len(items),physical_count=1,
            rule='Identical native members grouped across routes; equal bounds with different members remain distinct.'))
    counts=Counter(r['kind'] for r in rows if r['phase']=='STUDY')
    return dict(version=VERSION,status='SPEC_CONFLICTS_RECORDED' if run_violations else 'BOUNDED_SPEC_ACCOUNTING_ONLY',
        rows=rows,violations=run_violations,recorded_rejections=all_rejections,physical_origin_groups=groups,
        study_counts=dict(counts),scope=spec['scope'],spec_hash=digest(spec),
        teacher_verified=False,execution_allowed=False,performance=None,specialist_attestations_created=False,
        warnings=['No actual specialist approvals or teacher-fidelity clearance are created by this deterministic role map.',
                  '4H is mandatory for this study; earlier role text allowing optional 4H does not govern this frozen scope.',
                  'An existing known 1H context may link when 4H becomes known; this is explicitly permitted by SPEC.confirmation.',
                  'No 15M/5M/EMA requirement or new threshold added. Formation branch choice and respect remain research assumptions.',
                  'This checks recorded sequence semantics and exact formation members/bounds, not exhaustive event discovery, automatic formation-selector reproduction, independent source resampling, PnL, or all causal behaviors.']+
                  (['No major BOS observed in the study event stream; this is not evidence of teacher proficiency.'] if not counts['MAJOR_BOS'] else []))


def write_review(result,output,bindings,rules_sha):
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    result=copy.deepcopy(result);result['bindings']=dict(bindings,role_sources_sha256=rules_sha,
        reviewer_code_sha256=digest(Path(__file__).read_bytes()))
    (out/'REVIEW.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'chronology.jsonl').write_text(''.join(canon(r)+'\n' for r in result['rows']))
    fields=['known_CT','known_ET','timeframe','stage','direction','event_id','parent_link_ids','status','reasons','unresolved']
    with (out/'chronology.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for r in result['rows']:
            writer.writerow(dict(known_CT=r['known_clocks']['CT'],known_ET=r['known_clocks']['ET'],timeframe=r['timeframe'],
                stage=r['kind'],direction=r['direction'],event_id=r['event_id'],parent_link_ids=canon(r['identities']),
                status=r['status'],reasons='; '.join(r['reasons']),unresolved='; '.join(r['unresolved'])))
    text='[KNOWN] '+result['status']+'\n\n'
    text+='Recorded study stages: '+canon(result['study_counts'])+'\n'
    text+='Recorded frozen-spec conflicts: '+str(len(result['violations']))+'\n\n'
    text+='\n'.join(result['warnings'])+'\n\n'
    text+='Confidence: bounded deterministic accounting only.\nUnknowns: discretionary teacher formation, full teacher fidelity, and missing source coverage.\nHow to verify: inspect chronology.jsonl against the exact sealed plan/events; run causal verification and actual specialist/teacher review separately.\n'
    (out/'SUMMARY.txt').write_text(text)
    completion=dict(status='ROLE_ACCOUNTING_SAVED',execution_allowed=False,teacher_verified=False,
        files={p.name:digest(p.read_bytes()) for p in sorted(out.iterdir()) if p.is_file()})
    (out/'COMPLETION.json').write_text(json.dumps(completion,indent=2)+'\n')
    return completion


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True,help='Completed sealed study run; never a live source path')
    parser.add_argument('--output',type=Path,required=True,help='New exclusive output directory')
    parser.add_argument('--rules',type=Path,default=Path(__file__).with_name('ROLE_RULES.json'))
    args=parser.parse_args();rules,rules_sha=role_sources(args.rules)
    events,plan,spec,bindings=load_sealed(args.run)
    result=review(events,plan,spec,rules)
    write_review(result,args.output,bindings,rules_sha)
    print(json.dumps(dict(status=result['status'],violations=len(result['violations']),output=str(args.output),
                          teacher_verified=False,execution_allowed=False)))
    return 2 if result['violations'] else 0

if __name__=='__main__':
    raise SystemExit(main())
