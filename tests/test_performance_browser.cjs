// Optional integration check: NODE_PATH=<playwright packages> node tests/test_performance_browser.cjs
// Runs the real generated page in an isolated headless browser, opened directly from disk.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),path=require('node:path'),{pathToFileURL}=require('node:url');
(async()=>{
  const pageErrors=[];
  let browser;
  try{
    browser=await chromium.launch({headless:true,...(process.env.EWR_BROWSER_EXECUTABLE?{executablePath:process.env.EWR_BROWSER_EXECUTABLE}:{})});
    const page=await browser.newPage({viewport:{width:1400,height:1000}});
    page.on('pageerror',e=>pageErrors.push(e.message));
    await page.goto(pathToFileURL(path.resolve('ewr_analysis_dashboard.html')).href);
    await page.waitForFunction(()=>typeof performanceView!=='undefined');
    await page.evaluate(()=>{
      const grouped=new Map();for(const r of ROWS){if(!grouped.has(r[2]))grouped.set(r[2],[]);grouped.get(r[2]).push(r);}
      const [g,rows]=[...grouped].reduce((a,b)=>a[1].length>b[1].length?a:b);
      S.sel={type:'gauge',i:g};render();
      const rules=rows.slice(0,6).map(r=>({code:r[3],pu:META.pus[r[0]],pus:[META.pus[r[0]]]}));
      return {ewrs:rules,inventory:rules.map(r=>({...r,kind:'F',handler:'flow_handle',detailed:true}))};
    });
    await page.locator('#flowDet > summary').click();
    await page.waitForSelector('#jointPlot .plot-container');
    assert.equal(await page.locator('#loadSequence').count(),0);
    await page.locator('#otherDetails > summary').click();
    assert.equal(await page.locator('#robustnessTable td').count(),0);
    await page.locator('#robustnessDetails summary').click();
    await page.waitForFunction(()=>document.querySelectorAll('#robustnessTable td[data-scenario]').length>=28);
    await page.locator('#robustnessTable td[data-scenario]').first().click();
    assert.match(await page.locator('#robustnessValues').textContent(),/FR/);
    await page.evaluate(()=>{window.originalPerformanceNode=performanceView.node;S.sort='high';render();});
    assert.equal(await page.evaluate(()=>performanceView.node===window.originalPerformanceNode),true);
    await page.locator('#lockJointScale').uncheck();
    assert.equal(await page.locator('#jointSlot #jointContent').count(),1);
    assert.equal(await page.locator('#otherDetails #jointDetails').count(),1);
    assert.equal(await page.locator('#otherDetails #robustnessDetails').count(),1);
    await page.locator('#jointPlot').scrollIntoViewIfNeeded();
    await page.screenshot({path:process.env.EWR_REVIEW_SCREENSHOT||'/tmp/ewr-review.png'});
    assert.deepEqual(pageErrors,[]);
    console.log(JSON.stringify({pageErrors,screenshot:process.env.EWR_REVIEW_SCREENSHOT||'/tmp/ewr-review.png'}));
  }finally{if(browser)await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
