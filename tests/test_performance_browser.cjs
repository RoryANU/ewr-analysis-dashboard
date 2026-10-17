// Optional integration check: NODE_PATH=<playwright packages> node tests/test_performance_browser.cjs
// Runs the real generated page in an isolated headless browser, with controlled diagnostic HTTP responses.
const {chromium}=require('playwright');
const http=require('node:http'),fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
  let inventory={ewrs:[],inventory:[]},inventoryRequests=0,annualRequests=0,active=0,maxActive=0;
  const pageErrors=[];
  const server=http.createServer((req,res)=>{
    if(req.url.startsWith('/api/ewrs?')){inventoryRequests++;res.setHeader('Content-Type','application/json');res.end(JSON.stringify(inventory));return;}
    if(req.url.startsWith('/api/ewryears?')){
      annualRequests++;active++;maxActive=Math.max(active,maxActive);
      setTimeout(()=>{active--;res.setHeader('Content-Type','application/json');res.end(JSON.stringify({ystart:7,mrip_days:730,scenarios:{base:{years:{wy:[2000,2001,2002,2003,2004],achieved:[1,0,1,0,1],complete:[1,1,1,1,1]},coverage:{start:'2000-07-01',end:'2005-06-30',missing_days:0,complete_years:5,years:5}}}}));},150);return;
    }
    if(req.url.startsWith('/api/')){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({ok:true}));return;}
    res.setHeader('Content-Type','text/html');res.end(fs.readFileSync('ewr_analysis_dashboard.html'));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  try{
    browser=await chromium.launch({headless:true,...(process.env.EWR_BROWSER_EXECUTABLE?{executablePath:process.env.EWR_BROWSER_EXECUTABLE}:{})});
    const page=await browser.newPage({viewport:{width:1400,height:1000}});
    page.on('pageerror',e=>pageErrors.push(e.message));
    await page.route(/tile\.openstreetmap/,route=>route.abort());
    await page.goto(`http://127.0.0.1:${server.address().port}/`,{waitUntil:'networkidle'});
    await page.waitForFunction(()=>typeof performanceView!=='undefined');
    inventory=await page.evaluate(()=>{
      const grouped=new Map();for(const r of ROWS){if(!grouped.has(r[2]))grouped.set(r[2],[]);grouped.get(r[2]).push(r);}
      const [g,rows]=[...grouped].reduce((a,b)=>a[1].length>b[1].length?a:b);
      S.sel={type:'gauge',i:g};render();
      const rules=rows.slice(0,6).map(r=>({code:r[3],pu:META.pus[r[0]],pus:[META.pus[r[0]]]}));
      return {ewrs:rules,inventory:rules.map(r=>({...r,kind:'F',handler:'flow_handle',detailed:true}))};
    });
    assert.equal(await page.locator('#jointPlot svg').count(),1);
    assert.equal(await page.locator('#robustnessTable td').count(),0);
    await page.locator('#robustnessDetails summary').click();
    assert.ok(await page.locator('#robustnessTable td[data-scenario]').count()>=28);
    await page.locator('#robustnessTable td[data-scenario]').first().click();
    assert.match(await page.locator('#robustnessValues').textContent(),/FR/);
    await page.evaluate(()=>{window.originalPerformanceNode=performanceView.node;S.sort='high';render();});
    assert.equal(await page.evaluate(()=>performanceView.node===window.originalPerformanceNode),true);
    await page.locator('#inventoryDetails summary').click();
    await page.waitForFunction(()=>document.querySelector('#diagnosticInventory table'));
    await page.evaluate(()=>{render();});
    assert.equal(inventoryRequests,1);
    // Controlled uploaded context exercises the actual buttons, request scheduler and Plotly DOM.
    await page.evaluate(()=>{performanceView.ctx={ok:true,base:'fixture',gauge:performanceView.gauge,bn:'Fixture'};document.getElementById('loadSequence').disabled=false;});
    await page.locator('#loadSequence').click();
    await page.locator('#lockJointScale').uncheck();
    await page.waitForFunction(()=>document.getElementById('sequenceNote').textContent.includes('rule/scenario rows'));
    assert.equal(await page.locator('#loadSequence').isEnabled(),true);
    assert.equal(annualRequests,6);assert.equal(maxActive,4);
    assert.equal(inventoryRequests,1);
    assert.equal(await page.locator('#sequenceRaster .plot-container').count(),1);
    await page.locator('#conditionFile').setInputFiles({name:'observations.csv',mimeType:'text/csv',buffer:Buffer.from('date,stage_m,temperature_c\n2000-01-01,1,20\n2000-01-02,0.5,21')});
    await page.waitForFunction(()=>document.getElementById('conditionNote').textContent.includes('observations.csv'));
    assert.equal(await page.locator('#conditionPlot .plot-container').count(),1);
    await page.locator('#jointPlot').scrollIntoViewIfNeeded();
    await page.screenshot({path:process.env.EWR_REVIEW_SCREENSHOT||'/tmp/ewr-review.png'});
    assert.deepEqual(pageErrors,[]);
    console.log(JSON.stringify({inventoryRequests,annualRequests,maxActive,pageErrors,screenshot:process.env.EWR_REVIEW_SCREENSHOT||'/tmp/ewr-review.png'}));
  }finally{if(browser)await browser.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
