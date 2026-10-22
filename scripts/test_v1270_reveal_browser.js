"use strict";
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
const page = fs.readFileSync("app/templates/admin_users.html", "utf8");
const scripts = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(x => x[1]);
const source = scripts.find(s => s.includes("data-vault-reauth") && s.includes("const expiresAt"));
assert(source, "Vault reveal script not found");
new vm.Script(source);

function element(visible = true) {
  return {
    hidden: !visible, value: "", type: "password", textContent: "", disabled: false,
    listeners: {}, isConnected: true,
    addEventListener(name, fn) { this.listeners[name] = fn; },
    focus() { active = this; },
  };
}
let active = null;
let now = 0, interval = null, expiry = null;
const elements = {
  "[data-vault-reauth]": element(),
  "[data-vault-reauth-step]": element(),
  "[data-vault-result]": element(false),
  "[data-vault-secret-value]": element(),
  "[data-vault-error]": element(false),
  "[data-vault-csrf]": element(),
  "[data-vault-countdown]": element(),
  "[data-vault-confirm]": element(),
  "[data-vault-user-label]": element(),
  "[data-vault-toggle]": element(),
  "[data-vault-close]": element(),
};
elements["[data-vault-csrf]"].value = "csrf";
const overlay = element(false);
overlay.querySelector = key => {
  assert(elements[key], "Unknown selector " + key);
  return elements[key];
};
const button = element();
button.dataset = {vaultRevealUserId:"5", vaultRevealUsername:"operator"};
const documentMock = {
  hidden:false, listeners:{},
  querySelector:s => s==="[data-vault-overlay]" ? overlay : null,
  querySelectorAll:s => s==="[data-vault-reveal-user-id]" ? [button] : [],
  addEventListener(n,f){this.listeners[n]=f;},
};
const windowMock = {STREAMFORGE_APP_ROOT:"",listeners:{},addEventListener(n,f){this.listeners[n]=f;}};
const ctx = vm.createContext({
  document:documentMock,window:windowMock,URLSearchParams,
  performance:{now:()=>now},
  setInterval(fn){ interval=fn; return 11; },
  clearInterval(){interval=null;},
  setTimeout(fn){expiry=fn;return 22;},
  clearTimeout(){expiry=null;},
  fetch:async (url,options)=>{
    assert(url.endsWith("/admin-users/5/password/reveal"));
    assert(options.body.toString().includes("reauth_password="));
    return {ok:true,json:async()=>({password:"vault-secret-test",hide_after_seconds:10})};
  },
});
vm.runInContext(source,ctx,{timeout:1000});
(async()=>{
  button.listeners.click();
  assert.equal(overlay.hidden,false);
  assert.equal(elements["[data-vault-reauth-step]"].hidden,false);
  elements["[data-vault-reauth]"].value="AdminLoginTest";
  await elements["[data-vault-confirm]"].listeners.click();
  assert.equal(elements["[data-vault-reauth]"].value,"","Super Admin field must wipe");
  assert.equal(elements["[data-vault-reauth-step]"].hidden,true,"Super Admin field must hide");
  assert.equal(elements["[data-vault-confirm]"].hidden,true,"Reauth action must hide");
  assert.equal(elements["[data-vault-result]"].hidden,false);
  assert.equal(elements["[data-vault-secret-value]"].value,"vault-secret-test");
  assert.equal(elements["[data-vault-countdown]"].textContent,"Hides in 10 seconds");
  now=5900;interval();
  assert.equal(elements["[data-vault-countdown]"].textContent,"Hides in 5 seconds");
  now=9999;interval();
  assert.equal(elements["[data-vault-countdown]"].textContent,"Hides in 1 second");
  now=10000; interval();
  assert.equal(elements["[data-vault-result]"].hidden,true,"Secret must be hidden at 10 seconds");
  assert.equal(elements["[data-vault-secret-value]"].value,"","Secret must be cleared at expiry");
  assert.equal(elements["[data-vault-reauth-step]"].hidden,false,"Reauth step should return");
  assert.equal(elements["[data-vault-confirm]"].hidden,false);
  console.log("PASS: real reveal JS hides Super Admin reauth, countdown 10->5->1->0, wipes exact 10 seconds");
})().catch(e=>{console.error(e);process.exitCode=1});
