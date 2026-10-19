// Optional layout check: NODE_PATH=<Playwright packages> node tests/test_observed_browser.cjs
// Uses the generated dashboard's CSS and panel code, with synthetic statuses and no data requests.
const {chromium}=require('playwright'),fs=require('node:fs'),assert=require('node:assert/strict');
const html=fs.readFileSync('ewr_analysis_dashboard.html','utf8');
function section(from,to){
  for(const marker of [from,to])assert.equal(html.split(marker).length,2,`marker ${JSON.stringify(marker)} must occur once; update the marker in the test if the template section was renamed`);
  return html.slice(html.indexOf(from),html.indexOf(to));
}
const helpers=section('// ---- observed result classification','function observedPanel(){');
const panel=section('function observedPanel(){','function render(){\n  FLOWTOKEN++');
const toggle=section('$("panel").addEventListener("toggle",event=>{','// ---- observed result classification');
const runCode=section('let observedWatch=0;','function initObserved(){');
const click=section('$("panel").addEventListener("click",event=>{const b=event.target.closest("[data-obs]")','$("panel").addEventListener("change",event=>{const t=event.target;');
(async()=>{
  let browser;
  const errors=[];
  try{
    browser=await chromium.launch({headless:true,...(process.env.EWR_BROWSER_EXECUTABLE?{executablePath:process.env.EWR_BROWSER_EXECUTABLE}:fs.existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')?{executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'}:{})});
    const page=await browser.newPage({viewport:{width:800,height:1000}});
    page.on('pageerror',e=>errors.push(e.message));
    await page.setContent(`<style>${html.match(/<style>([\s\S]*?)<\/style>/)[1]}</style><main id="panel" style="max-width:none;min-width:0"></main>`);
    await page.addScriptTag({content:`
      ${helpers}
      const META={},OBSERVED_AVAILABLE=true;
      const gauges=Array.from({length:16},(_,i)=>({id:'G'+(i+1),name:i===0?'Murrumbidgee River at a very long station name with LongUnbrokenStationNameThatMustWrapCleanly':i===1?'':'Gauge '+(i+1)}));
      const OBSERVED_UNITS=[{sdl:'Test',gauges}];
      const S={sel:{type:'none'},obs:{sdl:'Test',gauges:new Set(gauges.map(g=>g.id)),filter:false,query:'',notice:'',job:'job',
        runIds:gauges.map(g=>g.id),displayIds:gauges.map(g=>g.id),completed:false,ranges:{},status:{},progress:{stage:'Renamed download stage',stage_code:'gauges',done:12,total:16}}};
      const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])), $=id=>document.getElementById(id);
      for(let i=1;i<=4;i++)S.obs.status['G'+i]={f:{status:'ok'},l:{status:'ok'}};
      for(let i=5;i<=9;i++)S.obs.status['G'+i]={f:{status:'ok'},l:{status:'error',message:'FileNotFoundError: ~/bom_water/cache/stations.json',retryable:false}};
      S.obs.status.G10={f:{status:'empty'},l:{status:'empty'}};
      S.obs.status.G11={f:{status:'empty'},l:{status:'unsupported'}};
      S.obs.status.G12={f:{status:'timeout',message:'server did not answer'},l:{status:'error',message:'error_num 218: too many requests in queue'}};
      S.obs.status.G13={f:{status:'error'}};S.obs.status.G14={f:{status:'running'},l:{status:'ok'}};
      ${panel}
      function render(){$('panel').innerHTML=observedPanel();}
      ${runCode}
      ${click}
      ${toggle}
      render();
    `});
    const ids=()=>page.locator('[data-obs-result]').evaluateAll(nodes=>nodes.map(n=>n.dataset.obsResult));
    const original=Array.from({length:16},(_,i)=>'G'+(i+1));
    assert.deepEqual(await ids(),original);
    assert.equal(await page.locator('[data-obs-measurement]').count(),32);
    assert.match(await page.locator('.obs-progress-status').innerText(),/12 of 16 checked · 4 complete · 5 partial · 2 no data · 1 failed \(4 not finished\)/);
    assert.equal(await page.locator('[aria-live="polite"]').count(),1);
    assert.doesNotMatch(await page.locator('.obs-progress-status').innerText(),/Renamed download stage|Checking gauges:/);
    assert.match(await page.locator('[data-obs-result="G2"] .obs-gauge-identity').innerText(),/Unnamed gauge/);
    assert.equal(await page.locator('[data-obs-result] [title]').count(),0);
    assert.doesNotMatch((await page.locator('[data-obs-result]').allInnerTexts()).join('\n'),/variables|cache|BoM|source busy|FileNotFoundError|error_num/);
    assert.match(await page.locator('label').filter({has:page.locator('#obsRefresh')}).innerText(),/Refresh cached data/);
    for(const width of [800,400,320]){
      await page.setViewportSize({width,height:1000});
      await page.locator('#panel').evaluate((node,w)=>{node.style.width=w+'px';},width);
      assert.equal(Math.round((await page.locator('#panel').boundingBox()).width),width);
      const overflow=await page.locator('.obs-gauge-status').evaluateAll(nodes=>nodes.filter(n=>n.scrollWidth>n.clientWidth+1).map(n=>n.dataset.obsResult));
      assert.deepEqual(overflow,[],`row overflow at ${width}px`);
      assert.equal(await page.locator('.obs-gauge-status').first().evaluate(n=>getComputedStyle(n).flexDirection),'column');
    }
    assert.equal(await page.locator('.obs-outcome [aria-hidden="true"]').count(),16);
    assert.equal(await page.locator('.obs-reason').first().evaluate(node=>node.previousElementSibling.getAttribute('aria-hidden')),'true');
    assert.match(await page.locator('[data-obs-result="G5"] .obs-measurement-item').nth(1).locator('.obs-action').innerText(),/One-time setup/);
    const details=page.locator('[data-obs-details="G5"]');
    await details.locator('summary').focus();await page.keyboard.press('Enter');
    assert.equal(await details.getAttribute('open'),'');
    assert.deepEqual(await details.locator('.obs-explanations dd p').allTextContents(),[
      "The local copy of the Bureau of Meteorology station list is missing, so this gauge can't be looked up.",
      "One-time setup. See docs/observed-gauge-pull-guide.md. Retrying won't help."
    ]);
    assert.equal(await details.locator('.obs-technical > b').innerText(),'Technical details');
    assert.match(await details.locator('.obs-technical').innerText(),/Water level\nStatus: error\nFileNotFoundError: ~\/bom_water\/cache\/stations.json/);
    assert.equal(await details.locator('.obs-explanations').evaluate(n=>!!(n.compareDocumentPosition(n.nextElementSibling)&Node.DOCUMENT_POSITION_FOLLOWING)),true);
    await page.waitForFunction(()=>S.obs.detailsOpen?.has('G5'));
    await page.evaluate(()=>render());assert.equal(await details.getAttribute('open'),'');
    await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.copiedObserved=text;}}}));
    await details.locator('[data-obs="copyDetails"]').click();
    assert.equal(await page.evaluate(()=>window.copiedObserved),'Gauge: G5\nName: Gauge 5\n\nWater level\nStatus: error\nMessage: FileNotFoundError: ~/bom_water/cache/stations.json');
    assert.equal(await details.locator('[data-obs="copyDetails"]').innerText(),'Copied');
    await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined}));
    await details.locator('[data-obs="copyDetails"]').click();
    const fallback=details.locator('[data-obs-copy-fallback]');
    assert.equal(await fallback.isVisible(),true);
    assert.equal(await fallback.locator('textarea').inputValue(),await page.evaluate(()=>window.copiedObserved));
    assert.equal(await fallback.locator('textarea').evaluate(n=>document.activeElement===n&&n.selectionStart===0&&n.selectionEnd===n.value.length),true);
    await details.locator('summary').click();await page.waitForFunction(()=>!S.obs.detailsOpen.has('G5'));
    await page.evaluate(()=>{S.obs.status.G15={f:{status:'error'},l:{status:'error'}};render();});
    assert.deepEqual(await ids(),original);assert.equal(await page.locator('[data-obs-complete]').count(),0);
    await page.evaluate(()=>{S.obs.job=null;S.obs.runIds=[];S.obs.completed=false;render();});
    assert.deepEqual(await ids(),original);assert.equal(await page.locator('[data-obs-complete]').count(),0);
    await page.evaluate(()=>{
      for(const id of ['G13','G14','G15','G16'])S.obs.status[id]={f:{status:'empty'},l:{status:'empty'}};
      S.obs.completed=true;S.obs.notice='Loaded 9 EWR results for Test.';render();
    });
    assert.deepEqual(await ids(),['G12','G5','G6','G7','G8','G9','G10','G11','G13','G14','G15','G16','G1','G2','G3','G4']);
    const complete=page.locator('[data-obs-complete]');
    assert.equal(await complete.getAttribute('open'),null);
    assert.equal(await complete.locator('summary').first().innerText(),'Show 4 complete gauges');
    assert.equal(await page.locator('[data-obs-result="G1"]').isVisible(),false);
    await complete.locator('summary').first().click();assert.equal(await page.locator('[data-obs-result="G1"]').isVisible(),true);
    await page.waitForFunction(()=>S.obs.completeOpen===true);
    await page.evaluate(()=>render());assert.equal(await complete.getAttribute('open'),'');
    await page.locator('[data-obs-result="G1"]').scrollIntoViewIfNeeded();
    await page.screenshot({path:'/tmp/ewr-observed-rows-mobile.png',fullPage:true});
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({panelWidths:[320,400,800],checks:'16 rows, actions, one progress counter/live region, icon labels, keyboard Details, retained disclosures, clipboard and selection fallback, stable running/cancel order, completion sort/group',errors,screenshot:'/tmp/ewr-observed-rows-mobile.png'}));
  }finally{if(browser)await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
