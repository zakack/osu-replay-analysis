"""Fast-jump rushing: how often the cursor reaches its closest point >=15 ms before the beat,
and how often that ends as a late-press miss, by turn, next jump, speed, tempo and AR.

    python3 tools/analysis/rush.py you|top
"""
import csv,collections,json,math,sys
def load_geom(path, keep):
    G={}; prev=None
    with open(path,newline='') as fh:
        rows=csv.DictReader(fh)
        last={}
        for r in rows:
            if not keep(r['replay']): continue
            G[(r['replay'],round(float(r['startTime']),1))]=r
    return G
def nxt_index(G):
    by=collections.defaultdict(list)
    for (rep,t) in G: by[rep].append(t)
    nx={}
    for rep,ts in by.items():
        ts.sort()
        for a,b in zip(ts,ts[1:]): nx[(rep,a)]=(rep,b)
    return nx
def f(x):
    try: return float(x)
    except: return None
def run(clicks, geom, keep, label, onset=None):
    G=load_geom(geom,keep); NX=nxt_index(G)
    pre={}
    if onset:
        with open(onset,newline='') as fh:
            for r in csv.DictReader(fh):
                if keep(r['replay']): pre[(r['replay'],round(float(r['startTime']),1))]=(f(r['preemptMs']),f(r['rate']) or 1)
    T=collections.defaultdict(lambda:[0,0,0])  # n, rushed, latePressMiss
    with open(clicks,newline='') as fh:
        for r in csv.DictReader(fh):
            if not keep(r['replay']) or not r['closestTime']: continue
            k=(r['replay'],round(float(r['startTime']),1)); g=G.get(k)
            if not g: continue
            v=f(g['requiredVelocity'])
            if v is None or v<0.045: continue
            n=NX.get(k); gn=G.get(n) if n else None
            ang=f(g['signedAngle']); sp_next=f(gn['spacingRadii']) if gn else None
            rushed=float(r['closestTime'])-float(r['startTime'])<=-15
            lpm=r['result']=='Miss' and bool(r['strayPressTime'])
            turn='acute<60' if ang is not None and abs(math.degrees(ang))<60 else ('mid60-120' if ang is not None and abs(math.degrees(ang))<120 else ('wide>120' if ang is not None else 'none'))
            nl='next long>=4r' if sp_next and sp_next>=4 else ('next short' if sp_next else 'next none')
            vb='v .045-.065' if v<0.065 else 'v >=.065'
            dt=f(g['deltaTime']); bpm='1/2 bpm>=200' if dt and dt<150 else '1/2 bpm<200'
            keys=[('cond','rushed' if rushed else 'on time'),('cond×tempo',('rushed ' if rushed else 'on time ')+bpm),('cond×vel',('rushed ' if rushed else 'on time ')+vb),('turn',turn),('next',nl),('vel',vb),('tempo',bpm),('vel×turn',vb+' '+turn)]
            if pre:
                p=pre.get(k)
                if p and p[0]: ar='AR>=9.7' if p[0]/p[1]<=500 else 'AR<9.7'; keys.append(('ar',ar))
            for kk in keys:
                c=T[kk]; c[0]+=1; c[1]+=rushed; c[2]+=lpm
    for kk in sorted(T):
        n,ru,lp=T[kk]
        if n>=300: print(f'{label:3} {kk[0]:9} {kk[1]:28} n={n:7} rushed {100*ru/n:5.1f}%  late-press miss {100*lp/n:5.2f}%')
pm={e['Path'].split('/')[-1] for e in json.load(open('build/corpus.json'))}
which=sys.argv[1]
if which=='you': run('build/clicks.csv','build/geometry.csv',lambda n:n.startswith('zaksynack'),'you','build/onset.csv')
else: run('build/clicks-reference-nm.csv','build/geometry-reference-nm.csv',lambda n:True,'top')
