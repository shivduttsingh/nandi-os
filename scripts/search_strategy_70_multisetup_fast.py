from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import search_strategy_70_multisetup as m


def fast_grid():
    combos = [
        (0,0,0),(1,0,0),(0,1,0),(0,0,1),
        (1,1,0),(1,0,1),(0,1,1),(1,1,1),
    ]
    exits = ((8.0,4.0),(8.0,5.0),(10.0,5.0))
    for il,pl,rl in combos:
        for score in (0.45,0.55,0.65):
            for target,stop in exits:
                for cooldown in (5,8):
                    for quota in (2,3):
                        yield m.Config(il,pl,rl,score,target,stop,cooldown,quota)


if __name__ == '__main__':
    m.config_grid = fast_grid
    m.main()
