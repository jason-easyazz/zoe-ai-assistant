/* ═══════════════════════════════════════════════════════════════════════════
   Orbit — Nebula Canvas Renderer  v4
   You are flying through the night. People are the light.
   ═══════════════════════════════════════════════════════════════════════════ */

const SolarSystem = (function () {
  'use strict';

  // ── Intent colours ─────────────────────────────────────────────────────────
  const INTENT_COLOR = {
    social:   '#34d399',
    activity: '#fbbf24',
    romantic: '#f472b6',
  };
  function intentColor(intent) {
    return INTENT_COLOR[intent] || '#a78bfa';
  }

  // ── State ──────────────────────────────────────────────────────────────────
  let canvas, ctx, W, H;
  let T = 0;
  let orbs = [];
  let connections = [];
  let sparks = [];
  let ripples = [];
  let bursts = [];
  let timedArcs = [];    // speed dating active pairs
  let nebLayers = [];    // parallax nebula blob layers
  let stars = [];
  let ambComets = [];
  let rafId = null, lastTs = 0;

  let statTotal = 0, statMeets = 0, statConns = 0;
  let chemPct = 0, prevChemPct = 0, shimX = 0;
  let challengeActive = false;
  let challengeLeaderboard = [];

  // ── Helpers ─────────────────────────────────────────────────────────────────
  function rgba(hex, a) {
    const r = parseInt(hex.slice(1,3),16), g = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
    return `rgba(${r},${g},${b},${+a.toFixed(3)})`;
  }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function rand(min, max) { return min + Math.random() * (max - min); }
  function findOrb(id) { return orbs.find(o => o.id === id); }

  // ── Resize ──────────────────────────────────────────────────────────────────
  function resize() {
    W = canvas.width  = canvas.offsetWidth  || 800;
    H = canvas.height = canvas.offsetHeight || 600;
    buildNebula();
    buildStars();
    buildAmbComets();
  }

  // ── Nebula layers ───────────────────────────────────────────────────────────
  function buildNebula() {
    nebLayers = [
      // layer: {x,y} as fraction, rx,ry radius fractions, color, alpha, dx,dy drift per frame, angle
      { x:0.15, y:0.25, rx:0.38, ry:0.28, col:'#1a0533', a:0.50, dx:0.00008, dy:0.00004 },
      { x:0.80, y:0.60, rx:0.32, ry:0.24, col:'#061a12', a:0.45, dx:-0.00006, dy:0.00006 },
      { x:0.55, y:0.15, rx:0.26, ry:0.20, col:'#0a0a2e', a:0.40, dx:0.00005, dy:0.00007 },
      { x:0.25, y:0.80, rx:0.30, ry:0.22, col:'#1a0010', a:0.38, dx:-0.00005, dy:-0.00004 },
      { x:0.70, y:0.35, rx:0.22, ry:0.18, col:'#002233', a:0.35, dx:0.00007, dy:-0.00005 },
    ];
  }

  function drawNebula(dt) {
    nebLayers.forEach(n => {
      n.x = (n.x + n.dx * dt * 60 + 1) % 1;
      n.y = (n.y + n.dy * dt * 60 + 1) % 1;
      const x = n.x * W, y = n.y * H;
      const rx = n.rx * Math.min(W, H), ry = n.ry * Math.min(W, H);
      const gr = ctx.createRadialGradient(x, y, 0, x, y, rx);
      gr.addColorStop(0,   rgba(n.col, n.a));
      gr.addColorStop(0.5, rgba(n.col, n.a * 0.35));
      gr.addColorStop(1,   rgba(n.col, 0));
      ctx.save();
      ctx.fillStyle = gr;
      ctx.beginPath();
      ctx.ellipse(x, y, rx, ry, Math.PI * 0.25, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    });
  }

  // ── Stars ───────────────────────────────────────────────────────────────────
  function buildStars() {
    stars = Array.from({ length: 220 }, () => ({
      x: Math.random() * W, y: Math.random() * H,
      r: rand(0.2, 1.6),
      a: rand(0.1, 0.7),
      t: rand(0, Math.PI * 2),
      s: rand(0.4, 2.0),
    }));
  }

  function drawStars(dt) {
    ctx.save();
    stars.forEach(s => {
      s.t += s.s * dt;
      ctx.globalAlpha = s.a * (0.5 + 0.5 * Math.sin(s.t));
      ctx.fillStyle = '#fff';
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.globalAlpha = 1;
    ctx.restore();
  }

  // ── Ambient comets ──────────────────────────────────────────────────────────
  class AmbComet {
    constructor(immediate) {
      this.reset(immediate);
    }
    reset(immediate) {
      const edge = Math.floor(Math.random() * 4);
      switch (edge) {
        case 0: this.x=-20; this.y=rand(0,H); break;
        case 1: this.x=W+20; this.y=rand(0,H); break;
        case 2: this.x=rand(0,W); this.y=-20; break;
        default: this.x=rand(0,W); this.y=H+20;
      }
      const tx=rand(W*0.25,W*0.75), ty=rand(H*0.25,H*0.75);
      const d=Math.hypot(tx-this.x,ty-this.y), spd=rand(0.9,2.2);
      this.vx=(tx-this.x)/d*spd; this.vy=(ty-this.y)/d*spd;
      this.len=rand(10,20); this.a=rand(0.04,0.10);
      this.col=['#a78bfa','#34d399','#fbbf24','#f472b6','#7dd3fc'][Math.floor(Math.random()*5)];
      this.trail=[];
      if (immediate) { this.x=rand(0,W); this.y=rand(0,H); }
    }
    update(dt) {
      this.trail.push({x:this.x,y:this.y});
      if (this.trail.length>this.len) this.trail.shift();
      this.x+=this.vx*dt*60; this.y+=this.vy*dt*60;
      if (this.x<-60||this.x>W+60||this.y<-60||this.y>H+60) this.reset(false);
    }
    draw() {
      if (this.trail.length<2) return;
      ctx.save();
      for (let i=1;i<this.trail.length;i++) {
        const p=i/this.trail.length;
        ctx.globalAlpha=p*this.a; ctx.strokeStyle=this.col;
        ctx.lineWidth=p*1.8; ctx.shadowColor=this.col; ctx.shadowBlur=4;
        ctx.beginPath();
        ctx.moveTo(this.trail[i-1].x,this.trail[i-1].y);
        ctx.lineTo(this.trail[i].x,this.trail[i].y);
        ctx.stroke();
      }
      ctx.restore();
    }
  }

  function buildAmbComets() {
    ambComets = Array.from({length:7}, (_,i) => {
      const c = new AmbComet(true);
      for (let j=0;j<i*35;j++) c.update(1/60);
      return c;
    });
  }

  // ── Orbs (people) ───────────────────────────────────────────────────────────
  class Orb {
    constructor(id, name, intent, quick) {
      this.id    = id;
      this.name  = (name||'?').split(' ')[0].substring(0,12);
      this.intent = intent||'social';
      this.col   = intentColor(this.intent);
      // Random position across canvas, avoiding edges
      this.x     = rand(W*0.08, W*0.92);
      this.y     = rand(H*0.08, H*0.82);
      // Gentle drift
      this.vx    = rand(-0.15, 0.15);
      this.vy    = rand(-0.12, 0.12);
      this.r     = rand(5, 9);
      this.alpha = quick ? 1 : 0;
      this.born  = quick;
      this.glowT = 0;
      this.glowCol = null;
      // Delivery comet target
      this._tx = this.x; this._ty = this.y;
    }
    glow(col, dur=2.5) { this.glowT=dur; this.glowCol=col||this.col; }
    update(dt) {
      if (!this.born) return;
      if (this.alpha < 1) this.alpha = Math.min(1, this.alpha + dt*2);
      if (this.glowT > 0) this.glowT = Math.max(0, this.glowT - dt);
      // Drift with soft boundary bounce
      this.x += this.vx * dt * 60;
      this.y += this.vy * dt * 60;
      const pad = 40;
      if (this.x < pad)    { this.vx = Math.abs(this.vx);  }
      if (this.x > W-pad)  { this.vx = -Math.abs(this.vx); }
      if (this.y < pad)    { this.vy = Math.abs(this.vy);  }
      if (this.y > H*0.82) { this.vy = -Math.abs(this.vy); }
    }
    draw() {
      if (!this.born) return;
      const gf = this.glowT > 0 ? this.glowT/2.5 : 0;
      ctx.save();
      ctx.globalAlpha = this.alpha;
      // Wide glow halo
      const gr = ctx.createRadialGradient(this.x,this.y,0, this.x,this.y,this.r*5);
      gr.addColorStop(0, rgba(this.glowCol||this.col, 0.25+gf*0.4));
      gr.addColorStop(1, rgba(this.col, 0));
      ctx.fillStyle=gr; ctx.beginPath(); ctx.arc(this.x,this.y,this.r*5,0,Math.PI*2); ctx.fill();
      // Core
      const cg = ctx.createRadialGradient(this.x-this.r*0.3,this.y-this.r*0.3,0, this.x,this.y,this.r);
      cg.addColorStop(0,'#ffffff'); cg.addColorStop(0.4,this.col); cg.addColorStop(1,rgba(this.col,0.6));
      ctx.fillStyle=cg; ctx.shadowColor=this.col; ctx.shadowBlur=12+gf*16;
      ctx.beginPath(); ctx.arc(this.x,this.y,this.r,0,Math.PI*2); ctx.fill();
      ctx.shadowBlur=0;
      // Name
      if (this.alpha>0.5) {
        ctx.font=`500 11px Inter,sans-serif`; ctx.fillStyle=`rgba(255,255,255,${0.65*this.alpha})`;
        ctx.textAlign='center'; ctx.textBaseline='top';
        ctx.fillText(this.name, this.x, this.y+this.r+4);
      }
      ctx.restore();
    }
  }

  // ── Delivery spark (new person arriving) ────────────────────────────────────
  class DeliveryComet {
    constructor(orb) {
      this.orb=orb;
      this.tx=orb.x; this.ty=orb.y;
      const e=Math.floor(Math.random()*4);
      switch(e){case 0:this.x=rand(0,W);this.y=-20;break;case 1:this.x=W+20;this.y=rand(0,H);break;
        case 2:this.x=rand(0,W);this.y=H+20;break;default:this.x=-20;this.y=rand(0,H);}
      this.trail=[]; this.done=false; this.col=orb.col;
    }
    update(dt) {
      if (this.done) return;
      this.trail.push({x:this.x,y:this.y});
      if (this.trail.length>22) this.trail.shift();
      const dx=this.tx-this.x, dy=this.ty-this.y, dist=Math.hypot(dx,dy);
      if (dist<4) { this.done=true; this.orb.born=true; this.orb.alpha=0; ripples.push(new Ripple(this.col,this.tx,this.ty,36)); return; }
      const s=Math.min(4*dt*60,dist); this.x+=dx/dist*s; this.y+=dy/dist*s;
    }
    draw() {
      if (this.done) return;
      ctx.save();
      for (let i=0;i<this.trail.length;i++){
        const p=i/this.trail.length;
        ctx.globalAlpha=p*0.8; ctx.fillStyle=this.col;
        ctx.shadowColor=this.col; ctx.shadowBlur=8;
        ctx.beginPath(); ctx.arc(this.trail[i].x,this.trail[i].y,Math.max(0.5,p*3.5),0,Math.PI*2); ctx.fill();
      }
      ctx.globalAlpha=1; ctx.shadowBlur=14; ctx.fillStyle='#fff';
      ctx.beginPath(); ctx.arc(this.x,this.y,3,0,Math.PI*2); ctx.fill();
      ctx.restore();
    }
  }

  // ── Connection arc ──────────────────────────────────────────────────────────
  class Connection {
    constructor(a,b) { this.a=a; this.b=b; this.alpha=1; this.decay=1/(90*60); }
    update(dt) { this.alpha=Math.max(0,this.alpha-this.decay*dt*60); }
    get dead() { return this.alpha<=0; }
    draw() {
      if (!this.a.born||!this.b.born) return;
      const cpx=(this.a.x+this.b.x)/2, cpy=(this.a.y+this.b.y)/2-30;
      const gr=ctx.createLinearGradient(this.a.x,this.a.y,this.b.x,this.b.y);
      gr.addColorStop(0,rgba(this.a.col,this.alpha*0.8));
      gr.addColorStop(0.5,rgba('#ffffff',this.alpha*0.3));
      gr.addColorStop(1,rgba(this.b.col,this.alpha*0.8));
      ctx.save(); ctx.strokeStyle=gr; ctx.lineWidth=1.5;
      ctx.globalAlpha=this.alpha; ctx.shadowColor='#a78bfa'; ctx.shadowBlur=5;
      ctx.beginPath(); ctx.moveTo(this.a.x,this.a.y);
      ctx.quadraticCurveTo(cpx,cpy,this.b.x,this.b.y); ctx.stroke();
      ctx.restore();
    }
  }

  // ── Timed arc (speed dating pair) ──────────────────────────────────────────
  class TimedArc {
    constructor(a, b, endsAt) {
      this.a=a; this.b=b; this.endsAt=endsAt;
    }
    get dead() { return Date.now()/1000 > this.endsAt; }
    draw() {
      if (!this.a||!this.b||!this.a.born||!this.b.born) return;
      const frac = Math.max(0, (this.endsAt - Date.now()/1000) / 300);
      const cpx=(this.a.x+this.b.x)/2, cpy=(this.a.y+this.b.y)/2-20;
      ctx.save();
      ctx.strokeStyle=rgba('#fbbf24',0.55+0.3*Math.sin(T*2));
      ctx.lineWidth=2; ctx.setLineDash([6,5]);
      ctx.shadowColor='#fbbf24'; ctx.shadowBlur=8;
      ctx.beginPath(); ctx.moveTo(this.a.x,this.a.y);
      ctx.quadraticCurveTo(cpx,cpy,this.b.x,this.b.y); ctx.stroke();
      ctx.setLineDash([]);
      // Timer label at midpoint
      const mx=(this.a.x+this.b.x)/2, my=(this.a.y+this.b.y)/2-14;
      const secsLeft = Math.max(0, Math.ceil(this.endsAt - Date.now()/1000));
      const mins=Math.floor(secsLeft/60), secs=secsLeft%60;
      const label=`${mins}:${String(secs).padStart(2,'0')}`;
      ctx.font='700 11px "Space Grotesk",sans-serif';
      ctx.fillStyle=rgba('#fbbf24',0.9); ctx.textAlign='center'; ctx.textBaseline='middle';
      ctx.fillText(label, mx, my);
      ctx.restore();
    }
  }

  // ── Spark flash between two orbs ────────────────────────────────────────────
  class Spark {
    constructor(a, b) {
      this.a=a; this.b=b; this.life=1;
    }
    update(dt) { this.life=Math.max(0,this.life-dt*2.5); }
    get dead() { return this.life<=0; }
    draw() {
      if (!this.a.born||!this.b.born) return;
      ctx.save(); ctx.globalAlpha=this.life;
      ctx.strokeStyle=rgba('#ffffff',this.life*0.9);
      ctx.lineWidth=2; ctx.shadowColor='#fff'; ctx.shadowBlur=12;
      ctx.beginPath(); ctx.moveTo(this.a.x,this.a.y); ctx.lineTo(this.b.x,this.b.y); ctx.stroke();
      ctx.restore();
    }
  }

  // ── Ripple ──────────────────────────────────────────────────────────────────
  class Ripple {
    constructor(col,x,y,maxR) {
      this.col=col; this.x=x??W/2; this.y=y??H/2;
      this.r=0; this.maxR=maxR??Math.hypot(W,H)*0.7; this.a=0.7;
      this.spd=this.maxR/70;
    }
    update(dt) { this.r+=this.spd*dt*60; this.a-=(0.7/70)*dt*60; }
    get dead() { return this.a<=0; }
    draw() {
      ctx.save(); ctx.globalAlpha=Math.max(0,this.a);
      ctx.strokeStyle=this.col; ctx.lineWidth=2;
      ctx.shadowColor=this.col; ctx.shadowBlur=10;
      ctx.beginPath(); ctx.arc(this.x,this.y,this.r,0,Math.PI*2); ctx.stroke();
      ctx.restore();
    }
  }

  // ── Burst (particles) ───────────────────────────────────────────────────────
  class Burst {
    constructor(x,y,col) {
      this.ps=Array.from({length:22},()=>({x,y,vx:rand(-4,4),vy:rand(-4,4),r:rand(0.8,2.5),a:1}));
      this.col=col;
    }
    update(dt) { this.ps.forEach(p=>{p.x+=p.vx*dt*60;p.y+=p.vy*dt*60;p.a-=0.02*dt*60;}); }
    get dead() { return this.ps.every(p=>p.a<=0); }
    draw() {
      ctx.save(); this.ps.forEach(p=>{
        if(p.a<=0)return; ctx.globalAlpha=Math.max(0,p.a);
        ctx.fillStyle=this.col; ctx.shadowColor=this.col; ctx.shadowBlur=6;
        ctx.beginPath(); ctx.arc(p.x,p.y,p.r,0,Math.PI*2); ctx.fill();
      }); ctx.restore();
    }
  }

  // ── Chemistry bar ───────────────────────────────────────────────────────────
  const MILESTONES = [25,50,75,100];

  function computeChem() {
    if (!statTotal) return 0;
    return Math.min(100, Math.round(((statMeets*2+statConns)/statTotal)*100));
  }

  function rrect(x,y,w,h,r) {
    ctx.beginPath(); ctx.moveTo(x+r,y); ctx.lineTo(x+w-r,y);
    ctx.arcTo(x+w,y,x+w,y+r,r); ctx.lineTo(x+w,y+h-r);
    ctx.arcTo(x+w,y+h,x+w-r,y+h,r); ctx.lineTo(x+r,y+h);
    ctx.arcTo(x,y+h,x,y+h-r,r); ctx.lineTo(x,y+r);
    ctx.arcTo(x,y,x+r,y,r); ctx.closePath();
  }

  function drawChemistry() {
    const newPct = computeChem();
    MILESTONES.forEach(m=>{ if(prevChemPct<m&&newPct>=m) fireMilestone(m); });
    prevChemPct=newPct; chemPct=newPct;

    // Full-width bar at very bottom (10px above bottom edge)
    const barH=10, barY=H-barH-4, cr=barH/2;
    const fillW=W*(chemPct/100);

    // Track
    ctx.fillStyle='rgba(255,255,255,0.05)';
    rrect(0,barY,W,barH,cr); ctx.fill();

    // Fill
    if (fillW>cr*2) {
      const gr=ctx.createLinearGradient(0,0,W,0);
      gr.addColorStop(0,'#4c1d95'); gr.addColorStop(0.4,'#7c3aed');
      gr.addColorStop(0.7,'#c084fc'); gr.addColorStop(1,'#f472b6');
      ctx.save(); ctx.fillStyle=gr; ctx.shadowColor='#c084fc'; ctx.shadowBlur=14;
      rrect(0,barY,fillW,barH,cr); ctx.fill(); ctx.shadowBlur=0;
      // Shimmer
      shimX=(shimX+1.1)%(W+80);
      const sg=ctx.createLinearGradient(shimX-30,0,shimX+30,0);
      sg.addColorStop(0,'rgba(255,255,255,0)'); sg.addColorStop(0.5,'rgba(255,255,255,0.18)'); sg.addColorStop(1,'rgba(255,255,255,0)');
      ctx.fillStyle=sg; rrect(0,barY,fillW,barH,cr); ctx.fill();
      ctx.restore();
    }

    // Labels
    if (statTotal > 0) {
      ctx.save();
      ctx.font='700 10px "Space Grotesk",sans-serif';
      ctx.fillStyle='rgba(167,139,250,0.6)'; ctx.textAlign='left'; ctx.textBaseline='bottom';
      ctx.fillText('✦  CHEMISTRY', 10, barY-4);
      ctx.fillStyle='rgba(226,232,240,0.75)'; ctx.textAlign='right';
      ctx.fillText(`${chemPct}%`, W-10, barY-4);
      ctx.restore();
    }
  }

  function fireMilestone(m) {
    const x=W*(m/100), y=H-18;
    const cols=['#a78bfa','#c084fc','#f472b6','#fde68a'];
    bursts.push(new Burst(x,y,cols[MILESTONES.indexOf(m)]||'#a78bfa'));
    if (m===100) { ripples.push(new Ripple('#fde68a')); ripples.push(new Ripple('#f472b6')); }
  }

  // ── Challenge leaderboard overlay ───────────────────────────────────────────
  function drawChallengeBadge() {
    if (!challengeActive || !challengeLeaderboard.length) return;
    const lx=W-180, ly=80, lw=170, lh=30+challengeLeaderboard.length*22+10;
    ctx.save();
    ctx.fillStyle='rgba(7,7,14,0.7)';
    ctx.strokeStyle='rgba(251,191,36,0.4)';
    ctx.lineWidth=1;
    rrect(lx,ly,lw,lh,10); ctx.fill(); ctx.stroke();
    ctx.font='700 10px "Space Grotesk",sans-serif';
    ctx.fillStyle='rgba(251,191,36,0.85)'; ctx.textAlign='center';
    ctx.fillText('🎯 SCAN CHALLENGE', lx+lw/2, ly+16);
    ctx.font='500 11px Inter,sans-serif';
    ctx.textAlign='left';
    challengeLeaderboard.forEach((row, i) => {
      ctx.fillStyle='rgba(226,232,240,0.8)';
      const medal = ['🥇','🥈','🥉'][i]||'  ';
      ctx.fillText(`${medal} ${row.display_name}`, lx+10, ly+36+i*22);
      ctx.fillStyle='rgba(251,191,36,0.8)'; ctx.textAlign='right';
      ctx.fillText(`${row.points}pt`, lx+lw-10, ly+36+i*22);
      ctx.textAlign='left';
    });
    ctx.restore();
  }

  // ── Waiting prompt ──────────────────────────────────────────────────────────
  function drawWaiting() {
    if (orbs.filter(o=>o.born).length > 0) return;
    const a=0.22+0.16*Math.sin(T*0.8);
    ctx.save(); ctx.font='400 14px Inter,sans-serif';
    ctx.fillStyle=`rgba(226,232,240,${a})`; ctx.textAlign='center'; ctx.textBaseline='middle';
    ctx.fillText('Waiting for your first guest…', W/2, H*0.45);
    ctx.restore();
  }

  // ── RAF loop ─────────────────────────────────────────────────────────────────
  function loop(ts) {
    rafId = requestAnimationFrame(loop);
    const dt = Math.min((ts-lastTs)/1000, 0.05);
    lastTs=ts; T+=dt;
    if (dt<=0) return;

    // Near-opaque clear — subtle motion blur
    ctx.fillStyle='rgba(4,4,11,0.88)';
    ctx.fillRect(0,0,W,H);

    drawNebula(dt);
    drawStars(dt);
    ambComets.forEach(c=>{c.update(dt);c.draw();});

    // Connections (fading arcs)
    connections=connections.filter(c=>!c.dead);
    connections.forEach(c=>{c.update(dt);c.draw();});

    // Timed arcs
    timedArcs=timedArcs.filter(a=>!a.dead);
    timedArcs.forEach(a=>a.draw());

    // Orbs
    orbs.forEach(o=>o.update(dt));
    // Delivery comets (need to filter done ones)
    sparks=sparks.filter(s=>!s.done&&!s.dead);

    // Draw orbs
    orbs.forEach(o=>o.draw());

    // Spark flashes
    const liveSparks=sparks.filter(s=>s instanceof Spark);
    liveSparks.forEach(s=>{s.update(dt);s.draw();});
    const liveComets=sparks.filter(s=>s instanceof DeliveryComet);
    liveComets.forEach(c=>{c.update(dt);c.draw();});
    // Clean up done delivery comets
    sparks=sparks.filter(s=>!(s instanceof DeliveryComet&&s.done)&&!s.dead);

    // Ripples + bursts
    ripples=ripples.filter(r=>!r.dead);
    ripples.forEach(r=>{r.update(dt);r.draw();});
    bursts=bursts.filter(b=>!b.dead);
    bursts.forEach(b=>{b.update(dt);b.draw();});

    drawWaiting();
    drawChemistry();
    drawChallengeBadge();
  }

  // ── Public API ───────────────────────────────────────────────────────────────
  function init(cvs, attendees) {
    canvas=cvs; ctx=canvas.getContext('2d');
    orbs=[]; connections=[]; sparks=[]; ripples=[]; bursts=[]; timedArcs=[];
    resize();
    window.addEventListener('resize', resize);
    if (attendees&&attendees.length) {
      attendees.forEach(a=>orbs.push(new Orb(a.id,a.display_name,a.intent,true)));
    }
    if (rafId) cancelAnimationFrame(rafId);
    lastTs=performance.now(); rafId=requestAnimationFrame(loop);
  }

  function onEvent(type, data) {
    switch(type) {
      case 'new_checkin': {
        const o=new Orb(data.checkin_id,data.display_name,data.intent,false);
        orbs.push(o);
        const dc=new DeliveryComet(o); sparks.push(dc);
        break;
      }
      case 'meet_confirmed': {
        const a=findOrb(data.id_1), b=findOrb(data.id_2);
        if(a&&b){ a.glow('#fff'); b.glow('#fff'); connections.push(new Connection(a,b)); sparks.push(new Spark(a,b)); }
        ripples.push(new Ripple('#34d399'));
        break;
      }
      case 'speed_dating_paired': {
        const a=findOrb(data.id_1), b=findOrb(data.id_2);
        if(a&&b) timedArcs.push(new TimedArc(a,b,data.ends_at));
        break;
      }
      case 'last_orders_triggered':
        ripples.push(new Ripple('#fbbf24'));
        orbs.forEach(o=>o.glow('#fbbf24',4));
        break;
      case 'safety_report':
        ripples.push(new Ripple('#f87171'));
        break;
      case 'stats_update':
        statTotal=data.total||0; statMeets=data.confirmed_meets||0; statConns=data.connections||0;
        break;
      case 'challenge_started':
        challengeActive=true; challengeLeaderboard=[];
        orbs.forEach(o=>o.glow('#fbbf24',1.5));
        ripples.push(new Ripple('#fbbf24'));
        break;
      case 'leaderboard_update':
        challengeLeaderboard=(data.top||[]).slice(0,3);
        if(challengeLeaderboard.length){
          const top=findOrb(challengeLeaderboard[0].checkin_id);
          if(top) top.glow('#fde68a',1);
        }
        break;
      case 'challenge_ended':
        challengeActive=false;
        ripples.push(new Ripple('#fde68a'));
        ripples.push(new Ripple('#c084fc'));
        bursts.push(new Burst(W/2,H/2,'#fde68a'));
        bursts.push(new Burst(W/3,H/3,'#a78bfa'));
        bursts.push(new Burst(W*0.7,H*0.6,'#f472b6'));
        break;
      case 'speed_dating_started':
        orbs.forEach(o=>o.glow('#fbbf24',2));
        break;
      case 'speed_dating_ended':
        timedArcs=[];
        break;
      case 'attendee_left': {
        const o=findOrb(data.checkin_id);
        if(o){o.glow('#555',0.5); setTimeout(()=>{orbs=orbs.filter(x=>x!==o);},1200);}
        break;
      }
      case 'session_ended':
        orbs.forEach(o=>{o.vx*=3;o.vy*=3;setTimeout(()=>{orbs=orbs.filter(x=>x!==o);},2000);});
        break;
    }
  }

  function destroy() {
    if(rafId) cancelAnimationFrame(rafId);
    window.removeEventListener('resize',resize);
    rafId=null;
  }

  return { init, onEvent, destroy };
})();
