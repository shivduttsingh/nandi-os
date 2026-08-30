from pathlib import Path
import sys
from bisect import bisect_right
from datetime import timedelta

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from scripts import search_strategy_70_chain_pressure as c


def simulate_next_open(e,cfg,series,times):
    if not series or not times:return None
    start=bisect_right(times,e.signal_time);deadline=e.signal_time+timedelta(minutes=2);idx=-1;entry=0.0
    for i in range(start,len(series)):
        b=series[i]
        if b.timestamp.date()!=e.day or b.timestamp>deadline:break
        idx=i;entry=b.open+0.20;break
    if idx<0:return None
    stop=max(.05,entry-cfg.stop_points);target=entry+cfg.target_points;et=series[idx].timestamp;cut=et+timedelta(minutes=20);future=[b for b in series[idx:] if b.timestamp.date()==e.day and b.timestamp<=cut]
    if not future:return None
    for b in future:
        if b.low<=stop:return c.Trade(e.day,e.signal_time,et,e.direction,e.strike,'LOSS',-cfg.stop_points-.50)
        if b.high>=target:return c.Trade(e.day,e.signal_time,et,e.direction,e.strike,'WIN',cfg.target_points-.50)
    return c.Trade(e.day,e.signal_time,et,e.direction,e.strike,'TIMEOUT',future[-1].close-entry-.50)


if __name__=='__main__':
    c.simulate=simulate_next_open
    c.main()
