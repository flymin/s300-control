"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

// Exercise the real state/control functions without browser event wiring or a speaker.
const source = fs.readFileSync(
  path.join(__dirname, "../src/s300_control/static/app.js"), "utf8",
);
const wiring = source.indexOf('$("volume").addEventListener');
assert.ok(wiring > 0, "frontend event wiring must follow the state functions");

function context() {
  const nodes = new Map();
  const element = () => ({
    dataset: {}, attributes: {}, children: [], textContent: "",
    setAttribute(key, value) { this.attributes[key] = value; },
    replaceChildren(...children) { this.children = children; },
    append(...children) { this.children.push(...children); },
    addEventListener() {}, close() { this.open = false; },
    querySelector: element, classList: { toggle() {}, remove() {} },
  });
  const scope = vm.createContext({
    URL, AbortController, setTimeout: () => 1, clearTimeout() {},
    document: {
      baseURI: "http://localhost/", activeElement: null,
      getElementById(id) {
        if (!nodes.has(id)) nodes.set(id, element());
        return nodes.get(id);
      },
      querySelectorAll: (selector) => selector === ".mode-option"
        ? (nodes.get("modeOptions")?.children || []) : [],
      createElement: element,
    },
    CSS: { escape: (value) => value },
  });
  vm.runInContext(source.slice(0, wiring) + `
    renderCurve = renderVolume = renderPresets = toast = () => {};
    renderConnection = updateEnabled;
    function fixture(revision, instance = "first", values = [0,0,0,0,0,0], level = 6) {
      return {revision, instance_id: instance, connection_epoch: 0, connection: "online",
        state: {gains: values, volume: level, min_volume: 0, max_volume: 30,
          sound_index: 1, selected_index: 4, eq_active: true},
        sound_modes: [
          {id:0,name:"经典",description:"Classic"}, {id:1,name:"监听",description:"Monitor"},
          {id:2,name:"动感",description:"Dynamic"}, {id:3,name:"人声",description:"Vocal"},
          {id:4,name:"DIY",description:"自定义 EQ"}],
        device: {id: "demo"}, devices: [], presets: []};
    }
    function withMode(data, mode) {
      data.state.selected_index = mode;
      data.state.eq_active = mode === 4;
      return data;
    }
  `, scope);
  return scope;
}

async function scenario(code) {
  const result = await vm.runInContext(`(async () => { ${code} })()`, context());
  return JSON.parse(JSON.stringify(result));
}

test("a restarted backend can reset revision; delayed old-instance responses stay ignored", async () => {
  const result = await scenario(`
    accept(fixture(100, "first", [5,0,0,0,0,0]));
    accept(fixture(1, "restarted", [10,0,0,0,0,0]));
    accept(fixture(101, "first", [15,0,0,0,0,0]));
    return {instance: snapshot.instance_id, revision: snapshot.revision, gains};
  `);
  assert.deepEqual(result, {instance: "restarted", revision: 1, gains: [10,0,0,0,0,0]});
});

test("out-of-order snapshots from the same backend cannot roll back settings", async () => {
  const result = await scenario(`
    accept(fixture(10, "first", [10,0,0,0,0,0]));
    accept(fixture(9, "first", [5,0,0,0,0,0]));
    return {revision: snapshot.revision, gains};
  `);
  assert.deepEqual(result, {revision: 10, gains: [10,0,0,0,0,0]});
});

for (const transition of ["failed poll", "offline speaker", "backend restart", "device switch"]) {
  test(`pending EQ is discarded after ${transition} and is not replayed by a volume change`, async () => {
    const result = await scenario(`
      accept(fixture(10));
      changeBand(0, 5);
      const transition = ${JSON.stringify(transition)};
      if (transition === "failed poll") {
        api = async () => { throw new Error("unreachable"); };
        await poll();
      } else if (transition === "offline speaker") {
        accept({...fixture(11), connection: "offline"});
      } else if (transition === "backend restart") {
        accept(fixture(1, "restarted"));
      } else {
        accept({...fixture(11), device: {id: "second-speaker"}});
      }
      const afterTransition = JSON.parse(JSON.stringify(pending));
      await pump();
      const instance = snapshot.instance_id;
      const device = snapshot.device;
      accept({...fixture(12, instance, [10,0,0,0,0,0]), device});
      const requests = [];
      api = async (kind, payload) => {
        requests.push({kind, payload});
        return {...fixture(13, instance, [10,0,0,0,0,0], payload.volume), device};
      };
      changeVolume(7);
      await pump();
      return {afterTransition, requests, gains};
    `);
    assert.deepEqual(result, {
      afterTransition: {eq: null, volume: null},
      requests: [{kind: "volume", payload: {volume: 7}}], gains: [10,0,0,0,0,0],
    });
  });
}

test("an old in-flight completion cannot restore stale state or erase a new user action", async () => {
  const result = await scenario(`
    accept(fixture(10));
    let complete;
    api = () => new Promise(resolve => { complete = resolve; });
    changeBand(0, 5);
    const oldPump = pump();
    accept(fixture(1, "restarted", [10,0,0,0,0,0]));
    changeVolume(7);
    complete(fixture(11, "first", [5,0,0,0,0,0]));
    await oldPump;
    const requests = [];
    api = async (kind, payload) => {
      requests.push({kind, payload});
      return fixture(2, "restarted", [10,0,0,0,0,0], payload.volume);
    };
    await pump();
    return {instance: snapshot.instance_id, requests, gains};
  `);
  assert.deepEqual(result, {
    instance: "restarted", requests: [{kind: "volume", payload: {volume: 7}}],
    gains: [10,0,0,0,0,0],
  });
});

test("an interrupted EQ gesture imports the new context before changing another band", async () => {
  const result = await scenario(`
    accept(fixture(10));
    let complete;
    api = () => new Promise(resolve => { complete = resolve; });
    changeBand(0, 5);
    draggingBand = 0;
    const oldPump = pump();
    accept(fixture(1, "restarted", [10,10,10,10,10,10]));
    const imported = [...gains];
    changeBand(1, 5);
    complete(fixture(11, "first", [5,0,0,0,0,0]));
    await oldPump;
    const requests = [];
    api = async (kind, payload) => {
      requests.push({kind, payload});
      return fixture(2, "restarted", payload.gains);
    };
    await pump();
    return {imported, requests, draggingBand};
  `);
  assert.deepEqual(result, {
    imported: [10,10,10,10,10,10], draggingBand: null,
    requests: [{kind: "eq", payload: {gains: [10,5,10,10,10,10]}}],
  });
});

test("a focused volume input imports the new context while an old write is in flight", async () => {
  const result = await scenario(`
    accept(fixture(10));
    document.activeElement = document.getElementById("volume");
    let complete;
    api = () => new Promise(resolve => { complete = resolve; });
    changeVolume(7);
    const oldPump = pump();
    accept(fixture(1, "restarted", [0,0,0,0,0,0], 10));
    const imported = volume;
    changeVolume(volume + 1);
    complete(fixture(11, "first", [0,0,0,0,0,0], 7));
    await oldPump;
    const requests = [];
    api = async (kind, payload) => {
      requests.push({kind, payload});
      return fixture(2, "restarted", [0,0,0,0,0,0], payload.volume);
    };
    await pump();
    return {imported, requests};
  `);
  assert.deepEqual(result, {
    imported: 10, requests: [{kind: "volume", payload: {volume: 11}}],
  });
});

test("a completed volume gesture keeps following external changes while still focused", async () => {
  const result = await scenario(`
    accept(fixture(10));
    document.activeElement = document.getElementById("volume");
    api = async () => fixture(11, "first", [0,0,0,0,0,0], 8);
    changeVolume(8);
    await pump();
    accept(fixture(12, "first", [0,0,0,0,0,0], 15));
    const imported = volume;
    const requests = [];
    api = async (kind, payload) => {
      requests.push({kind, payload});
      return fixture(13, "first", [0,0,0,0,0,0], payload.volume);
    };
    changeVolume(volume + 1);
    await pump();
    return {imported, requests};
  `);
  assert.deepEqual(result, {
    imported: 15, requests: [{kind: "volume", payload: {volume: 16}}],
  });
});

test("polling still preserves an unsent local volume adjustment", async () => {
  const result = await scenario(`
    accept(fixture(10));
    changeVolume(8);
    accept(fixture(11, "first", [0,0,0,0,0,0], 15));
    return {volume, pending: pending.volume};
  `);
  assert.deepEqual(result, {volume: 8, pending: {volume: 8}});
});

test("factory modes hide all DIY sections and reject EQ interaction", async () => {
  const result = await scenario(`
    const outputs = [];
    for (let mode = 0; mode < 5; mode++) {
      accept(withMode(fixture(mode + 1), mode));
      changeBand(0, 5);
      outputs.push({mode, eqHidden: $("eqPanel").hidden,
        libraryHidden: $("libraryPanel").hidden, canEdit: canEditEQ(), pendingEQ: pending.eq});
    }
    return outputs;
  `);
  for (const row of result) {
    assert.equal(row.eqHidden, row.mode !== 4);
    assert.equal(row.libraryHidden, row.mode !== 4);
    assert.equal(row.canEdit, row.mode === 4);
    assert.deepEqual(row.pendingEQ, row.mode === 4 ? {gains: [5,0,0,0,0,0]} : null);
  }
});

test("mode switches send only the selected mode and wait for readback before exposing DIY", async () => {
  const result = await scenario(`
    accept(withMode(fixture(10, "first", [10,5,0,0,0,0]), 1));
    let complete;
    const requests = [];
    api = (kind, payload) => {
      requests.push({kind,payload});
      return new Promise(resolve => { complete = resolve; });
    };
    const switching = changeMode(4);
    const during = {eqHidden: $("eqPanel").hidden, disabled: $("reset").disabled};
    await changeMode(2);
    complete(fixture(11, "first", [10,5,0,0,0,0]));
    await switching;
    return {requests,during,after: {eqHidden: $("eqPanel").hidden, canEdit: canEditEQ(),gains}};
  `);
  assert.deepEqual(result, {
    requests: [{kind:"mode",payload:{selected_index:4}}],
    during: {eqHidden:true,disabled:true},
    after: {eqHidden:false,canEdit:true,gains:[10,5,0,0,0,0]},
  });
});

test("an external factory mode cancels a pending DIY adjustment", async () => {
  const result = await scenario(`
    accept(fixture(10));
    changeBand(0,5);
    accept(withMode(fixture(11), 2));
    const requests = [];
    api = async (kind,payload) => {requests.push({kind,payload});return withMode(fixture(12),2);};
    await pump();
    changeBand(1,5);
    return {requests,pending:pending.eq,hidden:$("eqPanel").hidden};
  `);
  assert.deepEqual(result, {requests:[],pending:null,hidden:true});
});

test("a failed mode switch leaves the confirmed factory mode visible", async () => {
  const result = await scenario(`
    accept(withMode(fixture(10), 1));
    api = async kind => {
      if (kind === "mode") throw new Error("readback mismatch");
      return withMode(fixture(11), 1);
    };
    await changeMode(4);
    return {selected:snapshot.state.selected_index,hidden:$("eqPanel").hidden,requestedMode};
  `);
  assert.deepEqual(result, {selected:1,hidden:true,requestedMode:null});
});

test("a delayed saved-curve response cannot restore online state after a disconnect", async () => {
  const result = await scenario(`
    const savedResponse = fixture(10);
    accept(savedResponse);
    accept({...fixture(11),connection:"offline"});
    accept(savedResponse);
    return {connected,connection:snapshot.connection,canEdit:canEditEQ(),revision:snapshot.revision};
  `);
  assert.deepEqual(result,{connected:false,connection:"offline",canEdit:false,revision:11});
});

test("a same-device same-endpoint reconnect invalidates pending controls", async () => {
  const result = await scenario(`
    accept(fixture(10));
    changeBand(0,5);
    accept({...fixture(11,"first",[20,20,20,20,20,20]),connection_epoch:1});
    const requests=[];
    api=async(kind,payload)=>{requests.push({kind,payload});return fixture(12);};
    await pump();
    return {requests,gains,pending:pending.eq};
  `);
  assert.deepEqual(result,{requests:[],gains:[20,20,20,20,20,20],pending:null});
});

test("control HTTP requests carry the connection context displayed by the page", async () => {
  const result = await scenario(`
    accept({...fixture(10),connection_epoch:3});
    let body;
    fetch=async(url,options)=>{
      body=JSON.parse(options.body);
      return {ok:true,json:async()=>fixture(11)};
    };
    await api("mode",{selected_index:1});
    return body;
  `);
  assert.deepEqual(result,{selected_index:1,context:{instance_id:"first",connection_epoch:3,device_id:"demo"}});
});
