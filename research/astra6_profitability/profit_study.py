"""One-command, quote-priced ASTRA research study. No broker operations.

The frozen signal engine remains unchanged. Missing acquisition evidence stops
before a market result is computed. Synthetic software tests are never promoted
to market performance, and September remains exposed development data.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
from itertools import groupby
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

from quote_execution import Candidate, Config, Pivot, Policy, simulate_many

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
SIGNALS = HERE.parent / 'september_2026'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def epoch_ms(value):
    return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000)


def save(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True, allow_nan=False) + '\n')


def load_protocol(path):
    protocol = json.loads(Path(path).read_bytes())
    if (protocol.get('id') != 'ASTRA6_QUOTE_PROFIT_RESEARCH_V1'
            or protocol.get('execution_allowed') is not False
            or protocol.get('live_orders_enabled') is not False
            or protocol.get('market', {}).get('period_role') != 'EXPOSED_DEVELOPMENT_NOT_OOS'):
        raise ValueError('Explicit offline research protocol required')
    for name, digest in protocol['source_sha256'].items():
        source = (REPOSITORY / name).resolve()
        if REPOSITORY not in source.parents or sha(source) != digest:
            raise ValueError('Pinned signal/data source changed: ' + name)
    return protocol


def extract_candidates(events, study_start_ms, study_end_ms):
    """Join immutable events using only state known at each decision cutoff.

    Future invalidation times are attached as replay schedules, never applied
    before their clocks. The simulator evaluates them only as quotes advance.
    Unlike a final snapshot join, this does not retrospectively reject a setup
    merely because its parent eventually failed.
    """
    children, zones, links = {}, {}, {}
    revoked_authorities, revoked_links, retired_zones, retired_children = set(), set(), set(), set()
    candidates, pivots, provenance, native_blocks = [], [], [], []
    index_by_child = {}
    seen, previous = set(), -1
    for event in events:
        if (type(event.get('known_at')) is not int or event['known_at'] < previous
                or not isinstance(event.get('id'), str) or event['id'] in seen
                or event.get('execution_allowed') is not False):
            raise ValueError('Unordered, duplicate or nonresearch event journal')
        previous = event['known_at']; seen.add(event['id'])

    def valid_child(child_id):
        c = children.get(child_id)
        if not c:
            return False
        z, link = zones.get(c['parent_zone_id']), links.get(c['link_id'])
        return bool(z and link and child_id not in retired_children
                    and c['parent_zone_id'] not in retired_zones
                    and c['link_id'] not in revoked_links
                    and z['authority_id'] not in revoked_authorities
                    and link['h4_authority_id'] not in revoked_authorities
                    and link['h1_authority_id'] not in revoked_authorities)

    for known, grouped in groupby(events, key=lambda e: e['known_at']):
        batch = list(grouped)
        unavailable_children = set()
        for e in batch:
            kind = e['kind']
            if kind == 'ZONE_CANDIDATE':
                zones[e['zone_id']] = e
            elif kind == 'HTF_LINK':
                links[e['link_id']] = e
            elif kind == 'CHILD_CANDIDATE':
                children[e['child_id']] = e
            elif kind == 'AUTHORITY_REVOKED':
                revoked_authorities.add(e['authority_id'])
            elif kind == 'LINK_REVOKED':
                revoked_links.add(e['link_id'])
            elif kind == 'PARENT_ZONE_BREACHED':
                retired_zones.add(e['zone_id'])
            elif kind in ('CHILD_PERMISSION_REVOKED', 'CHILD_ZONE_BREACHED',
                          'CHILD_COVERAGE_UNKNOWN'):
                retired_children.add(e['child_id'])
            elif kind == 'CHILD_CONTEXT_UNAVAILABLE':
                # Native coverage can recover without retiring this child.
                # Existing pending entries cancel now; later genuine reviews
                # still use the child's actual permanent lifecycle.
                unavailable_children.add(e['child_id'])
            elif kind == 'PIVOT_CONFIRMED' and e.get('timeframe') == 1:
                p = e['pivot']
                pivots.append(Pivot(known_at_ms=p['known_at'] * 1000,
                                    kind=p['kind'], price=p['price'],
                                    pivot_time_ms=p['time'] * 1000, source_id=e['id']))
            elif kind == 'ENTRY_REVIEW_BLOCKED' and study_start_ms <= known * 1000 < study_end_ms:
                native_blocks.append({'event_id': e['id'], 'child_id': e['child_id'],
                                      'known_at_ms': known * 1000, 'reason': e['review_status']})
        # Apply ALL same-cutoff revocations before later execution eligibility.
        for child_id, i in index_by_child.items():
            if (not valid_child(child_id) or child_id in unavailable_children) and candidates[i].invalidated_at_ms is None:
                candidates[i] = replace(candidates[i], invalidated_at_ms=known * 1000)
        for e in batch:
            if e['kind'] != 'ENTRY_REVIEW' or not study_start_ms <= known * 1000 < study_end_ms:
                continue
            child_id = e['child_id']
            if child_id in index_by_child:
                raise ValueError('Multiple native reviews for one child')
            c = children.get(child_id)
            if c is None or c['parent_zone_id'] not in zones or c['link_id'] not in links:
                raise ValueError('Review lacks original child/parent/link lineage')
            z = zones[c['parent_zone_id']]
            if c['known_at'] > e['bar_start'] or e['direction'] != c['direction']:
                raise ValueError('Review uses premature or conflicting child geometry')
            target = e.get('target')
            candidate = Candidate(
                child_id=child_id, decision_time_ms=known * 1000,
                bar_start_ms=e['bar_start'] * 1000, direction=e['direction'],
                stop=c['zone_low'] if e['direction'] == 'long' else c['zone_high'],
                target=target['price'] if target else None,
                target_known_at_ms=target['known_at'] * 1000 if target else None,
                valid_at_decision=valid_child(child_id) and child_id not in unavailable_children,
                target_already_reached=e.get('target_already_reached', True),
                parent_zone_id=c['parent_zone_id'], target_bos_id=z['bos_id'])
            index_by_child[child_id] = len(candidates); candidates.append(candidate)
            provenance.append({'review_event_id': e['id'], 'child_event_id': c['id'],
                               'parent_event_id': z['id'], 'child_id': child_id,
                               'parent_zone_id': c['parent_zone_id'], 'link_id': c['link_id'],
                               'target_bos_id': z['bos_id'], 'target_at_review': target,
                               'valid_after_complete_cutoff': candidate.valid_at_decision})
    return candidates, pivots, {'review_count': len(candidates),
                                'native_blocked_count': len(native_blocks),
                                'native_blocked': native_blocks, 'provenance': provenance}


def policies_from_protocol(protocol):
    return [Policy(p['id'], break_even_after_r=p.get('breakeven_after_r'),
                   pivot_trail_after_r=p.get('trail_after_r'),
                   max_hold_minutes=p.get('max_hold_minutes'), fixed_r_control=p.get('target_r'))
            for p in protocol['policies']]


def load_sealed_events(structure, data):
    """Bind the replay handoff to the same raw-validated acquisition."""
    structure = Path(structure)
    paths = [structure / 'PIPELINE.json', structure / 'replay/COMPLETION.json',
             structure / 'replay/events.jsonl']
    captured = [p.read_bytes() for p in paths]
    hashes = [hashlib.sha256(b).hexdigest() for b in captured]
    pipeline, completion = [json.loads(b) for b in captured[:2]]
    if (pipeline.get('status') != 'RESEARCH_PIPELINE_FINISHED_NOT_DEPLOYMENT'
            or pipeline.get('acquisition_sha256') != data['manifest_sha256']
            or pipeline.get('source_sha256') != data['csv_sha256']
            or pipeline.get('replay_completion_sha256') != hashes[1]
            or completion.get('output_hashes', {}).get('events.jsonl') != hashes[2]):
        raise ValueError('Native replay handoff is not bound to validated inputs/events')
    roles = {'engine': 'engine.py', 'runner': 'month_replay.py', 'spec': 'SPEC.json',
             'clarifications': 'IMPLEMENTATION_CLARIFICATIONS.json'}
    expected = {role: data['source_sha256']['research/september_2026/' + name]
                for role, name in roles.items()}
    if any(pipeline.get('stage_source_hashes', {}).get(name) != expected[role]
           for role, name in roles.items()):
        raise ValueError('Native replay used different signal sources')
    roles.update(source='source.csv', acquisition='acquisition.json')
    expected.update(source=data['csv_sha256'], acquisition=data['manifest_sha256'])
    for role, name in roles.items():
        path = structure / 'replay/captures' / name
        if (completion.get('captured_input_hashes', {}).get(role) != expected[role]
                or sha(path) != expected[role]):
            raise ValueError('Native captured input differs from raw-validated source: ' + role)
        paths.append(path); hashes.append(expected[role])
    events = [json.loads(line) for line in captured[2].splitlines()]
    return events, {str(p): digest for p, digest in zip(paths, hashes)}


def add_cost_sensitivity(result, protocol):
    """Separate post-fill cost deductions, never fictional replacement prices."""
    scenarios = protocol['cost_hypotheses']['additional_slippage_cost_points_per_leg']
    for policy in result['policies'].values():
        closed = [t for t in policy['trades'] if t['status'] == 'CLOSED']
        stress = []
        for per_leg in scenarios:
            amounts = [t['net_points'] - 2 * per_leg for t in closed]
            rs = [(t['net_points'] - 2 * per_leg) / t['initial_risk_points'] for t in closed]
            wins = sum(x for x in amounts if x > 0)
            losses = -sum(x for x in amounts if x < 0)
            stress.append({'additional_cost_points_per_leg': per_leg,
                           'closed_trades': len(closed),
                           'net_points': sum(amounts) if closed else None,
                           'net_r_sum': sum(rs) if closed else None,
                           'mean_net_r': statistics.mean(rs) if rs else None,
                           'median_net_r': statistics.median(rs) if rs else None,
                           'profit_factor': wins / losses if losses else None,
                           'profit_factor_undefined': 'NO_LOSSES_OR_NO_TRADES' if not losses else None})
        policy['cost_sensitivity'] = stress
        split = {}
        for direction in ('long', 'short'):
            trades = [t for t in closed if t['direction'] == direction]
            split[direction] = {'closed_trades': len(trades),
                                'net_points': sum(t['net_points'] for t in trades) if trades else None}
        policy['direction_breakdown'] = split
        days, contexts = defaultdict(list), defaultdict(list)
        for trade in policy['trades']:
            day = datetime.fromtimestamp(trade['entry_time_ms'] / 1000, timezone.utc).date().isoformat()
            days[day].append(trade)
            contexts[trade['parent_zone_id']].append(trade)

        def breakdown(groups, key):
            rows = []
            for name, trades in sorted(groups.items()):
                completed = [t for t in trades if t['status'] == 'CLOSED']
                rows.append({key: name, 'entered_trades': len(trades),
                             'completed_trades': len(completed),
                             'censored_trades': len(trades) - len(completed),
                             'closed_subset_net_points': sum(t['net_points'] for t in completed) if completed else None,
                             'closed_subset_net_r': sum(t['net_r'] for t in completed) if completed else None})
            return rows

        policy['entry_day_breakdown'] = breakdown(days, 'entry_utc_day')
        policy['parent_context_breakdown'] = breakdown(contexts, 'parent_zone_id')
        policy['sample_scope'] = {
            'entry_utc_days': len(days), 'parent_contexts': len(contexts),
            'trade_independence_established': False,
            'uncertainty_estimation': 'NOT_RUN',
            'random_entry_comparison': 'NOT_RUN',
            'oos_status': 'NOT_RUN',
            'chronology_basis': 'UTC day of entry; realized results are the closed subset only.'}


def run(acquisition, output, protocol_path=HERE / 'PROFIT_PROTOCOL.json'):
    started = time.perf_counter(); output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    result = {'schema': 'ASTRA6_PROFIT_STUDY_V1', 'status': 'STARTED',
              'execution_allowed': False, 'live_orders_enabled': False,
              'profitability_established': False, 'real_market_backtest_completed': False,
              'publication_integrity_verified': False,
              'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'acquisition': str(Path(acquisition).resolve()),
              'protocol_sha256': sha(protocol_path),
              'software_source_sha256': {p.name: sha(p) for p in HERE.glob('*.py')},
              'period_role': 'EXPOSED_DEVELOPMENT_NOT_OOS'}
    save(output / 'RESULT.json', result)
    try:
        protocol = load_protocol(protocol_path)
        save(output / 'PROFIT_PROTOCOL.json', protocol)
        from data_source import validate_acquisition, iter_quotes
        validated = validate_acquisition(Path(acquisition), protocol)
        result['data'] = validated['summary']
        acquisition = Path(acquisition).resolve()
        if acquisition.is_dir():
            acquisition = acquisition / 'MANIFEST.json'
        # Reuse the already verified signal replay and every-cutoff causal gate.
        command = [sys.executable, '-B', str(SIGNALS / 'run_study.py'),
                   '--acquisition', str(acquisition), '--output', str(output / 'structure')]
        with (output / 'structure.log').open('w') as log:
            check = subprocess.run(command, cwd=REPOSITORY, stdout=log, stderr=subprocess.STDOUT)
        if check.returncode:
            raise ValueError('BLOCKED_NATIVE_CAUSAL_PIPELINE: see structure.log')
        structure = output / 'structure'
        events, replay_bindings = load_sealed_events(structure, validated['summary'])
        result['native_replay_handoff_sha256'] = replay_bindings
        start = epoch_ms(protocol['market']['study_start_utc'])
        end = epoch_ms(protocol['market']['end_exclusive_utc'])
        candidates, pivots, universe = extract_candidates(events, start, end)
        save(output / 'CANDIDATE_UNIVERSE.json', universe)
        save(output / 'CANDIDATES.json', [asdict(c) for c in candidates])
        result['universe'] = {'review_count': len(candidates),
                              'native_blocked_count': universe['native_blocked_count'],
                              'unique_parent_contexts': len({c.parent_zone_id for c in candidates})}
        cfg = protocol['execution_hypotheses']
        config = Config(entry_latency_ms=cfg['latency_ms'], pending_expiry_ms=cfg['pending_window_ms'],
                        max_quote_gap_ms=cfg['max_quote_gap_ms'],
                        commission_per_million=protocol['cost_hypotheses']['commission_rate_per_leg'] * 1e6,
                        study_start_ms=start, study_end_ms=end)
        simulation_started = time.perf_counter()
        simulation = simulate_many(iter_quotes(validated), candidates, pivots,
                                   policies_from_protocol(protocol), config)
        add_cost_sensitivity(simulation, protocol)
        simulation['quote_simulation_seconds'] = time.perf_counter() - simulation_started
        simulation['profitability_established'] = False
        simulation['actual_broker_fills_verified'] = False
        simulation['oos_status'] = 'NOT_RUN'
        if {p.name: sha(p) for p in HERE.glob('*.py')} != result['software_source_sha256']:
            raise ValueError('Research software changed during study')
        load_protocol(protocol_path)
        if sha(protocol_path) != result['protocol_sha256']:
            raise ValueError('Research protocol changed during study')
        if any(sha(path) != digest for path, digest in replay_bindings.items()):
            raise ValueError('Sealed native replay changed during quote simulation')
        # Publish market output only after every source and handoff recheck.
        save(output / 'QUOTE_SIMULATION.json', simulation)
        result.update(status='DEVELOPMENT_SIMULATION_COMPLETED_NOT_PROFITABILITY_ESTABLISHED',
                      real_market_backtest_completed=True,
                      publication_integrity_verified=True,
                      simulation_sha256=sha(output / 'QUOTE_SIMULATION.json'),
                      policy_summaries={k: v['summary'] for k, v in simulation['policies'].items()},
                      limitations=['September is previously exposed development data.',
                                   'The frozen calendar marks 2026-09-07 unresolved.',
                                   'No untouched OOS, random-entry comparison or broker-fill validation.',
                                   'Quote prices, commission proxy and exit mechanics are research hypotheses.',
                                   'Account size, actual fee tier, depth and live fills remain unverified.'])
        if not candidates:
            result['status'] = 'NO_NATIVE_ENTRY_REVIEWS_IN_VALIDATED_DEVELOPMENT_WINDOW'
        returncode = 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        result.update(status='BLOCKED_NO_PROFIT_RESULT', reason=str(exc), error_type=type(exc).__name__,
                      real_market_backtest_completed=False, publication_integrity_verified=False)
        result.pop('policy_summaries', None)
        result.pop('simulation_sha256', None)
        returncode = 2
    result['seconds'] = time.perf_counter() - started
    save(output / 'RESULT.json', result)
    return result, returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acquisition', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result, code = run(args.acquisition, args.output)
    print(json.dumps({'status': result['status'], 'reason': result.get('reason'),
                      'profitability_established': False,
                      'result': str(args.output.resolve() / 'RESULT.json')}))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
