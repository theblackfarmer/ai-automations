"""Research-only adapter from ASTRA ENTRY_REVIEW events to exit-study candidates.

No fill is asserted: hypothetical entry is next observed minute open, with
stop at the frozen child-zone far edge. This is an assumption, not an order.
"""
import csv
import json
from pathlib import Path

def candidates(events, bars):
    out=[]; blocked=[]
    for event in events:
        kind=event.get('kind')
        if kind not in ('ENTRY_REVIEW','ENTRY_REVIEW_BLOCKED'):
            continue
        eid=event.get('id')
        if kind!='ENTRY_REVIEW':
            blocked.append({'id':eid,'reason':'ENGINE_REVIEW_BLOCKED'});continue
        side=event.get('direction')
        if side not in ('LONG','SHORT'):
            blocked.append({'id':eid,'reason':'MISSING_DIRECTION'});continue
        low=event.get('zone_low');high=event.get('zone_high')
        t=event.get('known_at')
        if type(t) is not int or type(low) not in (int,float) or type(high) not in (int,float) or not 0<low<high:
            blocked.append({'id':eid,'reason':'MISSING_FROZEN_ZONE_OR_CLOCK'});continue
        # Entry is at next minute's open, not at the close that recognized respect.
        bar=bars.get(t)
        if bar is None:
            blocked.append({'id':eid,'reason':'NEXT_OPEN_UNOBSERVED'});continue
        entry=float(bar[0]);stop=float(low if side=='LONG' else high)
        if (side=='LONG' and entry<=stop) or (side=='SHORT' and entry>=stop):
            blocked.append({'id':eid,'reason':'NONPOSITIVE_INITIAL_RISK'});continue
        out.append({'id':eid,'entry_epoch':t,'side':side,'entry':entry,'stop':stop,
                    'source_event_id':eid,'assumption':'NEXT_OBSERVED_OPEN_AND_CHILD_FAR_EDGE_STOP'})
    return out,blocked

def main():
    import argparse
    from exit_comparison_v1 import load_bars
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--events',type=Path,required=True)
    p.add_argument('--bars',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    events=[json.loads(line) for line in a.events.read_text().splitlines() if line.strip()]
    rows,blocked=candidates(events,load_bars(a.bars))
    a.output.mkdir(parents=True,exist_ok=False)
    with (a.output/'signals.csv').open('x',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['id','entry_epoch','side','entry','stop','source_event_id','assumption'])
        w.writeheader();w.writerows(rows)
    (a.output/'blocked.json').write_text(json.dumps(blocked,indent=2)+'\n')
    (a.output/'MANIFEST.json').write_text(json.dumps({'research_only':True,'eligible':len(rows),
        'blocked':len(blocked),'fill_verified':False,'execution_allowed':False},indent=2)+'\n')
if __name__=='__main__':main()
