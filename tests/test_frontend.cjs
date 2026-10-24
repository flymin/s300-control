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
    dataset: {}, setAttribute() {}, replaceChildren() {},
    querySelector: element, classList: { toggle() {}, remove() {} }, textContent: "",
  });
  const scope = vm.createContext({
    URL, AbortController, setTimeout: () => 1, clearTimeout() {},
    document: {
      baseURI: "http://localhost/", activeElement: null,
      getElementById(id) {
        if (!nodes.has(id)) nodes.set(id, element());
        return nodes.get(id);
      },
      querySelectorAll: () => [], createElement: element,
    },
    CSS: { escape: (value) => value },
  });
  vm.runInContext(source.slice(0, wiring) + `
    renderCurve = renderVolume = renderPresets = toast = () => {};
    renderConnection = updateEnabled;
    function fixture(revision, instance = "first", values = [0,0,0,0,0,0], level = 6) {
      return {revision, instance_id: instance, connection: "online",
        state: {gains: values, volume: level, min_volume: 0, max_volume: 30, eq_active: true},
        device: {id: "demo"}, devices: [], presets: []};
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
