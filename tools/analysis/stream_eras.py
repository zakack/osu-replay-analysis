"""What starts strings of 50s in streams, per 10,000 stream circles, by input era (setup-timeline.csv
buttons rows). Not controlled for BPM or run length.

    python3 tools/analysis/stream_eras.py
"""
import sys,os,pickle,json,collections,bisect,re
sys.path.insert(0,'tools/analysis'); import streams as S
ERAS=[("plain, before QMK","0000","2024-11-01"),("QMK toggle (exclusive)","2024-11-01","2025-02-08"),
("plain by choice","2025-02-08","2025-05-05"),("glidepad","2025-05-05","2025-09-12"),
("glidepad + hold threshold","2025-09-12","2025-11-04"),("state-machine rewrite","2025-11-04","2026-01-03"),
("doubletap, exclusive","2026-01-03","2026-08-29"),("doubletap, analog","2026-08-29","9999")]
def era(name):
    d=re.search(r'\((\d{4}-\d\d-\d\d)_\d\d-\d\d\)',name).group(1)
    return next(i for i,(_,a,b) in enumerate(ERAS) if a<=d<b)
runs=pickle.load(open(S.CACHE,'rb')); presses=S.load_presses({r for r,_ in runs})
circ=collections.Counter(); origins=collections.defaultdict(collections.Counter); nstr=collections.Counter()
for replay,events in runs:
    if not replay.startswith('zaksynack'): continue
    ps=presses.get(replay)
    if ps is None: continue
    e=era(replay); objects=[x[0] for x in events]; beat,snap=events[0][1],events[0][2]; iv=snap*beat
    lo=bisect.bisect_left(ps,(objects[0]-iv,)); hi=bisect.bisect_right(ps,(objects[-1]+iv,"~"))
    res=S.classify(objects,[x[3] for x in events],[x[4] for x in events],ps[lo:hi],snap,beat)
    circ[e]+=len(objects)
    for s in res['strings']:
        nstr[e]+=1; origins[e][s['origin'] if s['class'] in ('pushback','ahead') else s['class']]+=1
keys=['stray','dropped','double','notelocked','cursor left early','own-early','own-late']
allk=collections.Counter()
for e in origins: allk.update(origins[e])
print('per 10,000 stream circles')
print(f"{'era':28}{'circles':>9}{'strings':>8}"+''.join(f'{k[:9]:>10}' for k in keys))
for i,(name,_,_) in enumerate(ERAS):
    c=circ[i]
    if c<5000: print(f'{name:28}{c:9}  (too few)'); continue
    print(f'{name:28}{c:9}{1e4*nstr[i]/c:8.1f}'+''.join(f'{1e4*origins[i][k]/c:10.1f}' for k in keys))
print('other origins:',{k:v for k,v in allk.items() if k not in keys})
