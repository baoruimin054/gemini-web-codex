import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {geminiPageStatus, geminiProfile} from './gemini_site.mjs';
import {accountPage, checkAccount} from './gemini_account.mjs';
import {collect} from './quota.mjs';

test('account routing retains a Gemini conversation ID and rejects external URLs', () => {
  assert.equal(accountPage(2,'https://gemini.google.com/u/1/app/chat123'),
               'https://gemini.google.com/u/2/app/chat123');
  assert.throws(()=>accountPage(2,'https://accounts.google.com/'),/无效/);
});

test('bound conversations cannot send using a different signed-in account', async () => {
  const page = {evaluate: async () => ({email:'Example@gmail.com',slot:1})};
  const account = await checkAccount(page,null,null);
  assert.equal(account.label,'ex•••@gmail.com');
  await assert.rejects(checkAccount(page,null,'a'.repeat(64)),/此聊天属于另一个/);
  await assert.rejects(checkAccount(page,{id:'a'.repeat(64)},null),/所选账号不一致/);
  assert.deepEqual(await checkAccount(page,{id:account.id},account.id),account);
});

test('the visible avatar provides a display name or a masked email fallback', async () => {
  const labels = ['Google Account: Example Person\n(example@gmail.com)', 'Google 帐号：example@gmail.com'];
  globalThis.location = {origin:'https://gemini.google.com',pathname:'/u/1/app'};
  try {
    for (const [index,label] of labels.entries()) {
      globalThis.document = {querySelectorAll:()=>[
        {checkVisibility:()=>false,getAttribute:()=> 'Google Account: Hidden (hidden@gmail.com)'},
        {checkVisibility:()=>true,getAttribute:()=>label},
      ]};
      const account = await checkAccount({evaluate:async read=>read()},null,null);
      assert.equal(account.displayName,index===0?'Example Person':'');
      assert.equal(account.label,'ex•••@gmail.com');
      assert.equal(account.slot,1);
    }
  } finally {
    delete globalThis.location;
    delete globalThis.document;
  }
});

test('first run accepts a signed-in Gemini editor and separates sign-in and region errors', () => {
  function visit(hostname, selectors=[], text='', pathname='/app', requireEditor=true) {
    globalThis.location = {hostname,pathname};
    globalThis.document = {
      body: {innerText:text},
      querySelector: query => selectors.some(selector => query.includes(selector)) ? {} : null,
    };
    return geminiPageStatus(requireEditor);
  }
  try {
    assert.equal(visit('accounts.google.com'), 'login_required');
    assert.equal(visit('accounts.google.com',[],'','/v3/signin/challenge/totp'), 'verification_required');
    assert.equal(visit('www.google.com',[],'','/sorry/index'), 'verification_required');
    assert.equal(visit('gemini.google.com',['ServiceLogin','contenteditable']), 'login_required');
    assert.equal(visit('gemini.google.com',['contenteditable']), null);
    assert.equal(visit('gemini.google.com',['Google Account','contenteditable']), 'ready');
    assert.equal(visit('gemini.google.com',['Google Account'],'','/app',false), 'ready');
    assert.equal(visit('gemini.google.com',[], 'Gemini isn’t currently supported in your country.'), 'region_unavailable');
    assert.equal(visit('example.org',['Google Account','contenteditable']), null);
  } finally {
    delete globalThis.location;
    delete globalThis.document;
  }
});

test('first run, gateway and quota check can use Ego’s default profile on a clean install', async () => {
  try {
    globalThis.profiles = async () => [{id:'Other'}, {id:'Default',isDefault:true}];
    assert.equal((await geminiProfile()).id, 'Default');
    globalThis.profiles = async () => [{id:'Default',isDefault:true}, {id:'Profile 1'}];
    assert.equal((await geminiProfile()).id, 'Profile 1');
  } finally {
    delete globalThis.profiles;
  }
});

test('account checks discover both logins without a model menu and distinguish signed-out and verification pages', async () => {
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'gemcodex-account-'));
  const file=path.join(directory,'quota.json');
  let status='ready';
  let slot=0;
  const page={
    async goto(url) {
      const requested=Number(new URL(url).pathname.split('/')[2]);
      slot=requested<2 ? requested : 0;
    },
    async waitForFunction() {},
    async evaluate(inspect) {
      if (inspect===geminiPageStatus) return status;
      return {email:`login${slot}@example.com`,displayName:`Account ${slot}`,slot};
    },
    async waitForSelector() { throw Error('Model picker unavailable'); },
  };
  globalThis.listTaskSpaces=async()=>[];
  globalThis.profiles=async()=>[];
  globalThis.taskSpace=async()=>({pages:async()=>[page],finish:async()=>{}});
  try {
    const saved=async()=>JSON.parse(await fs.readFile(file,'utf8'));
    await collect(file,null,true,false);
    const both=await saved();
    assert.equal(both.status,'ready');
    assert.deepEqual(both.accounts.map(account=>account.displayName),['Account 0','Account 1']);
    await collect(file,both.accounts[1],true,false);
    assert.equal((await saved()).account.id,both.accounts[1].id);
    for (status of ['login_required','verification_required']) {
      await collect(file,null,true,false);
      assert.equal((await saved()).status,status);
      assert.equal((await saved()).account,null);
      assert.deepEqual((await saved()).accounts,[]);
    }
  } finally {
    delete globalThis.listTaskSpaces;
    delete globalThis.profiles;
    delete globalThis.taskSpace;
    await fs.rm(file,{force:true});
    await fs.rmdir(directory);
  }
});
