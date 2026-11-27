import {geminiPageStatus, geminiProfile} from './gemini_site.mjs';
import {accountPage, pageAccount, storeAccountCache} from './gemini_account.mjs';

export function parseRows(rows) {
  function windowData(label) {
    const row = rows.find(text => label.test(text.split('\n')[0]));
    const percent = row?.match(/(\d+(?:\.\d+)?)\s*%\s*used/i);
    const reset = row?.split('\n').find(line => /^Resets\b/i.test(line));
    if (!percent || Number(percent[1]) > 100) throw Error('额度页未显示完整的使用比例');
    return {remaining: 100 - Number(percent[1]), reset: reset ?? ''};
  }
  return {five: windowData(/^(Current usage|Five.hour limit|5.hour limit)$/i),
          week: windowData(/^Weekly limit$/i), checked: Date.now()};
}

export async function collect(file, selected=null, discover=false, includeQuota=true) {
  const name = includeQuota ? 'Gemini usage monitor' : 'GemCodex account startup';
  const existing = (await listTaskSpaces()).find(s => s.name === name);
  if (existing && existing.ownership !== 'agent') throw Error('额度页面正在使用中');
  const task = existing ? await taskSpace(existing.id) : await taskSpace(name, {profileId:(await geminiProfile())?.id});
  try {
    const page = (await task.pages())[0] ?? await task.newPage();
    const accounts = [];
    const activeSlot = selected?.slot ?? 0;
    const slots = discover ? [activeSlot,...[0,1,2,3,4,5,6,7].filter(slot=>slot!==activeSlot)] : [activeSlot];
    const result={checked:Date.now(), status:'page_unavailable', account:null, accounts};
    for (const slot of slots) {
      let account;
      try {
        await page.goto(accountPage(slot), {waitUntil:'commit', timeout:12000});
        await page.waitForFunction(geminiPageStatus,false,{timeout:12000});
        const status=await page.evaluate(geminiPageStatus,false);
        if (status!=='ready') {
          if (slot===activeSlot) result.status=status ?? 'page_unavailable';
          break;
        }
        account=await pageAccount(page,5000);
      } catch {
        break;
      }
      if (!account) break;
      if (account.slot!==slot) {
        if (slot!==activeSlot) break;
        result.status='account_unavailable';
        continue;
      }
      if (slot===activeSlot) {
        if (selected && account.id!==selected.id) {
          result.status='account_unavailable';
        } else {
          result.status='ready';
          result.account=account;
        }
      }
      const picker='button[aria-label^="Open mode picker"]';
      try {
        await page.waitForSelector(picker,{timeout:5000});
        await page.evaluate(s=>document.querySelector(s).click(),picker);
        const options='[role=menuitemradio][data-mode-id]';
        await page.waitForSelector(options,{timeout:5000});
        account.models=await page.evaluate(s=>[...document.querySelectorAll(s)].filter(e=>
          e.checkVisibility() && e.getAttribute('aria-disabled')!=='true' &&
          !e.getAttribute('data-mode-id').startsWith('upsell_') &&
          !/Limit resets|\block\b/i.test(e.innerText)
        ).flatMap(e=>e.innerText.split('\n').map(line=>line.trim()).filter(line=>
          /^(?:Auto|4 (?:Hydrogen|Neon))$/.test(line))),options);
      } catch {
        // Login is visible even when the model menu is unavailable.
      }
      accounts.push(account);
    }
    const account=result.account;
    await storeAccountCache(file,result);
    if (!includeQuota || !account?.models?.some(model=>model!=='Auto')) return;
    try {
      await page.goto(accountPage(account.slot,'https://gemini.google.com/usage?hl=en'),{waitUntil:'commit',timeout:10000});
      await page.waitForFunction(() => [...document.querySelectorAll('li')].some(e =>
        /Weekly limit/i.test(e.innerText) && /% used/i.test(e.innerText)), null, {timeout:9000});
      if ((await pageAccount(page,5000))?.id!==account.id) return;
      const rows = await page.evaluate(() => [...document.querySelectorAll('li')].map(e => e.innerText.trim()));
      await storeAccountCache(file,{...result,...parseRows(rows)});
    } catch {
      // Subscription and locale can change the usage page. Account status remains available.
    }
  } finally {
    await task.finish({keep:[]});
  }
}
