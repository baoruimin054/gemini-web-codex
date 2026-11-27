import {createHash, randomUUID} from 'node:crypto';
import fs from 'node:fs/promises';

export function accountPage(slot, url='https://gemini.google.com/app') {
  const target = new URL(url);
  if (target.origin !== 'https://gemini.google.com' || !Number.isSafeInteger(slot) || slot < 0)
    throw Error('无效的 Gemini 账号地址');
  target.pathname = '/u/' + slot + target.pathname.replace(/^\/u\/\d+(?=\/)/, '');
  return target.href;
}

function readAccountIdentity() {
  if (location.origin !== 'https://gemini.google.com') return null;
  const link = [...document.querySelectorAll('a[href^="https://accounts.google.com/SignOutOptions"]')]
    .find(e => e.checkVisibility());
  const label = link?.getAttribute('aria-label')?.replace(/[\u200e\u200f\u202a-\u202e\u2066-\u2069]/g,'').trim();
  const email = label?.match(/[\w.!#$%&'*+/=?^\x60{|}~-]+@[\w.-]+\.[a-z]{2,}/i)?.[0];
  if (!email) return null;
  const nameStart = label.search(/[:：]/);
  const nameEnd = label.lastIndexOf(email);
  const displayName = nameStart !== -1 && nameEnd > nameStart
    ? label.slice(nameStart+1,nameEnd).replace(/\s*[（(]\s*$/,'').replace(/\s+/g,' ').trim() : '';
  return {email, displayName:displayName.includes('@') ? '' : displayName,
          slot:Number(location.pathname.match(/^\/u\/(\d+)\//)?.[1] ?? 0)};
}

export async function pageAccount(page, timeout=0) {
  if (timeout) {
    // A mounted mode picker can precede the account avatar during navigation.
    try { await page.waitForFunction(readAccountIdentity,null,{timeout}); }
    catch (error) { if (!/timed out/i.test(String(error))) throw error; }
  }
  const data = await page.evaluate(readAccountIdentity);
  if (!data) return null;
  const [name, domain] = data.email.toLowerCase().split('@');
  return {id:createHash('sha256').update(data.email.toLowerCase()).digest('hex'),
          label:name.slice(0,2)+'•••@'+domain, displayName:data.displayName || '', slot:data.slot};
}

export async function checkAccount(page, selected, previousId, timeout=0) {
  const account = await pageAccount(page,timeout);
  if (!account) throw Error('无法确认当前 Gemini 登录账号。请在左下角刷新或登录后重试。消息尚未发送。');
  if (selected && account.id !== selected.id)
    throw Error('网页账号与 GemCodex 所选账号不一致。请在左下角重新选择。消息尚未发送。');
  if (previousId && account.id !== previousId)
    throw Error('此聊天属于另一个 Gemini 账号。请切回原账号，或新建聊天。消息尚未发送。');
  return account;
}

export async function storeAccountCache(file, result) {
  const temporary = file + '.' + randomUUID() + '.tmp';
  try {
    await fs.writeFile(temporary, JSON.stringify(result), 'utf8');
    await fs.rename(temporary, file);
  } finally {
    await fs.rm(temporary,{force:true});
  }
}
