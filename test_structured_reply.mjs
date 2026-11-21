import assert from 'node:assert/strict';
import test from 'node:test';
import { hasSubmittedTurn, openGemini, structuredReply } from './browser.mjs';

test('loading an older Gemini message does not confirm a new submission', () => {
  const original = globalThis.document;
  try {
    globalThis.document = {querySelectorAll: () => [{textContent:'Turn old-message'}]};
    assert.equal(hasSubmittedTurn('Turn new-message'), false);
    globalThis.document = {querySelectorAll: () => [{textContent:'Turn old-message'},
                                                    {textContent:'Turn new-message'}]};
    assert.equal(hasSubmittedTurn('Turn new-message'), true);
  } finally {
    if (original === undefined) delete globalThis.document;
    else globalThis.document = original;
  }
});

test('a committed Gemini page can become ready without a full load event', async () => {
  const calls = [];
  const page = {
    async goto(url, options) { calls.push({url, options}); },
    async waitForSelector(selector) { calls.push({selector}); },
  };
  await openGemini(page, 'https://gemini.google.com/app/test');
  assert.equal(calls[0].options.waitUntil, 'commit');
  assert.match(calls[1].selector, /Open mode picker/);
});

const outage = 'I encountered an error doing what you asked. Could you try again?';
const cutoff = JSON.stringify({answer:'受限于当前回合工具调用额度已耗尽（0 remaining），尚待执行以下最后一步收尾'});
const action = {tool:'exec_command',arguments:{cmd:'Get-Location'}};
function fixture(replies) {
  const counts = {recover:0, stalled:0, sent:0};
  const run = () => structuredReply('task', async () => {
    assert.ok(counts.sent < replies.length);
    return replies[counts.sent++];
  }, async () => { counts.recover++; }, async () => { counts.stalled++; });
  return {counts, run};
}
const blocked=JSON.stringify({answer:'本轮可用工具调用次数已耗尽（0 remaining），无法在本轮继续写入修复补丁与运行编码。'});
for (const prefix of [[blocked,outage],[cutoff,outage], ['invalid',outage], [outage,'invalid']]) {
  test('recover then dispatch one call '+prefix[0].slice(0,16), async () => {
    const {counts,run} = fixture([...prefix,JSON.stringify(action)]);
    assert.deepEqual(await run(), action);
    assert.deepEqual(counts,{recover:1,stalled:0,sent:3});
  });
}
for (const sequence of [[outage,outage],['invalid','invalid'],[cutoff,cutoff,cutoff]]) {
  test('signal bounded session recovery '+sequence[0].slice(0,16),async()=>{
    const {counts,run} = fixture(sequence);
    await assert.rejects(run);
    assert.equal(counts.stalled,1);
    assert.equal(counts.sent,sequence.length);
  });
}
test('explicit approval requirement is returned',async()=>{
  const answer={answer:'写入遭拒，请批准目标目录。'};
  assert.deepEqual(await fixture([JSON.stringify(answer)]).run(),answer);
});
test('a bare tool budget claim without a continuation phrase is checked',async()=>{
  const {run}=fixture([JSON.stringify({answer:'工具调用额度已耗尽（0 remaining）。'}),JSON.stringify(action)]);
  assert.deepEqual(await run(),action);
});

const handoff=JSON.stringify({answer:'尚未在桌面创建文件，你可以直接复制以下正文，或回复确认后由我继续为你写入。'});
test('a file permission handoff becomes a native permission tool call',async()=>{
  const request={tool:'request_permissions',arguments:{reason:'保存已请求的文件',permissions:{file_system:{write:['C:/test/output']}}}};
  assert.deepEqual(await fixture([handoff,JSON.stringify(request)]).run(),request);
});
test('a granted permission can proceed directly to execution',async()=>{
  assert.deepEqual(await fixture([handoff,JSON.stringify(action)]).run(),action);
});
test('a denial is final and a missing task decision is preserved',async()=>{
  for(const answer of ['用户已拒绝桌面写入权限。','发现同名文件，请选择覆盖还是另存。']){
    assert.deepEqual(await fixture([JSON.stringify({answer})]).run(),{answer});
  }
});

test('compact messages omit bootstrap and repair only the offending schema',async()=>{
  const prompts=[];
  const toolDefs={exec_command:{type:'function',parameters:{type:'object',required:['cmd'],additionalProperties:false,properties:{cmd:{type:'string'}}}}};
  const responses=[{tool:'exec_command',arguments:{cmd:3}},action];
  const result=await structuredReply('delta',async text=>{prompts.push(text);return JSON.stringify(responses.shift());},undefined,undefined,{compact:true,toolDefs});
  assert.deepEqual(result,action);
  assert.ok(prompts[0].length<150);
  assert.ok(prompts[1].includes('incorrect type'));
  assert.ok(prompts[1].includes('required'));
});
test('a claimed file delivery carries a verification manifest',async()=>{
  const toolDefs={exec_command:{type:'function',parameters:{type:'object'}}};
  const promised={answer:'已生成 report.docx'};
  const final={...promised,artifacts:['C:/test/report.docx']};
  const responses=[promised,final];
  assert.deepEqual(await structuredReply('task',async()=>JSON.stringify(responses.shift()),undefined,undefined,{toolDefs}),final);
});
