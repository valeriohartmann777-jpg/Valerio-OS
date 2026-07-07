// @ts-nocheck
/* eslint-disable */
/* ── Vee dashboard tile animations ──
   Ported 1:1 from public/vee-dashboard.html (the approved mockup). Two effects:
   (1) the Vee centre — wire lights → ring colour pulse, V idle flicker, periodic
   score reveal; (2) the living orbs + rare mini pulse-bursts on every tile.
   All DOM queries are scoped to `root` (the .veeTiles element) and every timer /
   rAF is tracked so initVeeTiles can be fully torn down on unmount. */

export function initVeeTiles(
  root: HTMLElement,
  opts?: { score?: number | null; showNumber?: boolean },
): () => void {
  // The real Vitality Score drives the reveal. When there's no number to show
  // (no-routine), the score reveal loop is skipped entirely so the animation
  // never paints a stale digit over the "Set up your routine" prompt.
  const showNumber = opts?.showNumber !== false && opts?.score != null
  // Score shows out of 10 with one decimal (67 -> "6.7"). fmt10 is the single
  // source of truth for that format; the count-up renders through it too so
  // every frame is the same fixed width (no sideways jitter / right-edge clip).
  const fmt10 = (n: number) => (n / 10).toFixed(1)
  const scoreStr = showNumber ? fmt10(opts!.score!) : ''
  const timeouts = new Set<number>()
  const rafs = new Set<number>()
  const setTimeout = (fn: () => void, ms: number): number => {
    const id = window.setTimeout(() => { timeouts.delete(id); fn() }, ms)
    timeouts.add(id); return id
  }
  const clearTimeout = (id: number) => { window.clearTimeout(id); timeouts.delete(id) }
  const requestAnimationFrame = (fn: FrameRequestCallback): number => {
    const id = window.requestAnimationFrame((t) => { rafs.delete(id); fn(t) })
    rafs.add(id); return id
  }
  const cancelAnimationFrame = (id: number) => { window.cancelAnimationFrame(id); rafs.delete(id) }

  /* ── Vee tile ── */
  ;(function(){
    const vgrp=root.querySelector('.vgrp');
    const feedgrp=root.querySelector('.feedgrp');
    const feeds=[...root.querySelectorAll('.feed')];
    const ringgrp=root.querySelector('.ringgrp');
    const ringEls=[...root.querySelectorAll('.ring-soft,.ring-line')];
    const vnum=root.querySelector('.vnum');
    const vgi=root.querySelector('.vgi'), vgg=root.querySelector('.vgg');
    if(!vgrp) return;
    const rnd=(a,b)=>a+Math.random()*(b-a);
    const pick=a=>a[Math.floor(Math.random()*a.length)];
    const COLORS=['#6EE7B7','#A7F3D0','#b9a3ff','#e8c878','#E8964A','#7fd5e8'];

    let ringAnim=null, lastWire=-1, vAnim=null, vnumRAF=0, sAnims=[], mode='idle', idleTimer=null, nfTimer=null;

    const MINT='#6EE7B7';
    const vpaths=[...vgrp.querySelectorAll('path')];
    let tintAnims=[];
    function ringPulse(color){
      ringEls.forEach(e=>e.style.stroke=color);
      if(ringAnim){ try{ringAnim.cancel();}catch(e){} }
      ringAnim=ringgrp.animate([{opacity:0},{opacity:1,offset:.22},{opacity:.8,offset:.42},{opacity:0}],{duration:1000,easing:'ease-out',fill:'forwards'});
      tintAnims.forEach(a=>{try{a.cancel();}catch(e){}}); tintAnims=[];
      vpaths.forEach(p=>tintAnims.push(p.animate([{stroke:color},{stroke:color,offset:.5},{stroke:MINT}],{duration:1000,easing:'ease-out',fill:'forwards'})));
      if(vnum) tintAnims.push(vnum.animate([{fill:color},{fill:color,offset:.5},{fill:MINT}],{duration:1000,easing:'ease-out',fill:'forwards'}));
    }
    function sendLight(){
      let w; do{ w=Math.floor(Math.random()*feeds.length); }while(w===lastWire&&feeds.length>1); lastWire=w;
      const color=pick(COLORS), speed=Math.round(rnd(2000,3600));
      const light=feeds[w]; light.style.stroke=color;
      light.animate([
        {strokeDashoffset:0,opacity:0,offset:0},
        {opacity:1,offset:0.12},
        {strokeDashoffset:-95,opacity:1,offset:0.85},
        {strokeDashoffset:-100,opacity:0,offset:1},
      ],{duration:speed,easing:'linear',fill:'forwards'});
      setTimeout(()=>ringPulse(color), Math.round(speed*0.85));
      setTimeout(sendLight, Math.round(speed*0.85)+Math.round(rnd(2600,4600)));
    }
    setTimeout(sendLight, 1200);

    function vAnimate(kf,opt){ if(vAnim){try{vAnim.cancel();}catch(e){}} vAnim=vgrp.animate(kf,opt); return vAnim; }

    function idleFlicker(){
      const type=Math.floor(rnd(0,5)); let k, dur;
      if(type===0){
        k=[{opacity:1},{opacity:+rnd(.4,.65).toFixed(2),offset:.4},{opacity:1}]; dur=rnd(170,300);
      } else if(type===1){
        k=[{opacity:1},{opacity:+rnd(.3,.55).toFixed(2),offset:.22},{opacity:+rnd(.9,1).toFixed(2),offset:.4},{opacity:+rnd(.4,.6).toFixed(2),offset:.62},{opacity:1}]; dur=rnd(380,560);
      } else if(type===2){
        k=[{opacity:1,easing:'ease-in-out'},{opacity:+rnd(.5,.72).toFixed(2),offset:.5,easing:'ease-in-out'},{opacity:1}]; dur=rnd(620,920);
      } else if(type===3){
        k=[{opacity:1},{opacity:+rnd(.2,.4).toFixed(2),offset:.15},{opacity:+rnd(.7,.9).toFixed(2),offset:.3},{opacity:+rnd(.25,.45).toFixed(2),offset:.45},{opacity:+rnd(.8,1).toFixed(2),offset:.6},{opacity:1}]; dur=rnd(300,470);
      } else {
        k=[{opacity:1},{opacity:+rnd(.08,.2).toFixed(2),offset:.35},{opacity:+rnd(.6,.85).toFixed(2),offset:.55},{opacity:1}]; dur=rnd(240,420);
      }
      vAnimate(k,{duration:Math.round(dur),easing:'linear',fill:'forwards'});
    }
    function idleLoop(){ if(mode!=='idle') return; idleFlicker(); idleTimer=setTimeout(idleLoop, Math.round(rnd(900,2600))); }

    function buildFlickerIn(){
      const k=[{opacity:0,offset:0}];
      const n1=4+Math.floor(rnd(0,3));
      for(let i=1;i<=n1;i++){ const off=(i/(n1+1))*0.5, base=0.03+(i/n1)*0.28;
        k.push({offset:+off.toFixed(3),opacity:+Math.max(0.02,base*rnd(0.5,1.05)).toFixed(3)});
        if(Math.random()<0.5) k.push({offset:+(off+0.02).toFixed(3),opacity:+rnd(0.02,0.1).toFixed(3)}); }
      k.push({offset:0.56,opacity:+rnd(0.2,0.45).toFixed(3)});
      k.push({offset:0.70,opacity:+rnd(0.85,1).toFixed(3)});
      k.push({offset:0.78,opacity:+rnd(0.6,0.85).toFixed(3)});
      k.push({offset:1,opacity:1});
      for(let i=1;i<k.length;i++) if(k[i].offset<=k[i-1].offset) k[i].offset=Math.min(1,k[i-1].offset+0.001);
      return k;
    }
    function buildGlitchMain(){
      const k=[{offset:0,opacity:0,transform:'translateX(0px)'}];
      for(let i=0;i<6;i++){ const o=0.03+i*0.03; k.push({offset:+o.toFixed(3),opacity:+(Math.random()<.5?rnd(.05,.3):rnd(.6,1)).toFixed(2),transform:`translateX(${rnd(-3,3).toFixed(1)}px)`}); }
      k.push({offset:0.24,opacity:1,transform:'translateX(0px)'});
      k.push({offset:1,opacity:1,transform:'translateX(0px)'});
      return k;
    }
    function buildGhost(sign){
      return [
        {offset:0,opacity:0,transform:`translateX(${sign*4}px)`},
        {offset:.05,opacity:.5,transform:`translateX(${sign*4}px)`},
        {offset:.12,opacity:.25,transform:`translateX(${sign*-2}px)`},
        {offset:.20,opacity:.4,transform:`translateX(${sign*2}px)`},
        {offset:.26,opacity:0,transform:'translateX(0px)'},
        {offset:1,opacity:0,transform:'translateX(0px)'},
      ];
    }
    function revealNumber(intro){
      vnum.textContent=scoreStr;
      if(intro==='glitch'){
        sAnims.push(vnum.animate(buildGlitchMain(),{duration:800,fill:'forwards'}));
        sAnims.push(vgi.animate(buildGhost(-1),{duration:800,fill:'forwards'}));
        sAnims.push(vgg.animate(buildGhost(1),{duration:800,fill:'forwards'}));
      } else if(intro==='count'){
        sAnims.push(vnum.animate([{opacity:0},{opacity:.4,offset:.06},{opacity:.15,offset:.12},{opacity:1,offset:.26},{opacity:1}],{duration:820,fill:'forwards'}));
        // Count up past the real score then settle back onto it (overshoot feel).
        const target=Number(opts!.score), peak=target+5, dur=560, start=performance.now();
        const tick=now=>{ const p=Math.min(1,(now-start)/dur); let v;
          if(p<0.72)v=Math.round((p/0.72)*peak); else { const q=(p-0.72)/0.28; v=Math.round(peak+(target-peak)*q); }
          vnum.textContent=fmt10(v); if(p<1) vnumRAF=requestAnimationFrame(tick); else vnum.textContent=scoreStr; };
        vnumRAF=requestAnimationFrame(tick);
      } else {
        sAnims.push(vnum.animate(buildFlickerIn(),{duration:850,fill:'forwards'}));
      }
    }
    function numberLittleFlicker(){
      if(mode!=='score') return;
      sAnims.push(vnum.animate([{opacity:1},{opacity:+rnd(.45,.78).toFixed(2),offset:.35},{opacity:1,offset:.6},{opacity:1}],{duration:Math.round(rnd(180,340)),fill:'forwards'}));
      nfTimer=setTimeout(numberLittleFlicker, Math.round(rnd(650,1500)));
    }

    function runScore(){
      mode='score';
      clearTimeout(idleTimer);
      vAnimate([
        {opacity:1,offset:0},
        {opacity:.4,offset:.16},{opacity:.88,offset:.28},
        {opacity:.14,offset:.46},{opacity:.55,offset:.58},
        {opacity:.06,offset:.74},{opacity:0,offset:.88},{opacity:0,offset:1},
      ],{duration:520,easing:'linear',fill:'forwards'});
      setTimeout(()=>revealNumber(pick(['flicker','count','glitch'])), 540);
      const holdMs=Math.round(rnd(3600,5600));
      nfTimer=setTimeout(numberLittleFlicker, 540+950);
      setTimeout(()=>{
        clearTimeout(nfTimer);
        if(vnumRAF){ cancelAnimationFrame(vnumRAF); vnumRAF=0; }
        sAnims.push(vnum.animate([{opacity:1,offset:0},{opacity:.3,offset:.18},{opacity:.7,offset:.3},{opacity:.1,offset:.5},{opacity:0,offset:.7},{opacity:0,offset:1}],{duration:420,fill:'forwards'}));
        setTimeout(()=>{
          sAnims.forEach(a=>{try{a.cancel();}catch(e){}}); sAnims=[]; vnum.textContent=scoreStr;
          vAnimate([{opacity:0,offset:0},{opacity:.35,offset:.2},{opacity:.12,offset:.3},{opacity:.75,offset:.55},{opacity:.45,offset:.66},{opacity:1,offset:1}],{duration:640,easing:'linear',fill:'forwards'});
          mode='idle';
          setTimeout(idleLoop, 700);
          setTimeout(runScore, Math.round(rnd(7000,12000)));
        }, Math.round(rnd(450,850)));
      }, 540+950+holdMs);
    }

    idleLoop();
    // Only run the periodic score reveal when there's a real number to show.
    if(showNumber) setTimeout(runScore, 8000);
  })();

  /* ── living orbs + mini pulse-bursts ── */
  ;(function(){
    const R=Math.random, rnd=(a,b)=>a+R()*(b-a), pick=a=>a[(R()*a.length)|0], TWO=Math.PI*2;
    const NS='http://www.w3.org/2000/svg';

    const ring=(x,y)=>({cls:'pop',d:rnd(640,760),v:'--s0:.35;--s1:2.6;--o0:.9',
      svg:`<circle cx="${x}" cy="${y}" r="4" fill="none" stroke="currentColor" stroke-width="1.4"/>`});
    const ripple=(x,y)=>({cls:'pop',d:rnd(780,920),v:'--s0:.4;--s1:2.3;--o0:.8',
      svg:`<circle cx="${x}" cy="${y}" r="3" fill="none" stroke="currentColor" stroke-width="1"/><circle cx="${x}" cy="${y}" r="6.5" fill="none" stroke="currentColor" stroke-width=".7"/>`});
    const bloom=(x,y)=>({cls:'pop',d:rnd(620,760),v:'--s0:.3;--s1:2.0;--o0:.5',
      svg:`<circle cx="${x}" cy="${y}" r="5" fill="currentColor"/>`});
    const rays=(x,y)=>{let s='';for(let i=0;i<8;i++){const a=i/8*TWO,c=Math.cos(a),si=Math.sin(a);
      s+=`<line x1="${(x+c*5).toFixed(1)}" y1="${(y+si*5).toFixed(1)}" x2="${(x+c*10).toFixed(1)}" y2="${(y+si*10).toFixed(1)}" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>`;}
      return{cls:'pop',d:rnd(560,680),v:'--s0:.4;--s1:1.5;--o0:1',svg:s};};
    const particles=(x,y)=>{let s='';const n=7;for(let i=0;i<n;i++){const a=i/n*TWO+rnd(-.2,.2),r=rnd(7,10);
      s+=`<circle cx="${(x+Math.cos(a)*r).toFixed(1)}" cy="${(y+Math.sin(a)*r).toFixed(1)}" r="1.3" fill="currentColor"/>`;}
      return{cls:'pop',d:rnd(640,780),v:'--s0:.2;--s1:1.5;--o0:1',svg:s};};
    const sparkles=(x,y)=>{let s='';for(let i=0;i<4;i++){const a=rnd(0,TWO),r=rnd(4,9),px=x+Math.cos(a)*r,py=y+Math.sin(a)*r,d=1.6;
      s+=`<line x1="${(px-d).toFixed(1)}" y1="${py.toFixed(1)}" x2="${(px+d).toFixed(1)}" y2="${py.toFixed(1)}" stroke="currentColor" stroke-width="1" stroke-linecap="round"/><line x1="${px.toFixed(1)}" y1="${(py-d).toFixed(1)}" x2="${px.toFixed(1)}" y2="${(py+d).toFixed(1)}" stroke="currentColor" stroke-width="1" stroke-linecap="round"/>`;}
      return{cls:'tw',d:rnd(560,700),v:'',svg:s};};
    const orbit=(x,y)=>({cls:'orbit',d:rnd(820,1000),v:'',
      svg:`<circle cx="${(x+8).toFixed(1)}" cy="${y}" r="1.7" fill="currentColor"/>`});
    const spiral=(x,y)=>{let s='';const n=6;for(let i=0;i<n;i++){const t=i/n,a=t*TWO,r=2+t*8;
      s+=`<circle cx="${(x+Math.cos(a)*r).toFixed(1)}" cy="${(y+Math.sin(a)*r).toFixed(1)}" r="${(1.5-t).toFixed(2)}" fill="currentColor"/>`;}
      return{cls:'spiral',d:rnd(760,900),v:'',svg:s};};
    const confetti=(x,y)=>{let s='';const n=6;for(let i=0;i<n;i++){const a=i/n*TWO,r=rnd(6,9),px=x+Math.cos(a)*r,py=y+Math.sin(a)*r,rot=(R()*90)|0;
      s+=`<rect x="${(px-1).toFixed(1)}" y="${(py-1.6).toFixed(1)}" width="2" height="3.2" rx=".5" fill="currentColor" transform="rotate(${rot} ${px.toFixed(1)} ${py.toFixed(1)})"/>`;}
      return{cls:'conf',d:rnd(660,800),v:'',svg:s};};
    const ponder=(x,y)=>({cls:'ponder',d:rnd(820,980),v:'',
      svg:`<circle cx="${x}" cy="${y}" r="2" fill="currentColor"/>`});
    const flare=(x,y)=>{let s='';const L=[11,7,11,7,11,7,11,7];for(let i=0;i<8;i++){const a=i/8*TWO,c=Math.cos(a),si=Math.sin(a);
      s+=`<line x1="${(x+c*3).toFixed(1)}" y1="${(y+si*3).toFixed(1)}" x2="${(x+c*L[i]).toFixed(1)}" y2="${(y+si*L[i]).toFixed(1)}" stroke="currentColor" stroke-width="1" stroke-linecap="round"/>`;}
      return{cls:'pop',d:rnd(560,700),v:'--s0:.4;--s1:1.45;--o0:1',svg:s};};
    const dust=(x,y)=>{let s='';for(let i=0;i<11;i++){const a=R()*TWO,r=rnd(5,13);
      s+=`<circle cx="${(x+Math.cos(a)*r).toFixed(1)}" cy="${(y+Math.sin(a)*r).toFixed(1)}" r="${rnd(.6,1.1).toFixed(2)}" fill="currentColor"/>`;}
      return{cls:'pop',d:rnd(780,980),v:'--s0:.25;--s1:1.4;--o0:.85',svg:s};};
    const shoot=(x,y)=>{const a=R()*TWO,c=Math.cos(a),si=Math.sin(a);
      return{cls:'pop',d:rnd(420,560),v:'--s0:.4;--s1:2.2;--o0:1',
      svg:`<line x1="${(x+c*2).toFixed(1)}" y1="${(y+si*2).toFixed(1)}" x2="${(x+c*9).toFixed(1)}" y2="${(y+si*9).toFixed(1)}" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>`};};
    const petals=(x,y)=>{let s='';const n=5;for(let i=0;i<n;i++){const a=i/n*TWO,r=6,px=x+Math.cos(a)*r,py=y+Math.sin(a)*r,rot=(a*180/Math.PI).toFixed(0);
      s+=`<ellipse cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" rx="1.4" ry="3" fill="currentColor" transform="rotate(${rot} ${px.toFixed(1)} ${py.toFixed(1)})"/>`;}
      return{cls:'pop',d:rnd(640,800),v:'--s0:.3;--s1:1.5;--o0:.9',svg:s};};
    const cross=(x,y)=>({cls:'tw',d:rnd(520,660),v:'',
      svg:`<line x1="${(x-5).toFixed(1)}" y1="${y}" x2="${(x+5).toFixed(1)}" y2="${y}" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/><line x1="${x}" y1="${(y-5).toFixed(1)}" x2="${x}" y2="${(y+5).toFixed(1)}" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/><circle cx="${x}" cy="${y}" r="1.3" fill="currentColor"/>`});
    const halo=(x,y)=>({cls:'pop',d:rnd(720,880),v:'--s0:.6;--s1:2.4;--o0:.7',
      svg:`<circle cx="${x}" cy="${y}" r="5" fill="none" stroke="currentColor" stroke-width=".6"/>`});
    const BURSTS=[ring,ripple,bloom,rays,particles,sparkles,orbit,spiral,confetti,ponder,flare,dust,shoot,petals,cross,halo];

    function fire(svg,x,y,color){
      const b=pick(BURSTS)(x,y);
      const g=document.createElementNS(NS,'g');
      g.setAttribute('class','burst '+b.cls);
      g.style.cssText=`color:${color};transform-box:view-box;transform-origin:${x.toFixed(1)}px ${y.toFixed(1)}px;--d:${Math.round(b.d)}ms;${b.v}`;
      g.innerHTML=b.svg;
      svg.appendChild(g);
      setTimeout(()=>g.remove(), b.d+80);
    }

    const ease=x=>x<.5?2*x*x:1-Math.pow(-2*x+2,2)/2;
    const outBack=x=>{const c=1.9;return 1+(c+1)*Math.pow(x-1,3)+c*Math.pow(x-1,2);};
    const place=(o,x,y,sc)=>o.g.setAttribute('transform',`translate(${x.toFixed(2)} ${y.toFixed(2)}) scale(${sc.toFixed(3)})`);

    const orbs=[];
    root.querySelectorAll('.tile[data-orb]').forEach(tile=>{
      const svg=tile.querySelector('.art'), g=tile.querySelector('.orb'), node=tile.querySelector('.node');
      if(!svg||!g) return;
      const color=tile.classList.contains('fin')?'rgba(236,184,124,.95)':'rgba(178,245,214,.95)';
      const o={svg,g,node,color,mode:tile.dataset.orb,pulse:0,burstNext:rnd(3000,16000),
               vis:1,mDim:1,flState:'on',flT:rnd(4000,15000),flP:0,flDur:0};
      if(o.mode==='wander'){
        o.paths=[...tile.querySelectorAll('.mot, .motd')]; o.pi=0; o.path=o.paths[0]; o.len=o.path.getTotalLength();
        o.s=R()*o.len; o.from=o.s; o.to=o.s; o.t=1; o.dur=1; o.hold=rnd(800,3000); o.energy=false;
        o.swapping=false; o.swapNext=o.paths.length>1?rnd(6000,13000):Infinity;
        const p=o.path.getPointAtLength(o.s); place(o,p.x,p.y,1);
      } else if(o.mode==='hop'){
        o.pts=[[190,50],[252,44],[314,36],[376,26]]; o.i=(R()*4)|0; o.phase='idle';
        o.dwell=rnd(1200,3200); o.q=0; o.x=o.pts[o.i][0]; o.y=o.pts[o.i][1]; place(o,o.x,o.y,1);
      } else {
        const p=(tile.dataset.pt||'0,0').split(',').map(Number); o.x=p[0]; o.y=p[1]; o.hx=p[0]; o.hy=p[1];
        o.roam=tile.dataset.roam||null; o.rs='rest'; o.rT=rnd(7000,16000); o.q=0; o.legDur=0; o.glitch=0;
        if(o.roam==='spoke'){ o.tips=[...tile.querySelectorAll('.motd')].map(l=>[+l.getAttribute('x2'),+l.getAttribute('y2')]); o.legs=0; o.tip=p; }
        else if(o.roam==='ring'){ const c=tile.querySelector('.mot'); o.cx=+c.getAttribute('cx'); o.cy=+c.getAttribute('cy'); o.cr=+c.getAttribute('r'); }
        place(o,o.x,o.y,1);
      }
      orbs.push(o);
    });
    if(!orbs.length) return;

    let last=null;
    function frame(now){
      if(last==null) last=now;
      const dt=Math.min(now-last,48); last=now;
      for(const o of orbs){
        o.mDim=1;
        o.burstNext-=dt;
        if(o.burstNext<=0){
          o.burstNext=rnd(9000,20000);
          if(o.flState==='on'){
            let cx,cy;
            if(o.mode==='wander'){const p=o.path.getPointAtLength(o.s);cx=p.x;cy=p.y;} else {cx=o.x;cy=o.y;}
            fire(o.svg,cx,cy,o.color); o.pulse=1;
          }
        }
        if(o.pulse>0) o.pulse=Math.max(0,o.pulse-dt/520);
        const pb=o.pulse*o.pulse*(3-2*o.pulse), sc=1+pb*0.28;

        if(o.mode==='wander'){
          if(o.swapping){
            o.swapQ+=dt/260;
            if(o.swapQ>=0.5 && !o.swapped){ o.pi=(o.pi+1)%o.paths.length; const ratio=o.s/o.len; o.path=o.paths[o.pi]; o.len=o.path.getTotalLength(); o.s=ratio*o.len; o.from=o.s; o.to=o.s; o.t=1; o.hold=rnd(900,2400); o.swapped=true; }
            if(o.swapQ<1) o.mDim=(Math.floor(o.swapQ*6)%2?0.2:0.85);
            else o.swapping=false;
            const p=o.path.getPointAtLength(o.s); place(o,p.x,p.y,sc);
          } else {
            if(o.swapNext!==Infinity){ o.swapNext-=dt; if(o.swapNext<=0){ o.swapNext=rnd(8000,16000); o.swapping=true; o.swapped=false; o.swapQ=0; } }
            if(o.hold>0) o.hold-=dt;
            else if(o.t<1){ o.t=Math.min(1,o.t+dt/o.dur); o.s=o.from+(o.to-o.from)*ease(o.t); if(o.t>=1) o.hold=o.energy?rnd(800,2000):(R()<0.25?rnd(5000,9000):rnd(1800,5500)); }
            else {
              o.from=o.s; const energy=R()<0.30; let nt;
              if(energy){ nt=R()*o.len; }
              else { let delta=rnd(8,30); if(delta>o.len*0.8) delta=o.len*0.8; if(R()<0.5) delta=-delta; nt=o.s+delta; if(nt<0) nt=-nt; if(nt>o.len) nt=2*o.len-nt; nt=Math.max(0,Math.min(o.len,nt)); }
              o.to=nt; o.energy=energy; const dist=Math.abs(nt-o.from);
              o.dur=energy?(650+dist*3.2):(1200+dist*12+rnd(500,1700)); o.t=0;
            }
            const p=o.path.getPointAtLength(o.s); place(o,p.x,p.y,sc);
          }
        } else if(o.mode==='hop'){
          if(o.phase==='idle'){
            o.dwell-=dt; if(o.dwell<=0){o.phase='out';o.q=0;}
            place(o,o.x,o.y,sc);
          } else if(o.phase==='out'){
            o.q+=dt/150;
            if(o.q>=1){ let ni;do{ni=(R()*o.pts.length)|0;}while(ni===o.i); o.i=ni; o.x=o.pts[ni][0]; o.y=o.pts[ni][1]; o.phase='in'; o.q=0; }
            else { o.mDim=(Math.floor(o.q*5)%2?0.15:0.85); place(o,o.x,o.y,1-o.q*0.55); }
          } else {
            o.q+=dt/340;
            if(o.q>=1){ o.phase='idle'; o.dwell=rnd(1300,3600); place(o,o.x,o.y,sc); }
            else { const yb=-Math.abs(Math.sin(o.q*Math.PI*2))*(1-o.q)*5; place(o,o.x,o.y+yb,0.45+outBack(o.q)*0.65); }
          }
        } else {
          if(o.roam==='spoke'){
            if(o.rs==='rest'){ o.rT-=dt; o.x=o.hx; o.y=o.hy; if(o.rT<=0){ o.legs=R()<0.35?2:1; o.tip=o.tips[(R()*o.tips.length)|0]; o.rs='out'; o.q=0; o.legDur=rnd(700,1100); } }
            else if(o.rs==='out'){ o.q+=dt/o.legDur; const e=ease(Math.min(1,o.q)); o.x=o.hx+(o.tip[0]-o.hx)*e; o.y=o.hy+(o.tip[1]-o.hy)*e; if(o.q>=1){ o.rs='tip'; o.rT=rnd(180,560); } }
            else if(o.rs==='tip'){ o.rT-=dt; o.x=o.tip[0]; o.y=o.tip[1]; if(o.rT<=0){ o.rs='back'; o.q=0; o.legDur=rnd(700,1100); } }
            else { o.q+=dt/o.legDur; const e=ease(Math.min(1,o.q)); o.x=o.tip[0]+(o.hx-o.tip[0])*e; o.y=o.tip[1]+(o.hy-o.tip[1])*e; if(o.q>=1){ o.x=o.hx; o.y=o.hy; o.legs--; if(o.legs>0){ o.tip=o.tips[(R()*o.tips.length)|0]; o.rs='out'; o.q=0; o.legDur=rnd(700,1100); } else { o.rs='rest'; o.rT=rnd(18000,34000); } } }
          } else if(o.roam==='ring'){
            if(o.rs==='rest'){ o.rT-=dt; o.x=o.hx; o.y=o.hy; if(o.rT<=0){ o.a0=R()*TWO; o.a1=o.a0+(R()<0.5?-1:1)*rnd(Math.PI*0.8,Math.PI*2.2); o.rs='orbit'; o.q=0; o.legDur=rnd(1700,3400); o.glitch=rnd(140,220); } }
            else { o.q+=dt/o.legDur; const a=o.a0+(o.a1-o.a0)*ease(Math.min(1,o.q)); o.x=o.cx+Math.cos(a)*o.cr; o.y=o.cy+Math.sin(a)*o.cr; if(o.q>=1){ o.rs='rest'; o.rT=rnd(14000,28000); o.x=o.hx; o.y=o.hy; o.glitch=rnd(140,220); } }
            if(o.glitch>0){ o.glitch-=dt; o.mDim=Math.min(o.mDim, (Math.floor(o.glitch/45)%2?0.2:0.85)); }
          }
          place(o,o.x,o.y,sc);
        }

        o.flT-=dt;
        if(o.flState==='on'){ o.vis=1; if(o.flT<=0){ o.flState='out'; o.flP=0; o.flDur=rnd(420,680); } }
        else if(o.flState==='out'){ o.flP+=dt/o.flDur; o.vis=o.flP>=1?0:Math.max(0,1-o.flP)*(R()<0.5?1:0.22);
          if(o.flP>=1){ o.flState='off'; o.vis=0; o.flT=rnd(2000,15000);
            if(o.mode==='wander'){ o.s=R()*o.len; o.from=o.s; o.to=o.s; o.t=1; o.hold=rnd(400,1400); } } }
        else if(o.flState==='off'){ o.vis=0; if(o.flT<=0){ o.flState='in'; o.flP=0; o.flDur=rnd(420,680); } }
        else { o.flP+=dt/o.flDur; o.vis=o.flP>=1?1:Math.min(1,o.flP)*(R()<0.5?1:0.32); if(o.flP>=1){ o.flState='on'; o.vis=1; o.flT=rnd(6000,18000); } }

        o.g.style.opacity=(o.vis*o.mDim).toFixed(3);
        if(o.node) o.node.style.opacity=(0.85+pb*0.15).toFixed(3);
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  })();

  return () => {
    timeouts.forEach(id => window.clearTimeout(id))
    rafs.forEach(id => window.cancelAnimationFrame(id))
    timeouts.clear(); rafs.clear()
  }
}
