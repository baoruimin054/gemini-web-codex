import assert from 'node:assert/strict';
import test from 'node:test';
import {geminiPageStatus, geminiProfile} from './gemini_site.mjs';

test('first run accepts a signed-in Gemini editor and separates sign-in and region errors', () => {
  function visit(hostname, selectors=[], text='') {
    globalThis.location = {hostname};
    globalThis.document = {
      body: {innerText:text},
      querySelector: query => selectors.some(selector => query.includes(selector)) ? {} : null,
    };
    return geminiPageStatus();
  }
  try {
    assert.equal(visit('accounts.google.com'), 'login_required');
    assert.equal(visit('gemini.google.com',['ServiceLogin','contenteditable']), 'login_required');
    assert.equal(visit('gemini.google.com',['contenteditable']), null);
    assert.equal(visit('gemini.google.com',['Google Account','contenteditable']), 'ready');
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
