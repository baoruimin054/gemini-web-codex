import fs from 'node:fs/promises';
import {geminiProfile} from './gemini_site.mjs';

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

export async function collect(file) {
  const name = 'Gemini usage monitor';
  const existing = (await listTaskSpaces()).find(s => s.name === name);
  if (existing && existing.ownership !== 'agent') throw Error('额度页面正在使用中');
  const task = existing ? await taskSpace(existing.id) : await taskSpace(name, {profileId:(await geminiProfile())?.id});
  try {
    const page = (await task.pages())[0] ?? await task.newPage();
    await page.goto('https://gemini.google.com/usage?hl=en');
    await page.waitForFunction(() => [...document.querySelectorAll('li')].some(e =>
      /Weekly limit/i.test(e.innerText) && /% used/i.test(e.innerText)), null, {timeout:25000});
    const rows = await page.evaluate(() => [...document.querySelectorAll('li')].map(e => e.innerText.trim()));
    const result = parseRows(rows);
    await fs.writeFile(file+'.tmp', JSON.stringify(result));
    await fs.rename(file+'.tmp',file);
  } finally {
    await task.finish({keep:[]});
  }
}
