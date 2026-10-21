#!/usr/bin/env node
// In-memory DOM contract: no external browser dependency and no credentials.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const template = fs.readFileSync('app/templates/admin_user_form.html', 'utf8');
const matches = [...template.matchAll(/<script>([\s\S]*?)<\/script>/g)];
assert.equal(matches.length, 1, 'exactly one password UI script');
const code = matches[0][1];
function makeGroup(name) {
  const input = {name, type:'password', focusCalls:0, focus(){this.focusCalls++}};
  const button = {textContent:'Show', attrs:{}, addEventListener(event, cb) {
    assert.equal(event,'click'); this.onClick=cb;
  }, setAttribute(name,value){this.attrs[name]=value}};
  const group = {querySelector(sel) {
    if(sel==='[data-panel-password-field]') return input;
    if(sel==='[data-panel-password-toggle]') return button;
    return null;
  }};
  return {group, input, button};
}
for (const hash of ['', '#panel-user-password']) {
  const a = makeGroup('password');
  const b = makeGroup('password_confirm');
  const document = {
    querySelectorAll(sel) {assert.equal(sel,'[data-panel-password-group]');return [a.group,b.group]},
    querySelector(sel) {
      assert.equal(sel,'[data-panel-password-field][name="password"]');return a.input;
    },
  };
  vm.runInNewContext(code, {document, window:{location:{hash}}}, {timeout:1000});
  assert.equal(a.input.type,'password');
  assert.equal(b.input.type,'password');
  assert.equal(a.input.focusCalls, hash ? 1 : 0);
  a.button.onClick();
  assert.equal(a.input.type,'text', 'only newly typed password becomes visible');
  assert.equal(b.input.type,'password', 'confirmation remains hidden');
  assert.equal(a.button.attrs['aria-pressed'],'true');
  assert.equal(a.button.textContent,'Hide');
  a.button.onClick();
  assert.equal(a.input.type,'password');
  assert.equal(a.button.attrs['aria-pressed'],'false');
  b.button.onClick();
  assert.equal(a.input.type,'password');
  assert.equal(b.input.type,'text');
  b.button.onClick();
  assert.equal(b.input.type,'password');
}
console.log('PASS: show/hide activates only on explicit click, per field, and reset link focuses password');
