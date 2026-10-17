// Joint performance uses absolute per-EWR values, even in differential mode.
// Nulls are excluded from denominators; no temporal data are inferred from FR/ETP.
const jointClass=(fr,et)=>!Number.isFinite(fr)||!Number.isFinite(et)?null:(fr>=1?0:2)+(et<=S.ethr+1e-9?0:1);
const jointNames=['Meeting both','Frequency only','MRIP only','Failing both'];
const jointColours=['#207a65','#c38315','#527db3','#bb4052'];
const ruleLabel=r=>`${META.gauges[r[2]]} (${GAUGES.find(g=>g.i===r[2])?.name||"station name unavailable"}) · ${META.pus[r[0]]} · ${r[3]}`;
function jointSummary(rows,i){
  const counts=[0,0,0,0]; let severe=0;
  for(const r of rows){const k=jointClass(r[6][i],r[7][i]);if(k!==null){counts[k]++;if(r[6][i]<S.fbthr)severe++;}}
  const n=counts.reduce((a,b)=>a+b,0); return {counts,n,severe,score:n?counts[0]/n:null};
}
function sequenceStats(years,window=5){
  let last=null,prev=null,run=[]; const since=[],rolling=[];
  years.wy.forEach((w,i)=>{
    if(prev!==null&&w!==prev+1){last=null;run=[];} prev=w;
    if(!years.complete[i]){last=null;run=[];since.push(null);rolling.push(null);return;}
    const a=years.achieved[i]?1:0;if(a)last=w;
    since.push(last===null?null:w-last);run.push(a);if(run.length>window)run.shift();
    rolling.push(run.length===window?run.reduce((x,y)=>x+y,0)/window:null);
  });return {since,rolling};
}
function performancePanels(rows){
 const i=scnIdx(),b=scnIdx(S.bpol,S.bcli),q=jointSummary(rows,i), vals=rows.map(r=>r[6][i]).filter(Number.isFinite).sort((a,b)=>a-b);
 const mean=vals.length?vals.reduce((a,b)=>a+b,0)/vals.length:null, fmt=v=>v==null?'Unavailable':v.toFixed(3);
 const raw=vals.length?vals.filter(v=>v>=1).length/vals.length:null;
 const tol=vals.filter(v=>v>=.9&&v<1).length;
 const details=rows.map(r=>`${ruleLabel(r)}: FR ${r[6][i]??'missing'}, ETP ${r[7][i]??'missing'}; base FR ${r[6][b]??'missing'}, ETP ${r[7][b]??'missing'}`);
 return `<section class="performance"><h2>Joint frequency & inter-event performance</h2>
 <p class="note">Meeting Target: FR ≥ 1.0. Within Tolerance: 0.9 ≤ FR &lt; 1.0 (${tol} EWRs); tolerance does not establish ecological sufficiency. MRIP compliance: exceedance time proportion ≤ ${S.ethr.toFixed(3)}. These joint views always use the target of 1.0.</p>
 <div class="tiles">${q.counts.map((n,k)=>`<div class="tile"><b style="color:${jointColours[k]}">${n}</b><span>${jointNames[k]}</span></div>`).join('')}</div>
 <p>${q.n}/${rows.length} paired EWRs · ${rows.length-q.n} without paired metrics.</p>
 <label><input id="lockJointScale" type="checkbox" ${lockJointScale?'checked':''}> Lock joint axes for comparisons (FR 0–2; ETP 0–1)</label><div id="jointPlot"></div><p class="note">MRIP exceedance is a time proportion; ×10 is equivalent years per ten years, not a count of calendar years. Longest gaps require daily records; see Gaps between events vs MRIP in flow diagnostics.</p>
 <p><b>Unweighted EWR statistics:</b> mean FR ${fmt(mean)} · lower decile FR ${fmt(vals.length?vals[Math.floor((vals.length-1)*.1)]:null)} · minimum FR ${fmt(vals[0]??null)} · raw meeting target ${raw==null?'Unavailable':(100*raw).toFixed(1)+'%'} (${vals.length} observed).</p>
 <p class="note">Existing hierarchy summaries weight gauges and planning units equally at each level; their percentages are hierarchical weighted percentages, not raw percentages of EWRs.</p>
 <details><summary>Exact rule values and baseline comparisons</summary><pre style="white-space:pre-wrap">${esc(details.join('\n'))}</pre></details>
 <h2>Policy × climate robustness</h2><p class="note">Joint score = proportion meeting both, among paired EWRs. Severe failure = FR &lt; ${S.fbthr}, among paired EWRs. Coverage = paired / selected EWRs. Colour scale fixed at 0–100% for every cell.</p>${robustnessTable(rows)}
 <h2>Hydrological opportunities for recruitment</h2><p class="note">Annual achievement, time since last successful assessment year, and five-year rolling frequency. These are hydrological opportunities, not modelled recruitment outcomes.</p>
 <div id="sequenceControls"></div><div id="sequenceNote">Select an uploaded flow scenario and a gauge for annual diagnostics. Bundled summaries do not contain annual records.</div><div id="sequenceRaster"></div><div id="sequenceSince"></div><div id="sequenceRolling"></div>
 <details><summary>Detailed diagnostic rule inventory</summary><div id="diagnosticInventory">Inventory requires the local server and a selected gauge. Summary-only rules remain included in joint performance.</div></details>
 <details><summary>Stage rates, temperature & water-quality overlays</summary><p class="note">Optional observations for the selected gauge/scenario. Load an unquoted CSV: date (YYYY-MM-DD), stage_m, temperature_c, dissolved_oxygen_mg_l. Blank measurements remain missing. Rates use adjacent daily stage readings only. No ecological safety thresholds are inferred.</p><input id="conditionFile" type="file" accept=".csv"><div id="conditionNote"></div><div id="conditionPlot"></div></details>
 <footer id="performanceCoverage" class="note">Provenance: paired frequency_ratio.csv and etp.csv summary inputs (or selected uploaded results). Record dates, missing days and complete-year ratios are unavailable in summary inputs. Annual diagnostic metadata appears here when loaded. Stage, temperature and water-quality observations are not present in the supplied summary inputs.</footer></section>`;
}
function robustnessTable(rows){
 if(S.src==='user')return `<p class="note">Uploaded records have no policy × climate metadata. Climate robustness cannot be inferred from their scenario names; the table below compares records only.</p><table><tr><th>Uploaded record (climate unspecified)</th><th>Joint achievement</th><th>Severe</th><th>Coverage</th></tr>${CUSTOM.filter(c=>!c.removed).map(c=>{const q=jointSummary(rows,scnIdx(c.P,0));return `<tr><td>${esc(c.name)}</td><td>${q.score==null?'Unavailable':(q.score*100).toFixed(1)+'%'}</td><td>${q.severe}</td><td>${q.n}/${rows.length}</td></tr>`;}).join('')}</table>`;
 const policies=S.src==='user'?CUSTOM.filter(c=>!c.removed).map(c=>[c.name,c.P]):META.policies.slice(0,4).map((n,p)=>[n,p]);
 return `<div style="overflow:auto"><table><thead><tr><th>Policy</th>${META.climates.map(c=>`<th>${esc(c)}</th>`).join('')}<th>Across all climates</th></tr></thead><tbody>${policies.map(([name,p])=>{
 const indices=META.climates.map((_,c)=>scnIdx(p,c));
 const resilient=rows.filter(r=>indices.every(i=>jointClass(r[6][i],r[7][i])===0));
 const tipping=rows.filter(r=>{const ks=indices.map(i=>jointClass(r[6][i],r[7][i]));return ks.includes(0)&&ks.some(k=>k!==null&&k!==0);});
 return `<tr><th>${esc(name)}</th>${indices.map(i=>{const q=jointSummary(rows,i),base=jointSummary(rows,scnIdx(S.bpol,S.bcli));const title=rows.map(r=>`${ruleLabel(r)}: FR ${r[6][i]??'missing'}; ETP ${r[7][i]??'missing'}; base FR ${r[6][scnIdx(S.bpol,S.bcli)]??'missing'}; base ETP ${r[7][scnIdx(S.bpol,S.bcli)]??'missing'}`).join('\n');return `<td tabindex="0" title="${esc(title)}" style="background:${q.score==null?'#eee':`hsl(${q.score*130},40%,85%)`}"><b>${q.score==null?'Unavailable':(100*q.score).toFixed(1)+'%'}</b><br>${q.severe} severe<br>${q.n}/${rows.length} coverage<br>Base: ${base.score==null?'—':(100*base.score).toFixed(1)+'%'}</td>`;}).join('')}<td title="${esc('Resilient:\n'+resilient.map(ruleLabel).join('\n')+'\nThreshold crossings:\n'+tipping.map(ruleLabel).join('\n'))}">${resilient.length} resilient<br>${tipping.length} threshold crossings</td></tr>`;
 }).join('')}</tbody></table></div>`;
}
let performanceToken=0;
let lockJointScale=true;
function drawPerformance(rows){
 const token=++performanceToken,i=scnIdx(),b=scnIdx(S.bpol,S.bcli),pair=rows.filter(r=>jointClass(r[6][i],r[7][i])!==null);
 const max=lockJointScale?2:Math.max(1.2,...pair.flatMap(r=>[r[6][i],...(isDiff()&&Number.isFinite(r[6][b])?[r[6][b]]:[])])),x=v=>55+v/max*540,y=v=>270-v*240;
 let svg=`<svg role="img" aria-label="Frequency ratio versus MRIP exceedance time proportion" viewBox="0 0 650 320" style="width:100%;max-height:420px"><defs><marker id="jointArrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b"/></marker></defs><path d="M55 20V270H610" fill="none" stroke="#777"/><path d="M${x(1)} 20V270 M55 ${y(S.ethr)}H610" stroke="#888" stroke-dasharray="4 4"/><text x="280" y="310">Frequency ratio (target 1.0)</text><text transform="translate(15 245) rotate(-90)">MRIP exceedance time proportion</text>`;
 for(const v of [0,.25,.5,.75,1])svg+=`<text x="22" y="${y(v)+4}" font-size="10">${v}</text>`;
 for(const v of [0,1,max])svg+=`<text x="${x(v)}" y="290" font-size="10">${v.toFixed(2)}</text>`;
 let clipped=0;
 for(const r of pair){if(r[6][i]>max){clipped++;continue;}const f=r[6][i],e=r[7][i],k=jointClass(f,e),base=jointClass(r[6][b],r[7][b]);
 if(isDiff()&&base!==null&&r[6][b]<=max)svg+=`<path d="M${x(r[6][b])} ${y(r[7][b])}L${x(f)} ${y(e)}" stroke="#64748b" opacity=".5" marker-end="url(#jointArrow)"/><circle cx="${x(r[6][b])}" cy="${y(r[7][b])}" r="3" fill="white" stroke="#64748b"/>`;
 svg+=`<circle tabindex="0" cx="${x(f)}" cy="${y(e)}" r="4" fill="${jointColours[k]}" opacity=".8"><title>${esc(ruleLabel(r))}\n${jointNames[k]} · FR ${f} · ETP ${e}\nBase FR ${r[6][b]??'missing'} · ETP ${r[7][b]??'missing'}</title></circle>`;}
 $('jointPlot').innerHTML=svg+'</svg>'+`<p class="note">${clipped} points outside the locked FR range; unlock axes or inspect exact values above. ${isDiff()?'Hollow points = base; arrows point to comparison.':''}</p>`;
 $('lockJointScale').onchange=e=>{lockJointScale=e.target.checked;drawPerformance(rows);};
 $('conditionFile').onchange=e=>loadConditions(e.target.files[0],token);
 if(S.sel.type==='gauge')loadSequenceControls(rows,token);
}
async function loadSequenceControls(rows,token){
 try{
 const inv=await flowGet('/api/ewrs?gauge='+encodeURIComponent(META.gauges[S.sel.i]));if(token!==performanceToken)return;
 $('diagnosticInventory').innerHTML='<table><tr><th>Rule / planning unit</th><th>Type / handler</th><th>Detailed annual diagnostics</th></tr>'+inv.inventory.map(r=>`<tr><td>${esc(r.code+' · '+r.pu)}</td><td>${esc(r.kind+' / '+r.handler)}</td><td>${r.detailed?'Available':'Not supported'}</td></tr>`).join('')+'</table>';
 const c=flowCtx();if(!c||!c.ok)return;
 const list=inv.ewrs.filter(e=>rows.some(r=>r[3]===e.code&&e.pus.includes(META.pus[r[0]])));
 $('sequenceControls').innerHTML=`<button id="loadSequence">Load annual raster (${list.length} supported rules)</button>`;
 $('loadSequence').onclick=()=>loadSequences(list,c,rows,token);
 }catch(e){if(token===performanceToken)$('diagnosticInventory').textContent='Inventory unavailable: '+e.message;}
}
async function loadSequences(list,c,rows,token){
 const note=$('sequenceNote');note.textContent='Evaluating annual rules…';$('loadSequence').disabled=true;
 const series=[],coverage=[],errors=[];
 for(const e of list){
 if(token!==performanceToken)return;
 try{
 const q=new URLSearchParams({base:c.base,gauge:c.gauge,ewr:e.code,pu:e.pu});if(c.comp)q.set('comp',c.comp);
 const d=await flowGet('/api/ewryears?'+q);if(token!==performanceToken)return;
 const r=rows.find(r=>r[3]===e.code&&e.pus.includes(META.pus[r[0]]));
 const group=r?[...new Set(r[4].map(o=>META.themes[META.objTheme[o]]))].sort().join(' / '):'Unclassified';
 for(const [key,s] of Object.entries(d.scenarios)){
 const name=`${group} · ${e.code} · ${e.pu} · ${key==='base'?c.bn:c.cn} · ${ayName(d.ystart)}`;
 series.push({name,years:s.years,stats:sequenceStats(s.years)});
 const v=s.coverage;coverage.push(`${name}: ${v.start} – ${v.end}; ${v.missing_days} missing days; ${v.complete_years}/${v.years} complete assessment years; MRIP ${d.mrip_days??'unavailable'} days`);
 }
 }catch(e){errors.push(e.message);}
 }
 if(token!==performanceToken)return;
 series.sort((a,b)=>a.name.localeCompare(b.name));
 if(typeof Plotly==='undefined'){note.textContent='Plotly is unavailable; annual charts require the chart library.';return;}
 const years=[...new Set(series.flatMap(s=>s.years.wy))].sort((a,b)=>a-b);
 const aligned=(s,v)=>{const m=new Map(s.years.wy.map((w,i)=>[w,v[i]]));return years.map(w=>m.get(w)??null);};
 Plotly.newPlot('sequenceRaster',[{type:'heatmap',x:years,y:series.map(s=>s.name),z:series.map(s=>aligned(s,s.years.achieved.map((a,i)=>s.years.complete[i]?(a?1:0):null))),zmin:0,zmax:1,colorscale:[[0,'#eee'],[1,'#207a65']],hoverongaps:false,xgap:1,ygap:2,colorbar:{tickvals:[0,1],ticktext:['Failed','Achieved']}}],{height:Math.max(250,series.length*30),margin:{l:230,r:90,t:20,b:40},xaxis:{title:'Assessment start year (windows shown in row labels)',dtick:1}},PCFG);
 for(const [id,key,title] of [['sequenceSince','since','Years since last successful assessment year'],['sequenceRolling','rolling','Five-year rolling achievement proportion']])Plotly.newPlot(id,series.map(s=>({type:'scatter',mode:'lines+markers',name:s.name,x:years,y:aligned(s,s.stats[key]),connectgaps:false})),{height:280,margin:{l:70,r:20,t:20,b:45},showlegend:false,xaxis:{title:'Assessment start year'},yaxis:{title,...(key==='rolling'?{range:[0,1]}:{})}},PCFG);
 note.textContent=`${series.length} rule/scenario rows. Grey = failed, green = achieved, blank = incomplete/unavailable. Different assessment windows are labelled; alignment by start year does not imply simultaneous opportunities. Time since success is unknown before the first observed success or after a missing year; rolling values require five consecutive complete years. ${errors.length?'Failures: '+errors.join('; '):''}`;
 $('performanceCoverage').textContent='Provenance: py_ewr annual evaluation of uploaded daily flow records. '+coverage.join(' | ');
 $('loadSequence').disabled=false;
}
function parseConditions(text){
 const lines=text.replace(/^\uFEFF/,'').trim().split(/\r?\n/),headers=lines.shift().split(',').map(s=>s.trim());
 if(headers[0]!=='date'||headers.some(h=>!['date','stage_m','temperature_c','dissolved_oxygen_mg_l'].includes(h))||new Set(headers).size!==headers.length)throw Error('Use date,stage_m,temperature_c,dissolved_oxygen_mg_l headers; measurement columns are optional.');
 const data=lines.filter(s=>s.trim()).map(line=>{const a=line.split(',').map(s=>s.trim());if(a.length!==headers.length)throw Error('CSV column count does not match its header.');const date=a[0],time=Date.parse(date+'T00:00:00Z');if(!/^\d{4}-\d{2}-\d{2}$/.test(date)||!Number.isFinite(time)||new Date(time).toISOString().slice(0,10)!==date)throw Error('Invalid date: '+date);const r={date,time};headers.slice(1).forEach((h,i)=>{const v=a[i+1]===''?null:Number(a[i+1]);if(v!==null&&!Number.isFinite(v))throw Error('Invalid measurement on '+date);r[h]=v;});return r;}).sort((a,b)=>a.time-b.time);
 if(new Set(data.map(r=>r.date)).size!==data.length)throw Error('Duplicate dates are not allowed.');
 data.forEach((r,i)=>{const p=data[i-1];r.rate=p&&r.time-p.time===86400000&&Number.isFinite(r.stage_m)&&Number.isFinite(p.stage_m)?r.stage_m-p.stage_m:null;});return data;
}
async function loadConditions(file,token){
 if(!file)return;const note=$('conditionNote');
 try{
 if(S.sel.type!=='gauge')throw Error('Select a gauge before loading observations.');
 const data=parseConditions(await file.text());if(token!==performanceToken)return;
 if(!data.length)throw Error('No observation rows.');
 const rates=data.map(r=>r.rate).filter(Number.isFinite);
 note.textContent=`${file.name} · gauge ${META.gauges[S.sel.i]} · ${data[0].date} – ${data.at(-1).date}. ${rates.length} adjacent daily stage pairs. Maximum rise ${rates.length?Math.max(0,...rates).toFixed(3):'unavailable'} m/day; maximum recession ${rates.length?Math.max(0,...rates.map(v=>-v)).toFixed(3):'unavailable'} m/day. Observations are supplied by you for this selection.`;
 // Explicit breaks prevent drawing across missing calendar days.
 const expanded=[];data.forEach((r,i)=>{if(i&&r.time-data[i-1].time>86400000)expanded.push({date:new Date(data[i-1].time+86400000).toISOString().slice(0,10)});expanded.push(r);});
 const columns=[['stage_m','Stage (m)'],['rate','Stage change (m/day)'],['temperature_c','Temperature (°C)'],['dissolved_oxygen_mg_l','Dissolved oxygen (mg/L)']];
 const traces=columns.map(([k,name],i)=>({type:'scatter',mode:'lines',name,x:expanded.map(r=>r.date),y:expanded.map(r=>r[k]??null),yaxis:i?'y'+(i+1):'y',connectgaps:false}));
 const layout={height:640,margin:{l:80,r:20,t:25,b:45},showlegend:false,xaxis:{type:'date',title:'Date'}};
 columns.forEach(([k,name],i)=>{layout[i?'yaxis'+(i+1):'yaxis']={title:name,domain:[1-(i+1)*.25+.04,1-i*.25-.02]};});
 Plotly.newPlot('conditionPlot',traces,layout,PCFG);
 }catch(e){if(token===performanceToken){note.textContent=e.message;$('conditionPlot').innerHTML='';}}
}
