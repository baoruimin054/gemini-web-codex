import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

// A space must match the locally saved Codex binding before it can be closed.
export async function closeCodexSpaces(stateUrl, foregroundTitle = '') {
  const directory = path.join(fileURLToPath(stateUrl), 'conversations');
  const records = [];
  for (const name of await fs.readdir(directory).catch(() => [])) {
    if (!name.endsWith('.json')) continue;
    try {
      const record = JSON.parse(await fs.readFile(path.join(directory, name), 'utf8'));
      if (Number.isInteger(record.space) && record.name?.startsWith('Codex Gemini '))
        records.push({record, file: path.join(directory, name)});
    } catch {
      // Leave an unrecognizable record and its browser space untouched.
    }
  }

  let failures = 0;
  let deferred = 0;
  const seen = [];
  for (const entry of await listTaskSpaces()) {
    const saved = records.find(({record}) => record.space === entry.id && record.name === entry.name);
    const binding = saved?.record;
    if (saved) seen.push(path.basename(saved.file));
    if (!['agent', 'user'].includes(entry.ownership)) continue;
    const accountCheck = entry.createdBy === 'agent' && entry.ownership === 'agent' &&
      ['Gemini usage monitor', 'GemCodex account startup'].includes(entry.name);
    if (!binding && !accountCheck) continue;
    if (entry.name && entry.name === foregroundTitle) {
      deferred++;
      continue;
    }
    let task;
    try {
      task = entry.ownership === 'user' ? await claimTaskSpace(entry.id) : await taskSpace(entry.id);
      const tabs = await task.tabs();
      const tab = tabs.length === 1 ? tabs[0] : null;
      const matches = binding
        ? (binding.url ? tab?.url === binding.url : tab && tab.targetId === binding.targetId &&
          /^(?:about:blank|chrome:\/\/newtab\/|https:\/\/gemini\.google\.com(?:\/[^\s]*)?)$/.test(tab.url))
        : /^https:\/\/gemini\.google\.com\/(?:u\/\d+\/)?(?:app|usage)(?:[?#]|$)/.test(tab?.url ?? '');
      if (!matches) {
        if (entry.ownership === 'user') await task.finish({keep: 'all'});
        console.log('Kept space with changed tabs:', entry.id);
        continue;
      }
      const page = tab.label ? task.page(tab.label) : await task.adopt(tab.page);
      // Browser restarts may restore this exact Codex tab as user-owned.
      if (binding?.url && tab.openedBy === 'unknown') await page.close();
      await task.finish({keep: []});
      let remaining = true;
      for (let attempt = 0; attempt < 12; attempt++) {
        remaining = (await listTaskSpaces()).some(space => space.id === entry.id && space.name === entry.name);
        if (!remaining) break;
        await new Promise(resolve => setTimeout(resolve, 200));
      }
      if (remaining) {
        deferred++;
        console.log('Codex space still present after closing:', entry.id);
      } else {
        if (saved) {
          delete binding.space;
          delete binding.targetId;
          const temporary = saved.file + '.cleanup';
          await fs.writeFile(temporary, JSON.stringify(binding), 'utf8');
          await fs.rename(temporary, saved.file);
        }
        console.log('Closed Codex space:', entry.id);
      }
    } catch (error) {
      failures++;
      console.log('Could not close Codex space:', entry.id, String(error).slice(0, 180));
      if (task && entry.ownership === 'user') {
        try { await task.finish({keep: 'all'}); } catch { /* already finished */ }
      }
    }
  }
  if (failures) throw Error('Some Codex browser spaces could not be closed');
  return {deferred, seen};
}

// An agent-owned blank space identifies one running Ego service across CLI calls.
export async function cleanupPass(stateUrl, probes, probeName, foregroundTitle = '') {
  const spaces = await listTaskSpaces();
  const candidate = probes.find(item => spaces.some(space =>
    (item.id == null || space.id === item.id) && space.name === item.name && space.createdBy === 'agent'));
  let probe = candidate && spaces.find(space => space.name === candidate.name);
  if (probe) probe = {id: probe.id, name: probe.name};
  if (!probe) {
    const task = await taskSpace(probeName);
    probe = {id: task.spaceId, name: task.name};
  }
  return {probe, ...(await closeCodexSpaces(stateUrl, foregroundTitle))};
}

export async function closeCleanupProbes(probes) {
  const spaces = await listTaskSpaces();
  const closed = [];
  for (const probe of probes) {
    const entry = spaces.find(space => (probe.id == null || space.id === probe.id) && space.name === probe.name &&
      space.createdBy === 'agent' && space.ownership === 'agent');
    if (!entry) continue;
    const task = await taskSpace(entry.id);
    await task.finish({keep: []});
    closed.push(probe.name);
  }
  return closed;
}
