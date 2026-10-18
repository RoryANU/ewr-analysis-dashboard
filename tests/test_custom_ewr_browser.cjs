// Optional isolated Chromium integration test. Uses controlled backend responses and the real generated dashboard.
const {chromium}=require('playwright'),http=require('node:http'),fs=require('node:fs'),assert=require('node:assert/strict');
const fixtures=JSON.parse(fs.readFileSync('tests/fixtures/uewr_validation_cases.json','utf8'));
(async()=>{
 const definitions=new Map(),jobs=new Map(),errors=[],warnings=[];let next=0,holdJobs=false,pendingJobs=[];
 const makeRow=s=>({pu:s.pus[0],gauge:s.gauge,ewr:s.code,fr:.8,et:.1,ey:5,ny:25,dr:1,uewr_ver:s.ver});
 const server=http.createServer(async(req,res)=>{
  res.setHeader('Content-Type','application/json');let body='';for await(const chunk of req)body+=chunk;
  const data=body?JSON.parse(body):{},url=new URL(req.url,'http://test');let out={ok:true};
  if(url.pathname==='/api/uewr/schema')out=fixtures.schema;
  else if(url.pathname==='/api/uewr'&&req.method==='GET')out={specs:[...definitions.values()]};
  else if(url.pathname==='/api/uewr'&&req.method==='POST'){const spec={...data,id:data.id||'u_browser'+(++next),ver:(definitions.get(data.id)?.ver||0)+1};definitions.set(spec.id,spec);out={spec};}
  else if(url.pathname==='/api/uewr/preview')out={row:makeRow({...data.spec,ver:1})};
  else if(url.pathname==='/api/uewr/run'){const id='job'+next;out={job:id};jobs.set(id,{status:'done',progress:{stage:'Summarising',done:1,total:1},results:Object.fromEntries(data.scns.map(id=>[id,data.ids.map(id=>makeRow(definitions.get(id)))])),skipped:{},errors:{}});}
  else if(url.pathname==='/api/job'){out=jobs.get(url.searchParams.get('id'));if(holdJobs){pendingJobs.push(()=>res.end(JSON.stringify(out)));return;}}
  else if(url.pathname==='/api/uewr/delete')definitions.delete(url.searchParams.get('id'));
  else if(url.pathname==='/api/ewrs')out={ewrs:[],inventory:[]};
  else if(!url.pathname.startsWith('/api/')){res.setHeader('Content-Type','text/html');return res.end(fs.readFileSync('ewr_analysis_dashboard.html'));}
  res.end(JSON.stringify(out));
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
 try{
  browser=await chromium.launch({headless:true,...(process.env.EWR_BROWSER_EXECUTABLE?{executablePath:process.env.EWR_BROWSER_EXECUTABLE}:fs.existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')?{executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(15000);console.log('browser ready');
  page.on('pageerror',e=>{errors.push(e.message);console.error('PAGE ERROR:',e.stack);});page.on('console',m=>{if(m.type()==='error')errors.push(m.text());if(m.type()==='warning')warnings.push(m.text());});
  const image=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==','base64');
  await page.route(/\/tile\/|arcgisonline.*\/tile\//,route=>route.fulfill({contentType:'image/png',body:image}));
  await page.goto(`http://127.0.0.1:${server.address().port}/`,{waitUntil:'networkidle'});
  console.log('page loaded');await page.waitForFunction(()=>window.UEWR?.online);console.log('registry connected');
  assert.equal(await page.locator('#uewrAdd').isVisible(),false);
  await page.locator('#guideBtn').click();
  await page.evaluate(()=>{
   setSrc('user');const g='404216',r=ROWS.find(r=>META.gauges[r[2]]===g),v=[META.pus[r[0]],g,r[3],.8,.1,5,25];
   const c=addCustom('Uploaded',[v],'uploaded',[g],'Standard time-series',{levelGauges:[g],salinityGauges:[g]});S.pol=c.P;S.sel={type:'gauge',i:r[2]};refresh();
  });
  await page.locator('#uewrAdd').click();await page.locator('#uewrCode').fill('U_BROWSER');await page.locator('#uewrName').fill('Browser fresh');
  await page.locator('[data-param="Duration"]').fill('10');assert.equal(await page.locator('[data-param="MinSpell"]').inputValue(),'10');
  await page.locator('[data-param="MinSpell"]').fill('0');assert.equal(await page.locator('#uewrSave').isDisabled(),true);
  await page.locator('[data-param="MinSpell"]').fill('2');assert.equal(await page.locator('#uewrSave').isEnabled(),true);
  await page.locator('#uewrPreview').click();await page.waitForFunction(()=>document.getElementById('uewrStatus').textContent.startsWith('Preview:'));
  await page.locator('#uewrSaveRun').click();await page.waitForFunction(()=>!document.getElementById('uewrDlg').open);
  assert.equal(await page.evaluate(()=>CUSTOM[0].vals.some(v=>v[2]==='U_BROWSER'&&v[11].uewrVer===1)),true);
  assert.equal(await page.evaluate(()=>PUKEY.has('Broken River Reach 2: Hollands Creek to Caseys Weir|404216|U_BROWSER')),true);
  const first=await page.evaluate(()=>[...UEWR.specs.values()][0].id);
  await page.locator('#gqText').fill('U_BROWSER');assert.match(await page.locator('#gqList').textContent(),/U_BROWSER/);await page.locator('#gqList .gadd').first().click();assert.equal(await page.evaluate(()=>window.ewrGuide.getSel().some(k=>k.includes('|U_BROWSER|'))),true);
  await page.reload({waitUntil:'networkidle'});await page.waitForFunction(()=>window.UEWR?.online&&CUSTOM.length);
  await page.evaluate(()=>{setSrc('user');const c=CUSTOM[0];S.pol=c.P;S.sel={type:'gauge',i:META.gauges.indexOf('404216')};refresh();});
  assert.equal(await page.evaluate(()=>ROWS.find(r=>r.user)[6][CUSTOM[0].P*NC]),.8);assert.equal(await page.evaluate(()=>window.ewrGuide.getSel().some(k=>k.includes('|U_BROWSER|'))),true);
  assert.match(await page.locator('#panel').textContent(),/Unclassified/);
  holdJobs=true;await page.locator(`details[data-spec="${first}"] > summary`).click();await page.locator(`[data-u="edit"][data-id="${first}"]`).click();await page.locator('[data-param="Duration"]').fill('11');await page.locator('#uewrSave').click();
  await page.waitForFunction(()=>[...UEWR.specs.values()][0].ver===2);
  assert.equal(await page.evaluate(()=>{const r=ROWS.find(r=>r.user);return valOne(r,'fr',CUSTOM[0].P*NC);}),null);
  await page.waitForTimeout(600);holdJobs=false;for(const send of pendingJobs)send();pendingJobs=[];
  await page.waitForFunction(()=>!document.getElementById('uewrDlg').open);
  assert.equal(await page.evaluate(()=>{const r=ROWS.find(r=>r.user);return valOne(r,'fr',CUSTOM[0].P*NC);}),.8);assert.equal(await page.evaluate(()=>CUSTOM[0].vals.find(v=>v[2]==='U_BROWSER')[11].uewrVer),2);
  await page.evaluate(()=>setSrc('demo'));assert.equal(await page.locator('#uewrAdd').isVisible(),false);assert.equal(await page.evaluate(()=>selRows().some(r=>r.user)),false);
  assert.doesNotMatch(await page.locator('#gqList').textContent(),/U_BROWSER/);
  await page.evaluate(()=>setSrc('user'));await page.setViewportSize({width:375,height:812});
  await page.locator('#uewrAdd').click();await page.locator('#uewrCode').fill('U_MOBILE');await page.locator('#uewrName').fill('Mobile rule');
  await page.screenshot({path:'/tmp/ewr-user-form-mobile.png'});
  assert.equal(await page.evaluate(()=>document.getElementById('uewrDlg').scrollWidth<=document.getElementById('uewrDlg').clientWidth),true);
  await page.locator('[data-form="close"]').first().click();await page.setViewportSize({width:1440,height:1000});
  await page.evaluate(()=>{S.ev.ewr='U_BROWSER|PU';S.flow.ewr='U_BROWSER|PU';});
  if(await page.locator(`details[data-spec="${first}"]`).getAttribute('open')===null)await page.locator(`details[data-spec="${first}"] > summary`).click();await page.locator(`[data-u="delete"][data-id="${first}"]`).click();await page.locator(`[data-u="confirm-delete"][data-id="${first}"]`).click();
  await page.waitForFunction(()=>UEWR.specs.size===0);assert.equal(await page.evaluate(()=>ROWS.some(r=>r.user)),false);assert.equal(await page.evaluate(()=>S.ev.ewr),'');assert.equal(await page.evaluate(()=>window.ewrGuide.getSel().some(k=>k.includes('|U_BROWSER|'))),false);
  assert.deepEqual(errors,[]);assert.deepEqual(warnings.filter(s=>/state|mutat|unhandled/i.test(s)),[]);
  console.log(JSON.stringify({browser:'Chromium',errors,warnings,checks:'create/preview/run/reload/stale/demo/mobile/delete',mobileScreenshot:'/tmp/ewr-user-form-mobile.png'}));
 }finally{if(browser)await browser.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
