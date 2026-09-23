# Layout check for the ALE board. Usage: [SNAPSHOT=f.json] [ROWSEL=css] python3 check_layout.py check [page.html|url]
#   exits 1 on page overflow, unhandled overflow, or sibling collisions at 390..1600 px.
#   python3 check_layout.py shots  re-renders the mockup PNGs next to this file.
import sys, json
from playwright.sync_api import sync_playwright
import os
if os.environ.get('CODEX_SANDBOX'):
    print('CHECK_LAYOUT_SKIPPED: Chrome cannot run inside the Codex sandbox'); sys.exit(3)
PAGE=sys.argv[2] if len(sys.argv)>2 else os.path.join(os.path.dirname(os.path.abspath(__file__)),'board-v2-mock.html')
URL=PAGE if PAGE.startswith(('http','file:')) else 'file://'+os.path.abspath(PAGE)
OUT=os.path.dirname(os.path.abspath(__file__))
CHECK=r"""
(ROWSEL) => {
 const bad=[];
 const doc=document.documentElement;
 if (doc.scrollWidth>doc.clientWidth) bad.push('page h-overflow '+doc.scrollWidth+'>'+doc.clientWidth);
 const leaf=[...document.querySelectorAll(ROWSEL)].filter(e=>e.offsetParent!==null);
 for (const e of document.querySelectorAll('body *')) {
   if (e.offsetParent===null || e.closest('svg')) continue;
   const cs=getComputedStyle(e);
   if (e.scrollWidth>e.clientWidth+1 && cs.overflowX!=='auto' && cs.overflowX!=='scroll' && cs.textOverflow!=='ellipsis' && e.clientWidth>0 && cs.display!=='inline') bad.push('overflow '+e.className+' '+e.scrollWidth+'>'+e.clientWidth);
 }
 // sibling intersection within same parent
 const parents=new Set(leaf.map(e=>e.parentElement));
 for (const p of parents){
   const kids=[...p.children].filter(e=>e.offsetParent!==null && getComputedStyle(e).position!=='absolute');
   for(let i=0;i<kids.length;i++)for(let j=i+1;j<kids.length;j++){
     const a=kids[i].getBoundingClientRect(),b=kids[j].getBoundingClientRect();
     const ix=Math.min(a.right,b.right)-Math.max(a.left,b.left), iy=Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top);
     if(ix>0.5&&iy>0.5) bad.push('collide '+p.className+': '+kids[i].className+' x '+kids[j].className);
   }
 }
 // state word truncated for known labels?
 const trunc=[...document.querySelectorAll('.strip .state .w')].filter(w=>w.scrollWidth>w.clientWidth+1).map(w=>w.textContent);
 return {bad, trunc};
}
"""
MEASURE=r"""
() => {
 const labels=['Needs your answer','Failed to start','Out of attempts','Rejected','Failed','No heartbeat','Running','Verifying','Waiting on fix','Ready','Retrying','Waiting','Done','Superseded','Canceled','Superseded (parent accepted)'];
 const host=document.querySelector('.strip .state'); const out={}; if(!host) return {skipped:'no .strip .state in page'};
 for(const l of labels){const c=host.cloneNode(true);c.style.position='absolute';c.style.width='auto';c.querySelector('.w').textContent=l;document.body.appendChild(c);out[l]=c.getBoundingClientRect().width;c.remove()}
 const root=parseFloat(getComputedStyle(document.documentElement).fontSize);
 return {root,out};
}
"""
shots=[('desktop-light',1440,1000,'light',''),('desktop-dark',1440,1000,'dark',''),('desktop-light-drawer',1440,1000,'light','#sel=T20'),
       ('tablet-light-drawer',1024,900,'light','#sel=T20'),('mobile-light',390,844,'light',''),('mobile-dark',390,844,'dark',''),('mobile-dark-sheet',390,844,'dark','#sel=T20')]
mode=sys.argv[1] if len(sys.argv)>1 else 'check'
# Optional env: SNAPSHOT=path.json calls the page's applySnapshot(json) (ale/board.html has it);
# ROWSEL=css selects the elements whose siblings must not intersect (default: the mockup's classes).
SNAP=json.load(open(os.environ['SNAPSHOT'])) if os.environ.get('SNAPSHOT') else None
ROWSEL=os.environ.get('ROWSEL','.strip > *, .card .top > *, .hdr-top > *, .verdict > *, .legend > span, .totals > span, .toolbar > *, .d-top > *')
FAIL=[]
with sync_playwright() as p:
  b=p.chromium.launch(executable_path=os.environ.get('PW_CHROME') or None)
  if mode=='shots':
    for name,w,h,cs,hsh in shots:
      pg=b.new_page(viewport={'width':w,'height':h},color_scheme=cs,device_scale_factor=2 if w<500 else 1)
      pg.goto(URL+hsh); pg.wait_for_timeout(150)
      if hsh=='':
        H=pg.evaluate('document.documentElement.scrollHeight'); pg.set_viewport_size({'width':w,'height':H}); pg.wait_for_timeout(100)
      pg.screenshot(path=os.path.join(OUT,f'board-v2-{name}.png'))
      pg.close()
  else:
    pg=b.new_page(viewport={'width':1440,'height':900})
    pg.goto(URL); print(json.dumps(pg.evaluate(MEASURE)))
    hashes=['','#stress','#sel=T20','#sel=T20&stress'] if SNAP is None else ['']
    for hsh in hashes:
      for w in [390,480,600,768,900,1024,1280,1440,1600]:
        pg.set_viewport_size({'width':w,'height':900}); pg.goto('about:blank'); pg.goto(URL+hsh)
        if SNAP is not None: pg.evaluate('s=>applySnapshot(s)', SNAP)
        pg.wait_for_timeout(50)
        r=pg.evaluate(CHECK, ROWSEL); print(hsh or 'base',w,'OK' if not r['bad'] else r['bad'][:6], 'trunc:',r['trunc']); FAIL.extend(r['bad'])
  b.close()
sys.exit(1 if FAIL else 0)
