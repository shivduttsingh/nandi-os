from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import search_strategy_70_confluence as c


def fast_grid():
    levels = ((0,0,0),(1,0,0),(0,1,0),(1,1,0),(1,1,1))
    exits = ((8.0,4.0),(8.0,5.0),(10.0,5.0))
    for lev in levels:
        for score in (0.40,0.50,0.60):
            for aw,fw in ((3,6),(6,12)):
                for target,stop in exits:
                    yield c.Config(lev[0],lev[1],lev[2],score,aw,fw,target,stop,5)


if __name__ == '__main__':
    c.grid = fast_grid
    c.main()
