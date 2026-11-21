import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {geminiApp, geminiPageStatus, geminiProfile} from './gemini_site.mjs';

const picker = 'button[aria-label^="Open mode picker"]';

export function hasSubmittedTurn(marker) {
  return Array.from(document.querySelectorAll('user-query'))
    .some(query => query.textContent.includes(marker));
}

export async function responseDetails(page,note,mode,effort,summaries) {
  // The web app exposes optional, user-facing step titles in its response menu.
  // Copy titles only; the body of a thinking step is not part of this bridge.
  try {
    await page.evaluate(()=>document.querySelector('context-sidebar button[aria-label="Close sidebar"]')?.click());
    const opened=await page.evaluate(()=>{
      const more=Array.from(document.querySelectorAll('model-response')).at(-1)?.querySelector('button[aria-label="Show more options"]');
      if (!more) return false;
      more.click();
      return true;
    });
    if (!opened) return;
    await page.waitForSelector('[role=menu]',{timeout:2000});
    const available=await page.evaluate(()=>{
      const item=Array.from(document.querySelectorAll('[role=menuitem]')).find(e=>
        ['Show thinking steps','See response details'].some(label=>e.ariaLabel===label || e.innerText.trim()===label));
      if (!item) return false;
      item.click();
      return true;
    });
    if (!available) {await page.keyboard.press('Escape');return;}
    await page.waitForFunction(()=>Array.from(document.querySelectorAll('context-sidebar')).some(e=>
      e.innerText.split('\n').some(line=>/^Used\s+/.test(line.trim())) || e.querySelector('.thought-step-title')) ,null,{timeout:3500});
    const {used,titles}=await page.evaluate(()=>{
      const panel=document.querySelector('context-sidebar');
      const used=panel?.innerText.split('\n').map(s=>s.trim()).find(s=>/^Used\s+(?:Gemini\s+)?\d/i.test(s));
      const titles=Array.from(panel?.querySelectorAll('.thought-step-title')??[]).filter(e=>e.checkVisibility())
        .map(e=>e.innerText.trim()).filter(s=>s && s.length<=180).slice(0,8);
      return {used,titles};
    });
    if (used) {
      const actual=used.match(/^Used\s+(?:Gemini\s+)?4\s+(Argon|Hydrogen)(?:\s+(Low|Medium|High))?(?:\s|$)/i);
      if (!actual || actual[1].toLowerCase()!==mode.toLowerCase() || (actual[2] && actual[2].toLowerCase()!==effort))
        throw Error('Codex 请求 '+mode+' · '+effort+'，但 Gemini 回复详情显示 '+used.slice(0,90)+'。本轮结果未采用。');
      await note('Gemini 回复详情确认：'+used.slice(5)+'。');
    }
    if (summaries) for (const label of titles) await note('Gemini 网页公开步骤：'+label);
  } catch (err) {
    if (!/timed out/.test(String(err))) throw err;
  } finally {
    await page.evaluate(()=>document.querySelector('context-sidebar button[aria-label="Close sidebar"]')?.click());
  }
}

export async function openGemini(page, target) {
  try {
    await page.goto(target, {timeout: 30000, waitUntil: 'commit'});
  } catch (err) {
    if (!/page\.goto timed out|net::ERR_(CONNECTION_RESET|CONNECTION_CLOSED|TIMED_OUT)/i.test(String(err))) throw err;
    // Navigation may have committed before the load event timed out.
    const url = new URL(await page.url());
    if (url.href !== target || !await page.evaluate(() => !!document.querySelector('button[aria-label^="Open mode picker"]'))) {
      await page.goto(target, {timeout: 45000, waitUntil: 'commit'});
    }
  }
  try {
    await page.waitForSelector(picker, {timeout: 45000});
  } catch (error) {
    const status = await page.evaluate(geminiPageStatus).catch(() => null);
    if (status === 'region_unavailable') throw Error('Gemini 网页提示当前地区暂不支持，消息没有发送。');
    if (status === 'login_required') throw Error('Gemini 登录已失效，请在 Ego Lite 登录，或运行 Gemini-Codex.cmd --setup。');
    throw error;
  }
}

async function openModelMenu(page, mode) {
  const options='[role=menuitemradio][data-mode-id]';
  if (!await page.evaluate(s=>Array.from(document.querySelectorAll(s)).some(e=>e.checkVisibility()),options)) {
    await page.evaluate(selector=>{
      const button=document.querySelector(selector);
      if (!button) throw Error('Gemini model picker is unavailable');
      button.click();
    },picker);
  }
  await page.waitForSelector(options,{timeout:10000});
  const limit=await page.evaluate(({options,mode})=>{
    const description={Hydrogen:'Everyday help',Argon:'Code & analysis'}[mode];
    const option=Array.from(document.querySelectorAll(options)).find(e=>
      e.checkVisibility() && e.innerText.split('\n').map(s=>s.trim()).some(s=>s===description || s==='4 '+mode));
    return option?.innerText.match(/Limit resets[^\n]*/i)?.[0];
  },{options,mode});
  if (limit) {
    await page.keyboard.press('Escape');
    throw Error('Gemini 网页显示 '+mode+' 当前额度已用尽：'+limit+'。本次请求尚未发送。');
  }
}

export async function setThinkingLevel(page, effort, expectedMode) {
  const label = {low:'Low', medium:'Medium', high:'High'}[effort];
  if (!label) throw Error('Unsupported Gemini thinking level');
  async function openLevels() {
    await openModelMenu(page,expectedMode);
    if (expectedMode) {
      await page.waitForFunction(mode => Array.from(document.querySelectorAll('[role=menuitemradio][aria-checked=true]'))
        .some(e=>e.innerText.split('\n').some(line=>line.trim()==='4 '+mode)), expectedMode, {timeout:5000});
    }
    await page.waitForFunction(() => {
      const trigger = Array.from(document.querySelectorAll('[role=menu] [role=menuitem]')).find(e =>
        e.innerText.includes('Thinking level') && e.getAttribute('aria-disabled') !== 'true');
      if (!trigger) return false;
      trigger.click();
      return true;
    });
    await page.waitForFunction(name => Array.from(document.querySelectorAll('[role=menuitemradio]')).some(e=>e.innerText.split('\n')[0]===name), label);
  }
  async function selected() {
    return page.evaluate(name => Array.from(document.querySelectorAll('[role=menuitemradio]')).some(e=>
      e.innerText.split('\n')[0]===name && e.getAttribute('aria-checked')==='true' && e.getAttribute('aria-disabled')!=='true'), label);
  }
  await openLevels();
  if (!(await selected())) {
    // The custom radio host can have no pointer target while its submenu animates.
    // Activate the enabled option directly and verify the checked state on reopening.
    await page.waitForFunction(name => {
      const option = Array.from(document.querySelectorAll('[role=menu] [role=menuitemradio]')).find(e =>
        e.innerText.split('\n')[0] === name && e.getAttribute('aria-disabled') !== 'true');
      if (!option) return false;
      option.click();
      return true;
    }, label);
    await page.waitForFunction(() => !document.querySelector('[role=menu]'));
    await openLevels();
    if (!(await selected())) throw Error('Gemini did not apply thinking level ' + label);
  }
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.querySelector('[role=menu]'));
}

export async function run(jobUrl) {
  const file = fileURLToPath(jobUrl);
  const {mode, effort='medium', prompt, space, images=[], record, session={}, summaries=false,reload=false,compact=false,toolDefs,delivery,requestKey} = JSON.parse(await fs.readFile(file, 'utf8'));
  const note = message => fs.appendFile(path.join(path.dirname(file),'progress.jsonl'),JSON.stringify(message)+'\n');
  const chatUrl = value => /^https:\/\/gemini\.google\.com\/(?:u\/\d+\/)?app\/[\w-]+$/.test(value);
  if (session.url && !chatUrl(session.url)) throw Error('无效的 Gemini 会话地址');
  const name=session.name || 'Codex Gemini '+(record ? path.basename(record,'.json') : path.basename(path.dirname(file)));
  const saved = (await listTaskSpaces()).find(s=>s.name===name && (!session.space || s.id===session.space));
  if (!space && session.space && !saved) throw Error('EGO_BOUND_SPACE_NOT_FOUND');
  if (saved && !['agent','user'].includes(saved.ownership)) throw Error('Gemini 会话正在由用户操作');
  const task = space ? await taskSpace(space) : saved ? (saved.ownership==='user' ? await claimTaskSpace(saved.id) : await taskSpace(saved.id)) : await taskSpace(name, {profileId:(await geminiProfile())?.id});
  const tabs=await task.tabs();
  const tab=tabs.find(t=>session.targetId && t.targetId===session.targetId) ??
    tabs.find(t=>session.url && t.url===session.url) ?? ((!saved || !session.url) && tabs.length===1 ? tabs[0] : undefined);
  if ((!tab && tabs.length) || (saved && session.url && tab && tab.url!==session.url)) {
    if (saved?.ownership==='user') await task.finish({keep:'all'});
    throw Error('原 Gemini 标签已改变，无法安全地续接此聊天。');
  }
  const page=tab ? (tab.label ? task.page(tab.label) : await task.adopt(tab.page)) : await task.newPage();
  const targetId=tab?.targetId ?? (await task.tabs()).find(t=>t.label===page.label)?.targetId;
  const wasIdle=Date.now()-(session.lastUsedAt??0)>15*60*1000;
  async function save(url) {
    if (!record) return;
    if (url) session.url=url;
    Object.assign(session,{space:task.spaceId,name:task.name,targetId,lastUsedAt:Date.now()});
    await fs.writeFile(record+'.new',JSON.stringify(session));
    await fs.rename(record+'.new',record);
  }
  if (saved && !session.url && chatUrl(await page.url())) await save(await page.url());
  await save();
  const target=session.url || geminiApp;
  await note(session.url?'本机网关：正在续接此聊天的 Gemini 会话。':'本机网关：正在打开 Gemini 会话。');
  if (await page.url() !== target || (session.url && (wasIdle || reload))) await openGemini(page,target);
  await page.waitForSelector(picker,{timeout:30000});
  if (session.url && await page.url()!==session.url) throw Error('原 Gemini 会话未能打开。');
  await save();
  const selection = () => page.evaluate(() => {
    const label = document.querySelector('button[aria-label^="Open mode picker"]')?.ariaLabel;
    return label?.match(/currently (Hydrogen|Argon)(?: (Low|Medium|High))?$/i)?.slice(1) ?? [];
  });
  async function verifySelection() {
    if ((await selection())[0] !== mode) {
      await openModelMenu(page,mode);
      await page.waitForFunction(name => {
        const option = Array.from(document.querySelectorAll('[role=menu] [role=menuitemradio]'))
          .find(e => e.innerText.split('\n').some(line => line.trim() === '4 ' + name));
        if (!option) return false;
        if (option.getAttribute('aria-disabled') === 'true' || option.innerText.split('\n').includes('lock')) {
          throw Error('Requested Gemini model is unavailable');
        }
        option.click();
        return true;
      }, mode);
      await page.waitForFunction(name => {
        const label = document.querySelector('button[aria-label^="Open mode picker"]')?.ariaLabel;
        return label?.match(/currently (Hydrogen|Argon)(?: (Low|Medium|High))?$/i)?.[1] === name &&
          !document.querySelector('[role=menu]');
      }, mode);
    }
    // Reuse a verified setting only when the live picker explicitly confirms both values.
    const [liveModel,liveLevel]=await selection();
    if (!session.url || session.checkedMode!==mode || session.checkedEffort!==effort ||
        liveModel!==mode || liveLevel?.toLowerCase()!==effort) {
      await setThinkingLevel(page, effort, mode);
    } else {
      // The picker may retain the cached label even after the account hits its limit.
      await openModelMenu(page,mode);
      await page.keyboard.press('Escape');
    }
    if ((await selection())[0] !== mode) throw Error('网页确认的模型与 Codex 选择不符');
  }
  await verifySelection();
  await note('网页已选中 Gemini 4 '+mode+' · '+effort+'。');
  let verifyImages=0;
  async function attachImages() {
    if (!images.length) return;
    await page.click('button[aria-label="Upload & tools"]');
    const [chooser]=await Promise.all([page.waitForFileChooser({timeout:10000}),page.click('button[aria-label^="Upload files"]')]);
    await chooser.setFiles(images);
    verifyImages=images.length;
    await note('本机网关：已向网页添加 '+images.length+' 张图片。');
  }
  let resume=requestKey && session.inflight?.requestKey===requestKey && !reload ? session.inflight : null;
  const endMarker=resume?.end || 'END_LITERAL_'+randomUUID();
  if (!resume) await attachImages();
  async function checkCancel(submitted=false) {
    try { await fs.access(path.join(path.dirname(file),'cancel')); }
    catch (err) {
      if(err.code!=='ENOENT') throw err;
      try { await fs.access(file); return; }
      catch (missing) { if(missing.code!=='ENOENT') throw missing; }
    }
    if (submitted && session.url && await page.url()===session.url) {
      await page.evaluate(()=>document.querySelector('button[aria-label="Stop response"]')?.click());
    }
    delete session.inflight;
    if(record) await save();
    throw Error('Codex 已取消请求。');
  }
  async function sendDraft(text) {
    await checkCancel();
    if (await page.evaluate(()=>!!document.querySelector('button[aria-label="Stop response"]')))
      throw Error('Gemini 网页仍在生成上一条回复，本次请求尚未发送。');
    if (session.url && session.history?.length) {
      await page.waitForFunction(() => document.querySelectorAll('user-query').length > 0 &&
        document.querySelectorAll('model-response').length > 0, null, {timeout:45000});
    }
    const previous = await page.evaluate(() => document.querySelectorAll('model-response').length);
    const marker = text.split('\n',1)[0];
    await page.evaluate(() => {
      const box = document.querySelector('[contenteditable=true][role=textbox]');
      if (!box) throw Error('Gemini editor unavailable');
      box.focus();
      const range = document.createRange();
      range.selectNodeContents(box);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    });
    await page.keyboard.insertText(text);
    const matchesDraft = expected => {
      const box = document.querySelector('[contenteditable=true][role=textbox]');
      const normalize = value => value.replace(/\r\n?/g,'\n').replace(/\u00a0/g,' ').trimEnd();
      if (!box) return false;
      const lines=Array.from(box.children);
      const variants=[box.innerText,box.textContent];
      if (lines.length && [...box.childNodes].every(node => node.nodeType===1 && /^(P|DIV)$/.test(node.tagName)))
        variants.push(lines.map(line=>line.innerText.replace(/^\n$/,'')).join('\n'));
      return variants.some(value => normalize(value) === normalize(expected));
    };
    try {
      await page.waitForFunction(matchesDraft,text,{timeout:3500});
    } catch (err) {
      if (!String(err).includes('timed out')) throw err;
      await checkCancel();
      await page.evaluate(value => {
        const box=document.querySelector('[contenteditable=true][role=textbox]');
        if (!box) throw Error('Gemini editor unavailable');
        box.focus();
        const range=document.createRange();
        range.selectNodeContents(box);
        const selection=window.getSelection();
        selection.removeAllRanges(); selection.addRange(range);
        if (!document.execCommand('insertText',false,value)) throw Error('Gemini editor rejected insertion');
      },text);
      try { await page.waitForFunction(matchesDraft,text,{timeout:5000}); }
      catch {
        const stats=await page.evaluate(()=>{const b=document.querySelector('[contenteditable=true][role=textbox]');return [b?.innerText.length,b?.textContent.length];});
        throw Error('Gemini draft verification failed before submission: expected '+text.length+' chars; editor lengths '+stats);
      }
    }
    if ((await selection())[0] !== mode) throw Error('发送前网页模型已改变');
    if (session.url && await page.url()!==session.url) throw Error('发送前网页会话已改变');
    const send='button[aria-label="Send message"]:not([disabled]):not([aria-disabled=true])';
    await page.waitForSelector(send,{timeout:45000});
    await checkCancel();
    if (requestKey) {
      session.inflight={requestKey,marker,previous,end:endMarker};
      await save();
    }
    await page.click(send);
    try {
      await page.waitForFunction(hasSubmittedTurn,marker,{timeout:30000});
    } catch (err) {
      if (!String(err).includes('timed out')) throw err;
      // Retry only when the exact unsent draft remains and no turn was created.
      await page.evaluate(({marker,text,send})=>{
        const button=document.querySelector(send);
        if (!button || document.querySelector('button[aria-label="Stop response"]') ||
            Array.from(document.querySelectorAll('user-query')).some(query=>query.textContent.includes(marker)) ||
            document.querySelector('[contenteditable=true][role=textbox]')?.innerText.replace(/\r\n?/g,'\n').replace(/\u00a0/g,' ').trimEnd()!==text.replace(/\r\n?/g,'\n').replace(/\u00a0/g,' ').trimEnd()) return false;
        button.click();
        return true;
      },{marker,text,send});
      // A slow optimistic update can arrive after the first wait. Do not
      // click again unless the exact draft is still present and idle.
      await page.waitForFunction(hasSubmittedTurn,marker,{timeout:30000});
    }
    await note('已提交给 Gemini 4 '+mode+'，正在等待网页回复。');
    await fs.appendFile(path.join(path.dirname(file),'sent.jsonl'),String(text.length)+'\n');
    if (verifyImages) {
      await page.waitForFunction(count=>{
        const query=Array.from(document.querySelectorAll('user-query')).at(-1);
        return query?.querySelectorAll('img[alt="Uploaded image preview"]').length>=count;
      },verifyImages,{timeout:15000});
      verifyImages=0;
    }
    return previous;
  }
  async function exchange(text,timeout) {
    let previous;
    if(resume) {
      const query=await page.evaluate(()=>Array.from(document.querySelectorAll('user-query')).at(-1)?.textContent || '');
      if(!query.includes(resume.marker) || await page.url()!==session.url)
        throw Error('无法核对待恢复的网页消息，已停止重复提交。');
      previous=resume.previous;
      resume=null;
      await note('原请求已发出，正在接收原网页回复。');
    } else {
      previous=await sendDraft('Turn '+randomUUID().slice(0,12)+'\n'+text);
    }
    const start=Date.now();
    let lastTitle='', lastUpdate=start;
    for (;;) {
      if (record && !session.url) {
        const url=await page.url();
        if (chatUrl(url)) await save(url);
      }
      await checkCancel(true);
      const state=await page.evaluate(count => {
        const replies = document.querySelectorAll('model-response');
        if(replies.length<=count) return {};
        const latest=replies[replies.length-1];
        // Only forward a visible UI heading/status. Never open the thoughts panel.
        const title=Array.from(latest.querySelectorAll('thinking-overlay [role=status],thinking-overlay [role=heading],thinking-overlay h3,thinking-overlay h4'))
          .find(e=>e.checkVisibility() && e.innerText.trim().length>0 && e.innerText.trim().length<=180)?.innerText.trim();
        return {title,done:!!latest.querySelector('button[aria-label="Copy"]') && !document.querySelector('button[aria-label="Stop response"]')};
      },previous);
      if (state.title && state.title!==lastTitle) {
        await note('Gemini 网页：'+state.title);
        lastTitle=state.title;
      }
      if (state.done) {
        if (record && !session.url) throw Error('网页已回复，但未能保存会话地址。');
        break;
      }
      if (Date.now()-start>timeout) throw Error('等待 Gemini '+mode+' 回复超时，未重新提交。');
      if (Date.now()-lastUpdate>=20000) {
        await note('本机网关：已等待网页回复 '+Math.round((Date.now()-start)/1000)+' 秒。');
        lastUpdate=Date.now();
      }
      await new Promise(resolve=>setTimeout(resolve,350));
    }
    const [currentMode, currentLevel] = await selection();
    if (currentMode !== mode || (session.url && await page.url()!==session.url)) throw Error('模型或会话在回复期间改变');
    if (currentLevel && currentLevel.toLowerCase() !== effort) throw Error('Thinking level changed during reply');
    return await page.evaluate(() => {
      const root = Array.from(document.querySelectorAll('model-response')).at(-1)?.querySelector('message-content .markdown');
      if (!root) return '';
      const blocks = root.querySelectorAll('pre');
      if (blocks.length === 1) return (blocks[0].querySelector('code') ?? blocks[0]).textContent.trim();
      return Array.from(root.children).filter(e=>!e.matches('.attachment-container')).map(e=>{
        if (!e.querySelector('button,source-footnote')) return e.innerText;
        const copy=e.cloneNode(true);
        copy.querySelectorAll('button,source-footnote,.attachment-container').forEach(n=>n.remove());
        copy.querySelectorAll('br').forEach(n=>n.replaceWith('\n'));
        return copy.textContent;
      }).join('\n').trim();
    });
  }
  const parsed = await structuredReply(prompt, exchange, async()=>{
    if (session.url) {
      await note('Gemini 网页返回服务错误，正在重新连接原会话后重试一次。');
      await openGemini(page,session.url);
      if (await page.url()!==session.url) throw Error('重连后无法打开原 Gemini 会话');
      await verifySelection();
    }
    await attachImages();
  },()=>fs.writeFile(path.join(path.dirname(file),'upstream-failed'),''),{compact,toolDefs,delivery,endMarker});
  await responseDetails(page,note,mode,effort,summaries);
  session.checkedMode=mode;
  session.checkedEffort=effort;
  await save();
  await fs.writeFile(path.join(path.dirname(file), 'answer.txt'), JSON.stringify(parsed), 'utf8');
  if (!space) await task.finish({keep:record?[page.label]:[]});
}

function checkSchema(value,schema,field) {
  if (!schema || typeof schema!=='object') return;
  const type=Array.isArray(value)?'array':value===null?'null':typeof value;
  const types=[schema.type].flat();
  if(schema.type && !types.includes(type) && !(Number.isInteger(value)&&types.includes('integer')))
    throw Error(field+' has incorrect type');
  if(schema.enum && !schema.enum.some(v=>JSON.stringify(v)===JSON.stringify(value))) throw Error(field+' is outside enum');
  if(type==='object'){
    for(const key of schema.required??[]) if(!Object.hasOwn(value,key)) throw Error(field+' missing '+key);
    for(const [key,v] of Object.entries(value)){
      const known=Object.hasOwn(schema.properties??{},key);
      if(!known && schema.additionalProperties===false) throw Error(field+' unknown '+key);
      checkSchema(v,known?schema.properties[key]:schema.additionalProperties,field+'.'+key);
    }
  }else if(type==='array') for(const v of value) checkSchema(v,schema.items,field+'[]');
}

export async function structuredReply(prompt, exchange, recover=async()=>{}, markStalled=async()=>{},{compact=false,toolDefs,delivery,endMarker}={}) {
  const end=endMarker || 'END_LITERAL_'+randomUUID();
  const format = 'For answers and short tool calls use a single valid JSON object in a fenced code block. For multiline commands or patches use one fenced plaintext block: line 1 is a compact JSON header, e.g. {"tool":"exec_command","arguments":{},"literal_argument":"cmd"}. For custom tool input use {"tool":"apply_patch","literal_input":true}. After the header put the exact raw command or patch starting on line 2, with ordinary newlines, quotes and backslashes, and then the following exact terminator on its own last line: '+end+'. Keep other arguments in the header. Prefer small, independently verifiable edits.';
  const execution = 'One tool per response is a serialization rule. Codex sends its result and requests the next action automatically. When the user requested execution and work remains, issue the next available tool call. Do not infer a tool budget from conversation length or past assistant statements. Respect tool denials, user stop requests and approval requirements.';
  const permissions = 'For an already requested computer action, keep permission handling inside this turn. Use the normal tool for writable paths. When permission is required and policy permits, return the advertised request_permissions tool with the minimal scope and a specific reason, or use the execution tool approval fields if supported. Codex presents the native approval UI. Read its actual result: a turn-scoped grant covers matching actions for the rest of this turn. Proceed immediately within that grant and verify completion. Respect partial grants, cancellation and denial; never repeat a rejected request. A chat reply is not an OS permission grant. Ask separately only for genuinely missing task decisions.';
  const files='For any call that will create or overwrite named local files, also include "writes":["absolute path", ...] in its JSON or literal header. Codex checkpoints those files before executing the call; do not perform your own backup. When finishing a requested file delivery include "artifacts":["absolute path", ...] next to "answer" for native verification. Check the requested contents and constraints before reporting success. A blocked task should report the actual blocker. On an explicit undo request use the sealed checkpoint command from this conversation. Never invent file paths or receipts.';
  const first = compact ? 'Next input, same contract. Literal terminator: '+end+'\n'+prompt :
    prompt.replace('Return exactly one JSON object on one line as plain text, without markdown or commentary.', execution+' '+permissions+' '+files+' '+format);
  let reply = await exchange(first, 210000);
  let upstreamRetried=false;
  let formatRetried=false;
  let continuationRetries=0;
  while (true) {
    let parsed;
    try {
      const source=reply.trim().replace(/^```(?:json|text|plaintext)?[^\S\n]*\r?\n|\r?\n```$/gi,'');
      parsed=parseEnvelope(source,end);
      const answer = typeof parsed?.answer === 'string' && !('tool' in parsed);
      const args = parsed?.arguments;
      const call = typeof parsed?.tool === 'string' && parsed.tool.length > 0 &&
        (typeof parsed.input === 'string' || (args && typeof args === 'object' && !Array.isArray(args)));
      if (!answer && !call) throw Error('Expected an answer or tool call');
      if(answer && toolDefs?.exec_command && delivery?.formats?.length && !delivery.blocker &&
          !parsed.needs_input && delivery.formats.some(ext=>!parsed.artifacts?.some(p=>typeof p==='string' && p.toLowerCase().endsWith('.'+ext)))) {
        throw Error('User requested files. Call an available tool now to perform pending work. Instructions or commands in prose do not fulfill the request. After creating files, return their absolute paths in artifacts. If an essential task decision is genuinely missing, use needs_input with the specific question.');
      }
      if(call && toolDefs) {
        if(!Object.hasOwn(toolDefs,parsed.tool)) throw Error('Tool not offered: '+parsed.tool);
        const def=toolDefs[parsed.tool];
        if(def.type==='function') checkSchema(parsed.arguments,def.parameters,'arguments');
        else if(typeof parsed.input!=='string') throw Error('Missing custom tool input');
      }
      for(const key of ['writes','artifacts']) if(Object.hasOwn(parsed,key) &&
        (!Array.isArray(parsed[key]) || !parsed[key].length || parsed[key].length>32 ||
         parsed[key].some(p=>typeof p!=='string'|| !/^[A-Za-z]:[\\/]/.test(p) || /[\n\r\0]/.test(p))))
        throw Error('Invalid '+key+' paths');
      if(answer && toolDefs?.exec_command && !parsed.artifacts &&
          /(?:已(?:成功)?(?:创建|生成|写入|保存)|(?:created|saved|wrote|generated))[^。\n]{0,110}\.(?:docx|pdf|mp4|html|txt|xlsx|pptx)\b/i.test(parsed.answer))
        throw Error('Provide artifacts for the files claimed in the answer');
    } catch (err) {
      const upstreamError=/^(?:Sorry, something went wrong|I seem to be encountering an error|I encountered an error doing what you asked|I['’]m having a hard time fulfilling your request)\b/i.test(reply.trim());
      if (upstreamError) {
        if (upstreamRetried) {
          await markStalled();
          throw Error('Gemini 网页重连后仍返回服务错误，本轮没有新的工具调用。');
        }
        upstreamRetried=true;
        await recover();
        reply=await exchange(first,90000);
        continue;
      }
      if (formatRetried) {
        await markStalled();
        throw Error('Gemini 网页未返回完整的工具或回答格式，本轮没有新的工具调用。');
      }
      formatRetried=true;
      const definition=parsed?.tool && Object.hasOwn(toolDefs??{},parsed.tool) ? JSON.stringify(toolDefs[parsed.tool]) : '';
      reply = await exchange('Correct the last reply. '+(err instanceof SyntaxError?'Invalid JSON':err.message)+'. No tool was executed. '+definition+' '+files+' '+format, 90000);
      continue;
    }
    const answer=parsed.answer;
    const mentionsBudget=typeof answer==='string' &&
      /(?:工具调用|tool.calls?).{0,42}(?:用尽|耗尽|已满|上限|限制|limit|exhausted|0 remaining)/i.test(answer);
    // A numeric claim is sufficient even if the model omits "reply continue".
    // The correction still allows a real tool denial or a completed task.
    const budgetHandoff=mentionsBudget && /(?:\b0\s+remaining\b|剩余\s*0\s*次|(?:回复|发送|输入|说).{0,10}继续|下一轮.{0,16}(?:执行|继续)|尚待执行|尚未执行|无法(?:在本轮|在当前轮次)?继续|(?:reply|send|say).{0,12}continue)/i.test(answer);
    const permissionHandoff=typeof answer==='string' &&
      /(?:回复|输入|发送|等待你|等你)[“「"'\s]*(?:确认|同意|授权|批准)[”」"'\s]*(?:后|即可|，我|我)[^。\n]{0,40}(?:写入|创建|保存|执行|安装|修改|删除|移动)|(?:请确认是否允许|是否允许我|请授权我)[^。\n]{0,40}(?:写入|创建|保存|执行|安装|修改)|(?:reply|respond)\s+(?:with\s+)?["']?(?:confirm|yes)["']?[^.\n]{0,45}(?:write|create|save|run|install)/i.test(answer);
    const zeroTools=typeof answer==='string' && /\b(?:0|zero|no) (?:more )?tool calls? (?:remain|left|available)/i.test(answer);
    if (!budgetHandoff && !permissionHandoff && !zeroTools) return parsed;
    if (permissionHandoff && continuationRetries===2)
      throw Error('Gemini 没有提交可执行的权限请求或后续操作。');
    if (continuationRetries===2) {
      await markStalled();
      throw Error('Gemini 连续要求手动续轮，未提供下一项工具调用。');
    }
    continuationRetries++;
    reply=await exchange(execution+' '+permissions+' Check the most recent actual tool result. If it blocks execution or user input is required, explain the specific blocker without asking for a generic continue. Otherwise return the next authorized action. '+format,90000);
  }
}

export function parseEnvelope(source,end) {
  // Gemini sometimes renders the closing Markdown fence inside the <pre>,
  // immediately after our exact per-request delimiter. Leave the command intact.
  if (source.endsWith('\n'+end+'```')) source=source.slice(0,-3);
  const separator=source.indexOf('\n');
  if(separator<0 || !source.endsWith('\n'+end)) {
    const value=JSON.parse(source);
    if(value && (Object.hasOwn(value,'literal_argument')||Object.hasOwn(value,'literal_input'))) throw Error('Missing literal payload');
    return value;
  }
  const header=JSON.parse(source.slice(0,separator));
  const extra={};
  if(header && Object.hasOwn(header,'writes')) {extra.writes=header.writes; delete header.writes;}
  const literal=source.slice(separator+1,-end.length-1).replace(/\r$/,'');
  if(!literal || !header || typeof header.tool!=='string') throw Error('Incomplete literal tool call');
  if(header.literal_input===true && Object.keys(header).length===2) return {tool:header.tool,input:literal,...extra};
  const {tool,arguments:args,literal_argument:key}=header;
  if(typeof key!=='string'|| !/^[A-Za-z][A-Za-z0-9_]*$/.test(key)|| !args || typeof args!=='object' ||
      Array.isArray(args)||Object.hasOwn(args,key)||Object.keys(header).length!==3) throw Error('Invalid literal argument header');
  return {tool,arguments:{...args,[key]:literal},...extra};
}
