const {chromium}=require('/home/zoe/.openclaw/npm/node_modules/playwright-core');
(async()=>{
  const b=await chromium.launch({executablePath:'/home/zoe/.cache/ms-playwright/chromium-1148/chrome-linux/chrome',args:['--no-sandbox','--force-device-scale-factor=1']});
  const p=await b.newPage({viewport:{width:1280,height:720}});
  await p.goto('file://'+__dirname+'/dock-mockup.html');
  const keys=['bedroom_music','bedroom_nomusic','temp_open','lounge','kitchen','max4','today'];
  const rows=[];
  for(const k of keys){
    await p.evaluate(k=>window.__render(k),k);
    await p.waitForTimeout(220);
    await p.screenshot({path:`shot_${k}.png`});
    rows.push(await p.evaluate(()=>{
      const d=document.getElementById('dock'),r=d.getBoundingClientRect();
      const parts=[...d.querySelectorAll('.pc')].map(el=>{
        const c=el.classList;
        const k=c.contains('apps')?'apps':c.contains('dnp')?'nowplaying':c.contains('temp')?'temp':c.contains('scene')?'scene':'light';
        const n=el.querySelector('.nm')||el.querySelector('.tl');
        return `${n?n.textContent:''}[${k}]=${Math.round(el.getBoundingClientRect().width)}`;
      });
      return {w:Math.round(r.width),h:Math.round(r.height),parts,free:1280-Math.round(r.width)};
    }));
  }
  keys.forEach((k,i)=>{const r=rows[i];
    console.log(`${k.padEnd(16)} dock=${String(r.w).padStart(4)}x${r.h}  free=${String(r.free).padStart(4)}  ${r.parts.join(' ')}`);});
  await b.close();
})();
