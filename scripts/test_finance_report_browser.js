// End-to-end MIS preview, exports and drilldown through the real filter dialog.
const {chromium}=require(process.env.AIIS_PLAYWRIGHT_PACKAGE);
const fs=require('fs');
const meta=JSON.parse(fs.readFileSync('/tmp/aiis-browser-private.json','utf8'));
const base=process.env.AIIS_TEST_URL;
const out=process.env.AIIS_BROWSER_OUTPUT;
if(!base||!out||!meta.database.startsWith('aiis_flow_test_'))throw Error('An isolated test server and output directory are required');
fs.mkdirSync(out,{recursive:true});
const results=[];
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 for(const login of ['cfo','chairman']){
  const context=await browser.newContext({viewport:{width:1440,height:1100},acceptDownloads:true});
  const auth=await context.request.post(`${base}/web/session/authenticate`,{data:{jsonrpc:'2.0',params:{db:meta.database,login,password:meta.password}}});
  if((await auth.json()).error)throw Error('Test authentication failed');
  const page=await context.newPage();
  async function check(name,fn){
   try{const detail=await fn();results.push({login,name,status:'PASS',detail});console.log('PASS',login,name)}
   catch(e){results.push({login,name,status:'FAIL',detail:e.message});console.log('FAIL',login,name,e.message.slice(0,180));await page.screenshot({path:`${out}/${login}-${name}-failed.png`,fullPage:true})}
   fs.writeFileSync(`${out}/report-browser-results.json`,JSON.stringify(results,null,2));
  }
  async function open(kind,month=false){
   const action=meta.actions[kind==='pl'?'action_report_profit_loss':'action_report_balance_sheet'];
   await page.goto(`${base}/web?db=${meta.database}#action=${action}`);await page.reload();
   await page.locator('button[name="action_open"]').waitFor();
   if(month)await page.getByText('Months',{exact:true}).click();
   await page.locator('button[name="action_open"]').click();
   await page.waitForSelector('.oe_mis_builder_content tbody tr',{timeout:25000});
   if(await page.locator('.o_error_dialog').count())throw Error(await page.locator('.o_error_dialog').innerText());
  }
  for(const kind of ['pl','bs']){
   await check(`${kind}-normal-preview`,async()=>{await open(kind);const rows=await page.locator('.oe_mis_builder_content tbody tr').count();if(rows<10)throw Error('Report has too few rows');await page.screenshot({path:`${out}/${login}-${kind}-normal.png`,fullPage:true});return {rows,headers:await page.locator('.oe_mis_builder_content thead').innerText()}});
  }
  if(login==='cfo'){
   await check('pl-months',async()=>{await open('pl',true);const headers=await page.locator('.oe_mis_builder_content thead').innerText();if(!headers.includes('Jan 2026')||!headers.includes('Dec 2026'))throw Error(headers);return headers});
   await check('pl-xlsx-download',async()=>{await open('pl');const pending=page.waitForEvent('download',{timeout:30000});await page.locator('.oe_mis_builder_action_buttons button').filter({hasText:'Export'}).click();const download=await pending;await download.saveAs(`${out}/ui-profit-loss.xlsx`);if(await download.failure())throw Error(await download.failure());return download.suggestedFilename()});
   await check('pl-pdf-download',async()=>{const pending=page.waitForEvent('download',{timeout:30000});await page.locator('.oe_mis_builder_action_buttons button').filter({hasText:'Print'}).click();const download=await pending;await download.saveAs(`${out}/ui-profit-loss.pdf`);if(await download.failure())throw Error(await download.failure());return download.suggestedFilename()});
   await check('pl-account-drilldown',async()=>{await page.locator('a.mis_builder_drilldown').filter({hasText:/\d/}).first().click();await page.waitForSelector('.o_list_view',{timeout:20000});if(await page.locator('.o_error_dialog').count())throw Error(await page.locator('.o_error_dialog').innerText());await page.screenshot({path:`${out}/cfo-pl-drilldown.png`,fullPage:true});return page.url()});
  }
  await context.close();
 }
 await browser.close();
 console.log('SUMMARY',JSON.stringify(results.reduce((a,r)=>(a[r.status]=(a[r.status]||0)+1,a),{})));
})().catch(e=>{console.error(e);process.exit(1)});
