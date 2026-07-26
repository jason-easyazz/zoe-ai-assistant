const {chromium}=require('/home/zoe/.openclaw/npm/node_modules/playwright-core');
(async()=>{
  const b=await chromium.launch({executablePath:'/home/zoe/.cache/ms-playwright/chromium-1148/chrome-linux/chrome',args:['--no-sandbox']});
  const p=await b.newPage({viewport:{width:1280,height:720}});
  await p.goto('file://'+__dirname+'/dock-mockup.html');
  // saturate .dnm (max-width:132px) with a long title -> chip's true max width
  const r=await p.evaluate(()=>{
    window.__render('max4');
    document.querySelector('.dnm').textContent='Everything In Its Right Place (Remastered)';
    const d=document.getElementById('dock');
    const chip=document.querySelector('.dnp');
    return {dock:Math.round(d.getBoundingClientRect().width), chip:Math.round(chip.getBoundingClientRect().width)};
  });
  await p.waitForTimeout(150);
  console.log('4 pins + SATURATED now-playing chip:');
  console.log('  chip width       =',r.chip,'px  (CSS max-width:380 on .pc.dnp; .dnm capped at 132)');
  console.log('  full dock width  =',r.dock,'px of 1280   free =',1280-r.dock,'px');
  await p.screenshot({path:'shot_max4_longtitle.png'});
  await b.close();
})();
