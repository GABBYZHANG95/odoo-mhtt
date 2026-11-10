// Temporary Playwright installation is supplied through AIIS_PLAYWRIGHT_PACKAGE.
const {chromium}=require(process.env.AIIS_PLAYWRIGHT_PACKAGE);
const fs=require('fs');
const meta=JSON.parse(fs.readFileSync('/tmp/aiis-browser-private.json','utf8'));
const out=process.env.AIIS_BROWSER_OUTPUT||'backups/test-results/20261027';
fs.mkdirSync(out,{recursive:true});
const results=[];
const base=process.env.AIIS_TEST_URL;
if(!base) throw Error('AIIS_TEST_URL must point to the isolated test server');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 async function rpc(context,model,method,args=[],kwargs={}) {
  const res=await context.request.post(`${base}/web/dataset/call_kw`,{data:{jsonrpc:'2.0',method:'call',params:{model,method,args,kwargs},id:1}});
  const j=await res.json();if(j.error) throw Error(j.error.data?.message||JSON.stringify(j.error));return j.result;
 }
 for(const login of ['cfo','chairman','demo','jeffrey','procurement','admin']){
  const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
  const page=await context.newPage();let errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  async function check(name,fn){
   if(process.env.AIIS_BROWSER_ONLY && name!=='login' && !process.env.AIIS_BROWSER_ONLY.split(',').includes(name))return;
   errors=[];
   try{const detail=await fn();results.push({login,name,status:'PASS',detail,errors:[...errors]});console.log('PASS',login,name);}
   catch(e){results.push({login,name,status:'FAIL',detail:e.message,errors:[...errors]});console.log('FAIL',login,name,e.message.slice(0,160));try{await page.screenshot({path:`${out}/${login}-${name}-failure.png`,fullPage:true})}catch{}}
   fs.writeFileSync(`${out}/${process.env.AIIS_BROWSER_RESULTS||'browser-results.json'}`,JSON.stringify(results,null,2));
  }
  await check('login',async()=>{
   await page.goto(`${base}/web/login?db=${meta.database}`); await page.reload();
   await page.locator('input[name="login"]').fill(login);
   await page.locator('input[name="password"]').fill(meta.password);
   await page.locator('button[type="submit"]').click();
   await page.waitForSelector('.o_web_client',{timeout:45000});
   if(page.url().includes('/web/login'))throw Error('Login did not succeed');
   return 'Browser login succeeded';
  });
  if(['cfo','chairman'].includes(login)){
   for(const [key,name] of [['action_finance_overview','overview'],['action_finance_cash_flow','cash-flow'],['action_finance_checks','checks']]){
    await check(name,async()=>{
     await page.goto(`${base}/web?db=${meta.database}#action=${meta.actions[key]}`); await page.reload();
     await page.getByRole('heading',{name:({overview:'Finance Overview','cash-flow':'Cash Flow',checks:'Checks'})[name],exact:true}).waitFor({timeout:30000});
     await page.waitForSelector('.o_aiis_finance_board table tbody tr',{timeout:30000});
     if(errors.length)throw Error(errors.join('\n'));
     const text=await page.locator('.o_aiis_finance_board').innerText();
     if(name==='overview' && await page.locator('.o_aiis_card').count()!==4)throw Error('Expected four KPI cards');
     if(name==='cash-flow' && await page.locator('.o_aiis_finance_board svg').count()<1)throw Error('Cash flow chart missing');
     await page.screenshot({path:`${out}/${login}-${name}.png`,fullPage:true});
     return text;
    });
   }
   await check('profit-loss',async()=>{
    const rec=meta.records.pl;
    await page.goto(`${base}/web?db=${meta.database}#action=${meta.actions.pl_preview}`); await page.reload();
    await Promise.race([page.waitForSelector('.oe_mis_builder_content table',{timeout:15000}),page.waitForSelector('.o_error_dialog',{timeout:15000}).then(async()=>{const d=page.getByText('See details',{exact:true});if(await d.count())await d.click();throw Error(await page.locator('.o_error_dialog').innerText());})]);
    await page.screenshot({path:`${out}/${login}-profit-loss.png`,fullPage:true});
    if(errors.length)throw Error(errors.join('\n'));
    return (await page.locator('.oe_mis_builder_content').innerText()).slice(0,18000);
   });
   if(login==='cfo'){
    await check('overview-filters',async()=>{
     await page.goto(`${base}/web?db=${meta.database}#action=${meta.actions.action_finance_overview}`); await page.reload();
     await page.waitForSelector('.o_aiis_card');
     const before=await page.locator('.o_aiis_card_value').allTextContents();
     await page.locator('.o_aiis_filter select').nth(1).selectOption(String(meta.records.project.id));
     await page.waitForTimeout(800);
     const after=await page.locator('.o_aiis_card_value').allTextContents();
     if(JSON.stringify(before)===JSON.stringify(after))throw Error('Project filter did not change KPI values');
     await page.screenshot({path:`${out}/cfo-overview-filtered.png`,fullPage:true});
     await page.locator('.o_aiis_filter input[type=date]').fill('2026-04-30');
     await page.locator('.o_aiis_filter input[type=date]').press('Tab');
     await page.waitForTimeout(600);
     const cells=await page.locator('.o_aiis_table tbody tr').first().locator('td').allTextContents();
     if(cells.slice(-2).some(x=>x.trim()))throw Error('Future quarter contains numbers');
     return {before,after,futureCells:cells.slice(-2)};
    });
    for(let i=0;i<4;i++)await check(`drill-${i}`,async()=>{
     await page.goto(`${base}/web?db=${meta.database}#action=${meta.actions.action_finance_overview}`); await page.reload();
     await page.waitForSelector('.o_aiis_card');await page.locator('.o_aiis_card').nth(i).click();
     await page.waitForSelector('.o_list_view',{timeout:20000});
     if(errors.length)throw Error(errors.join('\n'));return page.url();
    });
   }
  }
  if(['demo','jeffrey','procurement'].includes(login)){
   await check('menus',async()=>{
    const menus=await rpc(context,'ir.ui.menu','load_menus',[false]);
    const menuNames=Object.values(menus).map(x=>x.name);
    const xmlids=Object.values(menus).map(x=>x.xmlid);
    if(xmlids.includes('aiis_finance.menu_finance_overview')||xmlids.includes('aiis_finance.menu_finance_reports'))throw Error('Restricted finance menus visible');
    if(['demo','jeffrey'].includes(login)&&xmlids.includes('aiis_finance.menu_personnel_cost'))throw Error('Personnel Costs menu visible');
    return {menuNames,xmlids};
   });
   await check('project-form',async()=>{
    const p=meta.records.project;
    await page.goto(`${base}/web?db=${meta.database}#model=aiis.project&id=${p.id}&view_type=form`); await page.reload();
    await page.waitForSelector('.o_form_view .o_field_widget[name="name"]',{timeout:20000});
    if(await page.locator('.o_error_dialog').count())throw Error('Odoo error dialog');
    await page.screenshot({path:`${out}/${login}-project.png`,fullPage:true});
    const costField=page.locator('.o_field_widget[name="total_cost"]');
    if(login==='demo'&&await costField.count()&&await costField.isVisible())throw Error('Sales can see project costs');
    return (await page.locator('.o_form_view').innerText()).slice(0,4000);
   });
  }
  if(login==='cfo'){
   await check('bank-create-permission',async()=>{
    const can=await rpc(context,'account.bank.statement.line','check_access_rights',['create',false]);
    if(!can)throw Error('CFO cannot create bank statement lines');return can;
   });
  }
  await context.close();
 }
 await browser.close();
 console.log('SUMMARY',JSON.stringify(results.reduce((a,r)=>(a[r.status]=(a[r.status]||0)+1,a),{})));
})().catch(e=>{console.error(e);process.exit(1)});
