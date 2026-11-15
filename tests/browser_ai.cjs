// Build with python tests/browser_fixture.py; serve test-results/pages-browser on port 18766.
// Requires Playwright and PAPER_BROWSER pointing to an installed headless Chromium.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');

(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.PAPER_BROWSER});
  try {
    const context=await browser.newContext(), page=await context.newPage({viewport:{width:1440,height:1000}});
    const failures=[], calls=[]; let mode='ok', foreignRequests=0;
    const key='browser-test-secret-key';
    page.on('pageerror',e=>failures.push(e.message));
    await context.route('https://evil.invalid/**',route=>{foreignRequests++;return route.abort();});
    await context.route('https://api.deepseek.com/chat/completions',async route=>{
      const request=route.request();
      const headers={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'authorization,content-type','Access-Control-Allow-Methods':'POST, OPTIONS','Content-Type':'application/json'};
      if(request.method()==='OPTIONS') return route.fulfill({status:204,headers,body:''});
      calls.push({body:request.postDataJSON(),headers:request.headers(),url:request.url()});
      if(mode==='denied') return route.fulfill({status:401,headers,body:JSON.stringify({error:key})});
      if(mode==='redirect') return route.fulfill({status:307,headers:{...headers,Location:'https://evil.invalid/collect'},body:''});
      if(mode==='hold') await new Promise(resolve=>setTimeout(resolve,800));
      try {await route.fulfill({status:200,headers,body:JSON.stringify({choices:[{finish_reason:'stop',message:{content:'基于摘要的回答。<img src=x onerror=window.poisoned=true>'}}]})});} catch {}
    });
    await page.goto(process.env.PAPER_TEST_URL || 'http://127.0.0.1:18766/paperclaw/');
    await page.waitForFunction(()=>document.querySelector('#visibleCount').textContent==='32');
    await page.locator('#wordCloud button').filter({hasText:'online scheduling'}).click();
    assert.equal(await page.locator('#visibleCount').innerText(),'2');
    await page.locator('#clearCloud').click();
    assert.equal(await page.locator('#visibleCount').innerText(),'32');
    await page.locator('#cloudSource').selectOption('topics');
    await page.locator('#wordCloud button').filter({hasText:'网络协议与架构'}).click();
    assert.equal(await page.locator('#visibleCount').innerText(),'2');
    await page.locator('#clearCloud').click();
    await page.locator('#filterCategory').selectOption('__featured__');
    assert.equal(await page.locator('#visibleCount').innerText(),'3');
    await page.locator('#filterCategory').selectOption('');
    const titleA=await page.locator('.paper h3').first().innerText();
    await page.locator('.paper-chat').first().click();
    await page.locator('[data-question]').first().click();
    assert.ok(await page.locator('#sendChat').isDisabled());
    await page.locator('#chatConnection').click();
    await page.locator('#aiKey').fill(key);
    await page.locator('#aiSettingsForm [type=submit]').click();
    assert.equal(await page.locator('#aiKey').inputValue(),'');
    assert.equal(calls.length,0);
    await page.locator('#sendChat').click();
    await page.waitForSelector('.chat-message.assistant');
    assert.equal(calls.length,1);
    assert.equal(calls[0].headers.authorization,'Bearer '+key);
    assert.ok(!JSON.stringify(calls[0].body).includes(key));
    assert.ok(calls[0].body.messages[1].content.includes(titleA));
    assert.deepEqual(calls[0].body.thinking,{type:'disabled'});
    assert.equal(await page.locator('#chatMessages img').count(),0);
    assert.equal(await page.evaluate(()=>Boolean(window.poisoned)),false);
    assert.ok(!(await page.evaluate(()=>JSON.stringify(localStorage)+JSON.stringify(sessionStorage))).includes(key));
    await page.locator('#closePaperChat').click();
    await page.locator('.paper-chat').nth(1).click();
    assert.equal(await page.locator('.chat-message').count(),0);
    await page.locator('#chatQuestion').fill('仅讨论本篇');
    await page.locator('#sendChat').click();
    await page.waitForSelector('.chat-message.assistant');
    assert.ok(!JSON.stringify(calls[1].body.messages).includes(titleA));
    await page.locator('#closePaperChat').click();
    await page.locator('.paper-chat').first().click();
    assert.equal(await page.locator('.chat-message.assistant').count(),1);
    mode='denied';
    await page.locator('#chatQuestion').fill('再次提问');
    await page.locator('#sendChat').click();
    await page.waitForFunction(()=>document.querySelector('#chatError').textContent.includes('Key 无效'));
    assert.ok(!(await page.locator('#chatError').innerText()).includes(key));
    mode='redirect';
    await page.locator('#sendChat').click();
    await page.waitForFunction(()=>document.querySelector('#chatError').textContent.includes('连接失败'));
    assert.equal(foreignRequests,0);
    mode='hold';
    await page.locator('#sendChat').click();
    await page.locator('#stopChat').click();
    await page.waitForFunction(()=>document.querySelector('#chatError').textContent.includes('已停止'));
    await page.locator('#chatConnection').click();
    await page.locator('#aiProvider').selectOption('custom');
    await page.locator('#aiEndpoint').fill('http://unsafe.example/v1');
    await page.locator('#aiModel').fill('test');
    await page.locator('#aiSettingsForm [type=submit]').click();
    assert.match(await page.locator('#aiSettingsError').innerText(),/HTTPS/);
    await page.locator('#aiEndpoint').fill('https://different.example/v1');
    await page.locator('#aiSettingsForm [type=submit]').click();
    assert.match(await page.locator('#aiSettingsError').innerText(),/重新输入 Key/);
    await page.locator('#aiKey').fill('discard-on-cancel');
    await page.keyboard.press('Escape');
    await page.waitForFunction(()=>document.querySelector('#aiKey').value==='');
    for(const width of [1440,900,390]){
      await page.setViewportSize({width,height:1000});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      assert.ok(await page.locator('#paperChat').evaluate(node=>node.getBoundingClientRect().width<=innerWidth));
    }
    await page.screenshot({path:'test-results/chat-mobile.png'});
    await page.locator('#closePaperChat').click();
    await page.reload();
    await page.waitForFunction(()=>document.querySelector('#visibleCount').textContent==='32');
    assert.equal(await page.locator('#aiSettings').innerText(),'连接我的 AI');
    await page.locator('.paper-chat').first().click();
    assert.equal(await page.locator('.chat-message').count(),0);
    assert.deepEqual(failures,[]);
    console.log('Passed cloud indexes, featured filter, chat connection, per-paper isolation, no key persistence, safe rendering, HTTP errors, redirect prevention, cancellation, custom endpoint checks and responsive dialogs.');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
