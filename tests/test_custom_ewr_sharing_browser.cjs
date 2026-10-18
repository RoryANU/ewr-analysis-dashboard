// Optional isolated Chromium check for portable JSON, the unsaved test and animated guidance.
const {chromium}=require('playwright'),http=require('node:http'),fs=require('node:fs'),assert=require('node:assert/strict');
const fixtures=JSON.parse(fs.readFileSync('tests/fixtures/uewr_validation_cases.json','utf8'));
(async()=>{
 const definitions=new Map(),jobs=new Map(),errors=[],warnings=[];let next=0,previewMode='ok',held=[],saves=0;
 const makeRow=s=>({pu:s.pus[0],gauge:s.gauge,ewr:s.code,fr:.8,et:.1,ey:5,ny:25,dr:1,uewr_ver:s.ver});
 const server=http.createServer(async(req,res)=>{
  res.setHeader('Content-Type','application/json');let body='';for await(const chunk of req)body+=chunk;
  const data=body?JSON.parse(body):{},url=new URL(req.url,'http://test');let out={ok:true};
  if(url.pathname==='/api/uewr/schema')out=fixtures.schema;
  else if(url.pathname==='/api/uewr'&&req.method==='GET')out={specs:[...definitions.values()]};
  else if(url.pathname==='/api/uewr'&&req.method==='POST'){saves++;const spec={...data,id:data.id||'u_sharing'+(++next),ver:(definitions.get(data.id)?.ver||0)+1};definitions.set(spec.id,spec);out={spec};}
  else if(url.pathname==='/api/uewr/preview'){
   out={row:makeRow({...data.spec,ver:1}),rows:[makeRow({...data.spec,ver:1})]};
   if(previewMode==='hold'){held.push(()=>res.end(JSON.stringify(out)));return;}
   if(previewMode==='error'){res.statusCode=400;out={errors:{request:'No matching flow series. Upload daily flows for this gauge, then test again.'}};}
  }
  else if(url.pathname==='/api/uewr/run'){const id='job'+(++next);out={job:id};jobs.set(id,{status:'done',results:Object.fromEntries(data.scns.map(scn=>[scn,data.ids.map(id=>makeRow(definitions.get(id)))]))});}
  else if(url.pathname==='/api/job')out=jobs.get(url.searchParams.get('id'));
  else if(url.pathname==='/api/uewr/delete')definitions.delete(url.searchParams.get('id'));
  else if(url.pathname==='/api/ewrs')out={ewrs:[],inventory:[]};
  else if(!url.pathname.startsWith('/api/')){res.setHeader('Content-Type','text/html');return res.end(fs.readFileSync('ewr_analysis_dashboard.html'));}
  res.end(JSON.stringify(out));
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
 try{
  browser=await chromium.launch({headless:true,...(process.env.EWR_BROWSER_EXECUTABLE?{executablePath:process.env.EWR_BROWSER_EXECUTABLE}:fs.existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')?{executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(20000);
  page.on('pageerror',e=>{errors.push(e.stack);console.error(e.stack);});page.on('console',m=>{if(m.type()==='error'&&!m.text().includes('400 (Bad Request)'))errors.push(m.text());if(m.type()==='warning')warnings.push(m.text());});
  const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==','base64');await page.route(/arcgisonline.*\/tile\//,r=>r.fulfill({contentType:'image/png',body:png}));
  await page.goto(`http://127.0.0.1:${server.address().port}/`,{waitUntil:'networkidle'});await page.waitForFunction(()=>window.UEWR?.online);await page.locator('#guideBtn').click();
  assert.equal(await page.locator('[data-u="import"]').isVisible(),false);
  await page.evaluate(()=>{setSrc('user');const g='404216',r=ROWS.find(r=>META.gauges[r[2]]===g);const c=addCustom('Uploaded',[[META.pus[r[0]],g,r[3],.8,.1,5,25]],'uploaded',[g],'Standard time-series',{levelGauges:[g],salinityGauges:[g]});S.pol=c.P;S.sel={type:'gauge',i:r[2]};refresh();});
  await page.locator('#uewrAdd').click();await page.locator('#uewrCode').fill('U_SHARE');await page.locator('#uewrName').fill('Sharing test');
  assert.match(await page.locator('#uewrTutorialStage').textContent(),/Step 1/);await page.locator('#uewrTutorialNext').click();assert.match(await page.locator('#uewrTutorialStage').textContent(),/Step 2/);
  await page.locator('#uewrTutorialPlay').click();assert.equal(await page.locator('#uewrTutorialPlay').getAttribute('aria-pressed'),'true');await page.waitForTimeout(200);await page.locator('#uewrTutorialPlay').click();
  const paused=await page.locator('#uewrTutorialSvg').innerHTML();await page.waitForTimeout(100);assert.equal(await page.locator('#uewrTutorialSvg').innerHTML(),paused);
  await page.emulateMedia({reducedMotion:'reduce'});await page.locator('#uewrTutorialPlay').click();assert.equal(await page.locator('#uewrTutorialPlay').getAttribute('aria-pressed'),'false');await page.emulateMedia({reducedMotion:'no-preference'});
  previewMode='hold';await page.locator('#uewrPreview').click();await page.waitForFunction(()=>document.getElementById('uewrPreviewCancel').hidden===false);
  assert.equal(await page.locator('#uewrCode').isDisabled(),true);assert.equal(await page.locator('#uewrSave').isDisabled(),true);assert.match(await page.locator('#uewrStatus').textContent(),/Testing on Uploaded/);
  await page.locator('#uewrPreviewCancel').click();await page.waitForFunction(()=>document.getElementById('uewrStatus').textContent.startsWith('Test cancelled'));
  for(const send of held)send();held=[];assert.equal(definitions.size,0);assert.equal(await page.locator('#uewrCode').isEnabled(),true);
  previewMode='error';await page.locator('#uewrPreview').click();await page.waitForFunction(()=>document.getElementById('uewrStatus').getAttribute('role')==='alert');assert.match(await page.locator('#uewrStatus').textContent(),/Upload daily flows/);
  previewMode='ok';await page.locator('#uewrPreview').click();await page.waitForFunction(()=>document.getElementById('uewrStatus').textContent.startsWith('Preview:'));
  assert.match(await page.locator('#uewrPreviewResults').textContent(),/10%/);assert.equal(definitions.size,0);assert.equal(await page.evaluate(()=>CUSTOM[0].vals.some(v=>v[2]==='U_SHARE')),false);await page.screenshot({path:'/tmp/ewr-builder-phase5-desktop.png'});
  await page.locator('[data-param="Duration"]').fill('6');assert.match(await page.locator('#uewrStatus').textContent(),/Definition changed/);assert.equal(await page.locator('#uewrPreviewResults').textContent(),'');
  await page.locator('#uewrSaveRun').click();await page.waitForFunction(()=>!document.getElementById('uewrDlg').open);const first=[...definitions.keys()][0];assert.equal(definitions.size,1);
  await page.locator(`details[data-spec="${first}"] > summary`).click();const downloading=page.waitForEvent('download');await page.locator(`[data-u="export"][data-id="${first}"]`).click();const download=await downloading;
  assert.equal(download.suggestedFilename(),'U_SHARE.json');const exported=JSON.parse(fs.readFileSync(await download.path(),'utf8'));assert.equal(exported.spec.params.MinSpell,6);assert.equal(Object.hasOwn(exported.spec,'id'),false);assert.equal(Object.hasOwn(exported.spec,'ver'),false);assert.equal(Object.hasOwn(exported.spec,'results'),false);
  const chooser=page.waitForEvent('filechooser');await page.locator('[data-u="import"]').click();await (await chooser).setFiles({name:'definition.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(exported))});
  await page.waitForFunction(()=>document.getElementById('uewrDlg').open);assert.match(await page.locator('#uewrStatus').textContent(),/code already exists/);assert.equal(await page.locator('#uewrSave').isDisabled(),true);assert.equal(saves,1);
  await page.locator('#uewrCode').fill('U_IMPORTED');await page.locator('#uewrSaveRun').click();await page.waitForFunction(()=>!document.getElementById('uewrDlg').open);assert.equal(definitions.size,2);assert.equal(definitions.get(first).ver,1);
  assert.equal(await page.evaluate(()=>ROWS.filter(r=>r[3]==='U_IMPORTED').length),1);
  // Imported display strings remain literal text, including strings that look like markup.
  const textOnly=structuredClone(exported);textOnly.spec.code='U_TEXT';textOnly.spec.name='<img src=x onerror="window.__injected=true">';
  await page.locator('#uewrImportFile').setInputFiles({name:'text.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(textOnly))});await page.waitForFunction(()=>document.getElementById('uewrDlg').open);
  assert.equal(await page.locator('#uewrName').inputValue(),textOnly.spec.name);assert.equal(await page.locator('#uewrDlg img').count(),0);assert.equal(await page.evaluate(()=>window.__injected),undefined);await page.locator('[data-form="close"]').first().click();
  const zero=structuredClone(exported);zero.spec.params.MinSpell=0;const missing=structuredClone(exported);delete missing.spec.params.MinSpell;const id=structuredClone(exported);id.spec.id=first;
  for(const [name,buffer,pattern] of [
   ['broken.json',Buffer.from('{'),/not valid JSON/],['zero.json',Buffer.from(JSON.stringify(zero)),/MinSpell/],['missing.json',Buffer.from(JSON.stringify(missing)),/missing required parameter/],
   ['identity.json',Buffer.from(JSON.stringify(id)),/unsupported fields/],['unsafe.json',Buffer.from('{"__proto__":{}}'),/unsafe property/],['duplicate.json',Buffer.from('{"version":1,"version":2}'),/duplicate property/],
   ['huge.json',Buffer.alloc(65537,32),/64 KiB/],['encoding.json',Buffer.from([0xff,0xfe,0xfd]),/UTF-8/],
  ]){
   await page.locator('#uewrImportFile').setInputFiles({name,mimeType:'application/json',buffer});await page.waitForFunction(()=>document.getElementById('uewrToast').getAttribute('role')==='alert');
   await page.waitForFunction(pattern=>new RegExp(pattern).test(document.querySelector('#uewrToast span').textContent),pattern.source);
   assert.match(await page.locator('#uewrToast').textContent(),pattern);assert.equal(await page.evaluate(()=>document.getElementById('uewrDlg').open),false);assert.equal(definitions.size,2);assert.equal(saves,2);
  }
  // Closing a slow test and opening a new form cannot let the old response overwrite the new draft.
  await page.locator('#uewrAdd').click();await page.locator('#uewrCode').fill('U_SLOW');await page.locator('#uewrName').fill('Slow test');previewMode='hold';await page.locator('#uewrPreview').click();
  await page.waitForFunction(()=>document.getElementById('uewrPreviewCancel').hidden===false);await page.locator('[data-form="close"]').first().click();await page.locator('#uewrAdd').click();await page.locator('#uewrCode').fill('U_NEW');
  for(const send of held)send();held=[];await page.waitForTimeout(100);assert.equal(await page.locator('#uewrCode').inputValue(),'U_NEW');assert.equal(await page.locator('#uewrCode').isEnabled(),true);assert.equal(await page.locator('#uewrStatus').textContent(),'');
  await page.locator('#uewrName').fill('Boundary guidance');await page.locator('[data-param="Duration"]').fill('366');await page.locator('[data-param="MinSpell"]').fill('366');
  assert.match(await page.locator('#uewrTutorial').textContent(),/365-day record/);assert.equal(await page.locator('#uewrSave').isEnabled(),true);
  await page.locator('[data-param="Duration"]').fill('5');await page.locator('[data-param="MinSpell"]').fill('5');
  await page.locator('[data-param="FlowThresholdMin"]').fill('1000000');await page.locator('[data-param="FlowThresholdMax"]').fill('1000001');await page.locator('#uewrTutorialNext').click();
  assert.doesNotMatch(await page.locator('#uewrTutorialSvg').innerHTML(),/NaN|Infinity/);
  await page.locator('[data-param="FlowThresholdMin"]').fill('200');await page.locator('[data-param="FlowThresholdMax"]').fill('1000000');
  await page.setViewportSize({width:375,height:812});await page.locator('#uewrName').fill('Mobile guidance');await page.locator('#uewrTutorialNext').click();await page.screenshot({path:'/tmp/ewr-builder-phase5-mobile.png'});
  assert.equal(await page.evaluate(()=>document.getElementById('uewrDlg').scrollWidth<=document.getElementById('uewrDlg').clientWidth),true);await page.locator('[data-form="close"]').first().click();
  assert.equal(await page.evaluate(()=>typeof customForm==='function'&&Array.isArray(CEWR)),true);
  await page.evaluate(()=>setSrc('demo'));assert.equal(await page.locator('[data-u="import"]').isVisible(),false);assert.equal(await page.locator('#uewrToast').isVisible(),false);
  assert.deepEqual(errors,[]);assert.deepEqual(warnings,[]);console.log(JSON.stringify({checks:'JSON download/import, collision/new identity, malformed/unsafe/oversize/UTF-8 rejection, escaped text, animation/pause/reduced motion, isolated preview/cancel/errors, stale response, 375px, Demo, legacy retained',errors,warnings}));
 }finally{for(const send of held)send();if(browser)await browser.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
