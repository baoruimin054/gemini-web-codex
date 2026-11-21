import {randomUUID} from 'node:crypto';
import {geminiApp, geminiPageStatus, geminiProfile} from './gemini_site.mjs';

// Inspect only login indicators. Do not read account identifiers, credentials or chats.
export async function checkLogin() {
  const profile = await geminiProfile();
  const task = await taskSpace('Gemini Codex login check ' + randomUUID(), {profileId: profile?.id});
  try {
    const page = (await task.pages())[0] ?? await task.newPage();
    await page.goto(geminiApp, {waitUntil:'commit', timeout:18000});
    try {
      await page.waitForFunction(geminiPageStatus, null, {timeout:12000});
    } catch (error) {
      if (!/timed out/i.test(String(error))) throw error;
    }
    return {status:await page.evaluate(geminiPageStatus) ?? 'page_unavailable', profile:profile?.id};
  } finally {
    await task.finish({keep:[]});
  }
}
