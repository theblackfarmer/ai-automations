"""Separate causal 5M/15M context; no inferred major trend or trade authority."""
from copy import deepcopy
from autonomous_zone_review_v1 import AutonomousZoneReview


class MultiTimeframeContext:
    def __init__(self, source_hashes, symbol, feed):
        if set(source_hashes) != {300, 900}:
            raise ValueError('Explicit native 5M and 15M sources required')
        self.observers = {step: AutonomousZoneReview(sha, symbol, feed, step)
                          for step, sha in source_hashes.items()}
        self._last = {}
        self._zones = {300: {}, 900: {}}
        self._cursors = {300: 0, 900: 0}
        self._activity = {300: {}, 900: {}}
        self._clock = None

    def feed_bar(self, step, bar):
        if type(step) is not int or step not in self.observers:
            raise ValueError('Native 5M or 15M required')
        obs = self.observers[step]
        valid = obs.review._validate(bar)
        if self._clock is not None and valid['known_at'] < self._clock:
            raise ValueError('Context delivery must follow completed-bar clocks')
        obs.feed_bar(valid)
        self._clock = valid['known_at']
        self._last[step] = valid
        events = obs.events_since(self._cursors[step])
        self._cursors[step] += len(events)
        for event in events:
            if event['kind'] == 'ESTABLISHED_ZONE_REGISTERED':
                self._zones[step][event['zone_id']] = deepcopy(event)
                self._activity[step][event['zone_id']] = dict(last_overlap=None,
                    boundary_crossing_count=0, last_boundary_crossing=None)
            elif event['kind'] == 'ESTABLISHED_ZONE_REVIEW_BLOCKED':
                self._zones[step].pop(event['zone_id'], None)
                self._activity[step].pop(event['zone_id'], None)
            elif event['kind'] == 'ESTABLISHED_ZONE_TAP_OBSERVED':
                self._activity[step][event['zone_id']]['last_overlap'] = deepcopy(event)
            elif event['kind'] == 'ESTABLISHED_ZONE_BOUNDARY_CROSSED':
                activity = self._activity[step][event['zone_id']]
                activity['boundary_crossing_count'] += 1
                activity['last_boundary_crossing'] = deepcopy(event)
        return deepcopy(events)

    def snapshot(self):
        return deepcopy(dict(known_at=self._clock, execution_allowed=False,
            major_trend='NOT_AUTONOMOUSLY_VERIFIED',
            timeframes={step: dict(last_closed_bar=self._last.get(step),
                coverage_status=('NOT_OBSERVED' if step not in self._last else
                  'CURRENT_COMPLETE_BAR' if self._last[step]['known_at'] == self._clock//step*step
                  else 'LAGGING_OR_MISSING'),
                observed_leg=obs.tracker.run_direction,
                pivots=obs.tracker.pivots,
                unverified_origin_evidence=obs.origins.snapshot(),
                local_zone_proposals=[dict(registration=z, activity=self._activity[step][zid])
                    for zid,z in self._zones[step].items()])
                for step, obs in self.observers.items()}))
