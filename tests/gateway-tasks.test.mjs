import assert from 'node:assert/strict';
import test from 'node:test';
import { SessionAccessCounter } from 'supergateway/dist/lib/sessionAccessCounter.js';
import { Gateway, usesTasks } from '../runtime/gateway.mjs';

const quiet = { info() {}, error() {} };

for (const [name, message, initializeResult] of [
  ['client capability', { method:'initialize',params:{capabilities:{tasks:{}}} }, false],
  ['server capability', { id:1,result:{capabilities:{tasks:{requests:{tools:{call:true}}}}} }, true],
  ['task request', { id:2,method:'tools/call',params:{task:{ttl:60000}} }, false],
  ['task creation result', { id:2,result:{task:{taskId:'task-1',status:'working'}} }, false],
]) {
  test(`task lifetime survives its completed RPC: ${name}`, t => {
    t.mock.timers.enable({ apis:['setTimeout'] });
    const expired=[];
    const counter=new SessionAccessCounter(10,key=>expired.push(key),quiet);
    const gateway=Object.create(Gateway.prototype);
    Object.assign(gateway,{ idleCounters:new Map([['fixture',counter]]),event(){} });
    const record={ key:'owned',profile:'fixture',closing:false };
    const releaseRpc=gateway.hold(record,'RPC');
    gateway.protectTasks(record,message,initializeResult);
    gateway.protectTasks(record,message,initializeResult);
    releaseRpc();
    t.mock.timers.tick(100);
    assert.equal(record.taskPinned,true);
    assert.deepEqual(expired,[],'RPC response is not completion of background task');
    // Explicit close remains able to clear the persistent lease.
    counter.clear(record.key,false,'explicit close');
    assert.equal(counter.sessions.size,0);
  });
}

test('ordinary sessions still expire; malformed/absent Tasks do not pin them', t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const expired=[];
  const gateway=Object.create(Gateway.prototype);
  const counter=new SessionAccessCounter(10,key=>expired.push(key),quiet);
  Object.assign(gateway,{idleCounters:new Map([['fixture',counter]]),event(){}});
  const record={key:'ordinary',profile:'fixture',closing:false};
  for (const message of [{method:'initialize',params:{capabilities:{}}},
      {id:2,result:{content:[]}}, {params:{task:null}}]) {
    assert.equal(usesTasks(message),false);
    gateway.protectTasks(record,message);
  }
  gateway.hold(record,'RPC')();
  t.mock.timers.tick(100);
  assert.deepEqual(expired,['ordinary']);
});
