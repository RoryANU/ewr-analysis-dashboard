// Isolated real-browser propagation/cascade regression. NODE_PATH=<Playwright packages> node tests/test_custom_ewr_displays.cjs
const {chromium}=require('playwright'),http=require('node:http'),fs=require('node:fs'),assert=require('node:assert/strict');
const fixtures=JSON.parse(fs.readFileSync('tests/fixtures/uewr_validation_cases.json','utf8'));
(async()=>{
 const specs=new Map(),jobs=new Map(),errors=[],warnings=[];let next=0,holdAnnual=false,lateAnnual=[];
 const row=s=>({pu:s.pus[0],gauge:s.gauge,ewr:s.code,fr:.4,et:s.params['MaxInter-event']?.1:null,ey:5,ny:25,sev:1.2,dr:.8,nm:[2,4],win:{mil:{fr:.5,et:.2,sev:1.5,dr:.7,n:10}},uewr_ver:s.ver});
 const annual=s=>({code:s.code,gauge:s.gauge,pu:s.pus[0],kind:s.kind,unit:s.kind==='L'?'m':s.kind==='S'?'EC':'ML/d',thr:s.params.LevelThresholdMin??s.params.FlowThresholdMin??0,thr_max:s.params.LevelThresholdMax??s.params.FlowThresholdMax??1000000,dur:5,min_spell:5,per_year:1,gap_tol:0,sm:7,em:6,ystart:7,target:50,mrip_days:730,
  scenarios:{base:{years:{wy:[2000,2001,2002],achieved:[1,0,1],n_events:[1,0,1],n_all:[1,0,1],event_days:[5,0,5],max_event:[5,0,5],missing:[0,0,0],complete:[1,1,1],dr:[1,0,1]},events:[{wy:2000,start:'2000-10-01',end:'2000-10-05',days:5,qual:5,peak:250,vol:['L','S'].includes(s.kind)?null:1000,ok:true}],gaps:[],etp:.1,coverage:{start:'2000-07-01',end:'2003-06-30',missing_days:0,complete_years:3,years:3}}}});
 const server=http.createServer(async(req,res)=>{
  res.setHeader('Content-Type','application/json');let body='';for await(const chunk of req)body+=chunk;const data=body?JSON.parse(body):{},url=new URL(req.url,'http://test');let out={ok:true};
  if(url.pathname==='/api/uewr/schema')out=fixtures.schema;
  else if(url.pathname==='/api/uewr'&&req.method==='GET')out={specs:[...specs.values()]};
  else if(url.pathname==='/api/uewr'&&req.method==='POST'){const spec={...data,id:data.id||'u_display'+(++next),ver:(specs.get(data.id)?.ver||0)+1};specs.set(spec.id,spec);out={spec};}
  else if(url.pathname==='/api/uewr/run'){const id='job'+(++next);out={job:id};jobs.set(id,{status:'done',results:Object.fromEntries(data.scns.map(id=>[id,data.ids.map(id=>row(specs.get(id)))]))});}
  else if(url.pathname==='/api/job')out=jobs.get(url.searchParams.get('id'));
  else if(url.pathname==='/api/uewr/delete')specs.delete(url.searchParams.get('id'));
  else if(url.pathname==='/api/ewrs')out={ewrs:[],inventory:[]};
  else if(url.pathname==='/api/ewryears'){
   const spec=[...specs.values()].find(s=>s.code===url.searchParams.get('ewr'));out=spec?annual(spec):{error:'Deleted rule'};
   if(holdAnnual){lateAnnual.push(()=>res.end(JSON.stringify(out)));return;}
  }
  else if(url.pathname==='/api/interevents')out={exceed:[],mrip_days:730,n_gaps:0,n_censored:0};
  else if(url.pathname==='/api/heatmap')out={thresholds:[0,200,400],durations:[1,5,10],base:[[100,80,10],[90,60,0],[80,40,0]],xlim:400,spacing:200,mark:[60],years:25,dates:['2000-07-01','2003-06-30']};
  else if(url.pathname==='/api/curves')out={dates:['2000-07-01','2001-07-01','2002-06-30'],flow:[150,250,180],p:[0,50,100],q:[250,180,150],base:[150,250,180],years:[],n:3};
  else if(!url.pathname.startsWith('/api/')){res.setHeader('Content-Type','text/html');return res.end(fs.readFileSync('ewr_analysis_dashboard.html'));}
  res.end(JSON.stringify(out));
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
 try{
  browser=await chromium.launch({headless:true,args:['--enable-unsafe-swiftshader'],...(process.env.EWR_BROWSER_EXECUTABLE?{executablePath:process.env.EWR_BROWSER_EXECUTABLE}:fs.existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')?{executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(20000);
  page.on('pageerror',e=>{errors.push(e.stack);console.error(e.stack);});page.on('console',m=>{if(m.type()==='error')errors.push(m.text());if(m.type()==='warning')warnings.push(m.text());});
  const image=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==','base64');await page.route(/arcgisonline.*\/tile\//,r=>r.fulfill({contentType:'image/png',body:image}));
  await page.goto(`http://127.0.0.1:${server.address().port}/`,{waitUntil:'networkidle'});await page.waitForFunction(()=>window.UEWR?.online);
  const baseline=await page.evaluate(()=>{
   setSrc('user');const g='404216',rs=ROWS.filter(r=>META.gauges[r[2]]===g),vals=rs.map(r=>[META.pus[r[0]],g,r[3],.8,.1,10,25]);
   const c=addCustom('Display scenario',vals,'uploaded',[g],'Standard time-series',{levelGauges:[g],salinityGauges:[g]});S.pol=c.P;S.sel={type:'gauge',i:rs[0][2]};refresh();return {mean:agg(selRows(),[],'fr',scnIdx()).mean,count:selRows().length,map:PERF.gauge[S.sel.i]};
  });
  await page.evaluate(async base=>{
   for(const kind of ['F','V','L','S']){
    const params=kind==='L'?{LevelThresholdMin:1,LevelThresholdMax:5,Duration:5,TargetFrequency:50,'MaxInter-event':2}:kind==='V'?{VolumeThreshold:10000,AccumulationPeriod:30,Duration:5,TargetFrequency:50,'MaxInter-event':0}:{...base.params};
    await UEWR.save({...base,code:'U_DISPLAY_'+kind,name:kind+' display rule',kind,handler:kind==='L'?'level_handle':kind==='V'?'cumulative_handle':'flow_handle',params,...(kind==='S'?{salinity:{unit:'EC'}}:{})});
   }
   await UEWR.run([...UEWR.specs.keys()],['uploaded']);
  },fixtures.base);
  const propagation=await page.evaluate(()=>({mean:agg(selRows(),[],'fr',scnIdx()).mean,count:selRows().length,map:PERF.gauge[S.sel.i],joint:jointSummary(selRows().filter(r=>r.user),scnIdx()).n,html:document.getElementById('panel').textContent}));
  assert.equal(propagation.count,baseline.count+4);assert.ok(propagation.mean<baseline.mean);assert.notEqual(propagation.map,baseline.map);assert.equal(propagation.joint,3);assert.match(propagation.html,/U_DISPLAY_L/);
  // SDL/PU matrices and the climate table use the same live rows as gauge charts.
  assert.equal(await page.evaluate(()=>{
   const saved={...S.sel},r=ROWS.find(r=>r.user);S.sel={type:'sdl',i:r[1]};refresh();const sdl=Object.values(LASTMX).some(mx=>mx.cols.some(c=>c.rows.some(r=>r.user)));
   S.sel={type:'pu',i:r[0],sdl:r[1]};refresh();const pu=LASTMX.main.cols.some(c=>c.rows.some(r=>r.user));S.sel=saved;refresh();
   return sdl&&pu&&robustnessTable(performanceView).includes('Display scenario')&&detailTable(performanceView,scnIdx()).includes('U_DISPLAY_S');
  }),true);
  // Matrix cells must open a complete results table, including unclassified custom rules.
  await page.evaluate(()=>{const c=LASTMX.main.cols.find(c=>c.rows.some(r=>r[3]==='U_DISPLAY_L'));S.cell={mx:'main',c:c.id,r:LASTMX.main.orows.find(o=>o.unclassified).id};render();});
  assert.match(await page.locator('.cellt').textContent(),/U_DISPLAY_L/);
  await page.evaluate(()=>{S.period='mil';refresh();});assert.equal(await page.evaluate(()=>valOne(ROWS.find(r=>r[3]==='U_DISPLAY_F'),'fr',scnIdx())),.5);
  await page.evaluate(()=>{S.period='';S.flow.open=true;S.flow.chart='joint';render();});await page.waitForSelector('#jointPlot .plot-container');
  assert.equal(await page.evaluate(()=>document.getElementById('jointPlot').data.flatMap(t=>t.customdata||[]).filter(d=>String(d[0]).startsWith('U_DISPLAY_')).length),3);
  // Every kind is available to annual diagnostics and all four event views, with correct units.
  await page.evaluate(()=>loadSequences(performanceView));assert.match(await page.locator('#sequenceNote').textContent(),/rule\/scenario rows/);
  assert.equal(await page.evaluate(()=>document.getElementById('sequenceRaster').firstElementChild.data[0].y.length),4);
  for(const kind of ['F','V','L','S'])for(const chart of ['ev-year','ev-events','ev-timing','ev-gaps']){
   await page.evaluate(({kind,chart})=>{S.flow.chart=chart;S.ev.ewr='U_DISPLAY_'+kind+'|'+[...UEWR.specs.values()][0].pus[0];render();},{kind,chart});
   await page.waitForSelector('#evPlot .plot-container');
   assert.equal(await page.locator('#evEwr option').count(),4);assert.doesNotMatch(await page.locator('#evMsg').textContent(),/Could not draw/);
   if(chart==='ev-events'){
    const text=await page.locator('#evTbl').textContent();if(['L','S'].includes(kind)){assert.doesNotMatch(text,/volume|magnitude/);assert.match(text,kind==='L'?/peak \(m\)/:/peak \(EC\)/);}else assert.match(text,/Median volume/);
   }
  }
  // Flow-only overlays exclude L/S; the heat map also excludes volume and advanced handlers.
  const overlays=await page.evaluate(()=>{S.flow.chart='hydro';const hydro=flowEwrsFor('404216').filter(e=>e.user).map(e=>e.kind);S.flow.chart='heat';return {hydro,heat:flowEwrsFor('404216').filter(e=>e.user).map(e=>e.kind)};});assert.deepEqual(overlays,{hydro:['F','V'],heat:['F']});
  for(const chart of ['heat','hydro']){
   await page.evaluate(chart=>{S.flow.chart=chart;S.flow.ewr='U_DISPLAY_F|'+[...UEWR.specs.values()][0].pus[0];render();},chart);
   await page.waitForSelector('#flowPlot .plot-container');await page.waitForFunction(()=>!document.getElementById('flowMsg').textContent.startsWith('Loading'));
   assert.doesNotMatch(await page.locator('#flowMsg').textContent(),/Could not draw/);
   if(chart==='heat')assert.match(await page.locator('#flowMsg').textContent(),/U_DISPLAY_F/);
   assert.doesNotMatch(await page.locator('#flowEwr').textContent(),/U_DISPLAY_L|U_DISPLAY_S/);
  }
  // Keep an annual HTTP response in flight while deleting its rule. It must never repaint or recache it.
  holdAnnual=true;
  await page.evaluate(()=>{S.flow.chart='ev-events';S.ev.ewr='U_DISPLAY_F|'+[...UEWR.specs.values()][0].pus[0];flowsChanged();render();});
  await new Promise(resolve=>setTimeout(resolve,150));assert.ok(lateAnnual.length);
  const removed=await page.evaluate(async()=>{const s=[...UEWR.specs.values()].find(s=>s.kind==='F');S.flow.ewr=s.code+'|'+s.pus[0];S.flow.thr=['g:'+s.code+'|200|7|6'];await UEWR.remove(s.id);return s.id;});
  holdAnnual=false;for(const send of lateAnnual)send();lateAnnual=[];await page.waitForTimeout(200);
  assert.equal(specs.has(removed),false);assert.equal(await page.evaluate(()=>[...FLOWCACHE.keys()].some(k=>k.includes('U_DISPLAY_F'))),false);
  assert.equal(await page.evaluate(()=>ROWS.some(r=>r[3]==='U_DISPLAY_F')),false);assert.doesNotMatch(await page.locator('#evEwr').textContent(),/U_DISPLAY_F/);
  await page.evaluate(async()=>{for(const id of [...UEWR.specs.keys()])await UEWR.remove(id);S.flow.open=false;refresh();});
  const restored=await page.evaluate(()=>({mean:agg(selRows(),[],'fr',scnIdx()).mean,count:selRows().length,map:PERF.gauge[S.sel.i],values:CUSTOM.some(c=>c.vals.some(v=>v[2].startsWith('U_'))),rows:ROWS.some(r=>r.user),selection:S.cell}));
  assert.equal(restored.mean,baseline.mean);assert.equal(restored.count,baseline.count);assert.equal(restored.map,baseline.map);assert.equal(restored.values,false);assert.equal(restored.rows,false);assert.equal(restored.selection,null);assert.equal(specs.size,0);
  assert.deepEqual(errors,[]);assert.deepEqual(warnings,[]);console.log(JSON.stringify({checks:'F/V/L/S: maps, matrices, detail tables, droughts, joint, annual raster, 16 event views; delayed-response deletion; exact aggregate restoration',errors,warnings}));
 }finally{if(browser)await browser.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
