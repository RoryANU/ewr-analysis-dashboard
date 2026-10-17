// EWR_OFFLINE_HTML can point at a centrally prepared file with scenarios.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),path=require('node:path'),{pathToFileURL}=require('node:url');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.EWR_BROWSER_EXECUTABLE?{executablePath:process.env.EWR_BROWSER_EXECUTABLE}:{})});
 try{
 const page=await browser.newPage({viewport:{width:1400,height:1000}}),requests=[],errors=[],violations=[];
 page.on('console',m=>{if(m.type()==='error')console.error(m.text());});page.on('request',r=>{if(!r.isNavigationRequest()&&!r.url().startsWith('blob:'))requests.push(r.url());});page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{window.cspErrors=[];document.addEventListener('securitypolicyviolation',e=>window.cspErrors.push(e.violatedDirective));
 // file origins can deny all storage access.
 Object.defineProperty(window,'localStorage',{get(){throw Error('Storage unavailable');}});});
 await page.goto(pathToFileURL(path.resolve(process.env.EWR_OFFLINE_HTML||'ewr_analysis_dashboard.html')).href);
 await page.waitForFunction(()=>typeof DashboardData==='object'&&typeof S==='object');
 assert.equal(await page.locator('#addScn').isVisible(),false);
 const result=await page.evaluate(async()=>{
  const data=JSON.parse(document.getElementById('offline-data').textContent),ids=Object.keys(data.scenarios);
  const gauge=ids.length?Object.keys(data.scenarios[ids[0]].flows)[0]:META.gauges[0];
  S.sel={type:'gauge',i:META.gauges.indexOf(gauge)};S.flow.chart='joint';refresh();
  if(!ids.length)return {tested:false};
  const q=new URLSearchParams({base:ids[0],...(ids[1]?{comp:ids[1]}:{}),gauge,thr:'100',dmax:'10',xlim:'1000'}),outputs={};
  for(const k of ['spells','regimes','heatmap','curves']){outputs[k]=await DashboardData.json('/api/'+k+'?'+q);}
  const inv=await DashboardData.json('/api/ewrs?gauge='+gauge),e=inv.ewrs[0];
  if(e){q.set('ewr',e.code);q.set('pu',e.pu);outputs.ewryears=await DashboardData.json('/api/ewryears?'+q);q.set('scn',ids[0]);outputs.interevents=await DashboardData.json('/api/interevents?'+q);}
  const name='<img src=https://invalid.example/x onerror=alert(1)>';
  POL_L[CUSTOM[0].key]=name;CUSTOM[0].name=name;fillPolicyOptions();renderCustomList();
  return {tested:true,kinds:Object.keys(outputs),hasFlows:DashboardData.capabilities.hasFlows};
 });
 await page.locator('#flowDet > summary').click();
 await page.waitForSelector('#jointPlot .plot-container');
 // Render the interactive diagnostic plot as well as exercising its worker transport.
 if(result.tested){await page.evaluate(()=>{S.flow.open=true;S.flow.chart='heat';render();});await page.waitForSelector('#flowPlot .plot-container');}
 violations.push(...await page.evaluate(()=>window.cspErrors));
 assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);assert.deepEqual(violations,[]);
 console.log('Offline browser passed: zero subresource requests, no CSP violations, blocked localStorage, '+JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
