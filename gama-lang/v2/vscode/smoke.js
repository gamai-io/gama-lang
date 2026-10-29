'use strict';

/**
 * Smoke test for extension.js with a stubbed `vscode` API and a stubbed
 * `child_process`. No VS Code, no Python execution, no network.
 *
 * Run: node smoke.js
 */

const assert = require('assert');
const EventEmitter = require('events');
const Module = require('module');
const path = require('path');

const VSCODE_DIR = __dirname;

// ---------------------------------------------------------------- stubs

const state = {
  trusted: true,
  config: { pythonPath: 'python3', serverScript: '' },
  warnings: [],
  infos: [],
  errors: [],
  commands: new Map(),
  spawns: [],
  execs: [],
  procs: [],
  executed: [],
};

function fakeProc(cmd, args) {
  const proc = new EventEmitter();
  proc.stdin = { writable: true, write() { return true; } };
  proc.stdout = new EventEmitter();
  proc.stderr = new EventEmitter();
  proc.pid = 4000 + state.procs.length;
  proc.exitCode = null;
  proc.killCalls = 0;
  proc.cmd = cmd;
  proc.args = args;
  proc.kill = () => { proc.killCalls += 1; proc.exitCode = 0; };
  state.procs.push(proc);
  return proc;
}

const fakeVscode = {
  workspace: {
    get isTrusted() { return state.trusted; },
    workspaceFolders: [
      {
        uri: { fsPath: VSCODE_DIR, toString: () => 'file://' + VSCODE_DIR },
        name: 'vscode',
      },
    ],
    textDocuments: [],
    getConfiguration() {
      return {
        get(key, dflt) {
          return Object.prototype.hasOwnProperty.call(state.config, key)
            ? state.config[key]
            : dflt;
        },
      };
    },
    onDidOpenTextDocument() { return { dispose() {} }; },
    onDidChangeTextDocument() { return { dispose() {} }; },
    onDidCloseTextDocument() { return { dispose() {} }; },
    onDidChangeConfiguration() { return { dispose() {} }; },
  },
  window: {
    createOutputChannel() {
      return { appendLine() {}, append() {}, dispose() {} };
    },
    showWarningMessage(msg, ...actions) {
      state.warnings.push({ msg: String(msg), actions });
      return Promise.resolve(undefined);
    },
    showInformationMessage(msg, ...actions) {
      state.infos.push({ msg: String(msg), actions });
      return Promise.resolve(undefined);
    },
    showErrorMessage(msg, ...actions) {
      state.errors.push({ msg: String(msg), actions });
      return Promise.resolve(undefined);
    },
  },
  languages: {
    createDiagnosticCollection() {
      return { set() {}, delete() {}, clear() {}, dispose() {} };
    },
  },
  commands: {
    registerCommand(id, handler) {
      state.commands.set(id, handler);
      return { dispose() { state.commands.delete(id); } };
    },
    executeCommand(id, ...args) {
      state.executed.push({ id, args });
      return Promise.resolve(undefined);
    },
  },
  DiagnosticSeverity: { Error: 0, Warning: 1, Information: 2, Hint: 3 },
  Position: class Position {
    constructor(line, character) { this.line = line; this.character = character; }
  },
  Range: class Range {
    constructor(start, end) { this.start = start; this.end = end; }
  },
  Diagnostic: class Diagnostic {
    constructor(range, message, severity) {
      this.range = range;
      this.message = message;
      this.severity = severity;
    }
  },
  Uri: {
    parse(value) { return { toString: () => value, fsPath: value }; },
  },
};

const fakeCp = {
  spawn(cmd, args, options) {
    state.spawns.push({ cmd, args: args.slice(), options });
    const proc = fakeProc(cmd, args);
    if (state.failNextSpawnWithEnoent) {
      state.failNextSpawnWithEnoent = false;
      process.nextTick(() => proc.emit('error', { code: 'ENOENT', message: 'spawn ENOENT' }));
    }
    return proc;
  },
  execFile(cmd, args, options, cb) {
    state.execs.push({ cmd, args: args.slice(), options });
    const payload = state.nextDoctorPayload || { ok: true, checks: [] };
    setTimeout(() => cb(null, JSON.stringify(payload), ''), 0);
  },
};

// Intercept only the two modules extension.js needs to have stubbed.
const originalLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === 'vscode') return fakeVscode;
  if (request === 'child_process') return fakeCp;
  return originalLoad.apply(this, arguments);
};

const extension = require('./extension.js');

// ---------------------------------------------------------------- helpers

function reset() {
  state.config = { pythonPath: 'python3', serverScript: '' };
  state.warnings = [];
  state.infos = [];
  state.errors = [];
  state.spawns = [];
  state.execs = [];
  state.procs = [];
  state.executed = [];
  state.nextDoctorPayload = null;
  state.failNextSpawnWithEnoent = false;
  state.trusted = true;
}

function context() {
  return { subscriptions: [], extensionPath: VSCODE_DIR };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function hasWarning(fragment) {
  return state.warnings.some((w) => w.msg.indexOf(fragment) !== -1);
}

function run(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => {
      console.log('ok   - ' + name);
    })
    .catch((err) => {
      console.error('FAIL - ' + name);
      console.error('       ' + (err && err.message));
      throw err;
    });
}

// ---------------------------------------------------------------- tests

async function main() {
  await run('untrusted workspace does not spawn and explains once', () => {
    reset();
    state.trusted = false;
    state.config.serverScript = __filename; // would be executable if trust were ignored
    const ctx = context();
    const handle = extension.activate(ctx);
    assert.strictEqual(state.spawns.length, 0, 'must not spawn in an untrusted workspace');
    assert.ok(hasWarning('not trusted'), 'expected a trust warning');
    const trustWarnings = state.warnings.filter((w) => w.msg.indexOf('not trusted') !== -1);
    assert.strictEqual(trustWarnings.length, 1, 'trust warning must be shown once');
    assert.deepStrictEqual(
      trustWarnings[0].actions,
      ['Manage Trust', 'Open Settings'],
      'trust warning must offer both actions'
    );
    handle.dispose();
  });

  await run('untrusted workspace does not spawn when serverScript is empty', () => {
    reset();
    state.trusted = false;
    const handle = extension.activate(context());
    assert.strictEqual(state.spawns.length, 0);
    assert.ok(hasWarning('not trusted'));
    handle.dispose();
  });

  await run('trusted + configured serverScript spawns python3 <script> lsp', () => {
    reset();
    state.config.serverScript = __filename;
    const handle = extension.activate(context());
    assert.strictEqual(state.spawns.length, 1, 'expected exactly one spawn');
    const spawn = state.spawns[0];
    assert.strictEqual(spawn.cmd, 'python3');
    assert.deepStrictEqual(spawn.args, [__filename, 'lsp']);
    assert.strictEqual(spawn.options.cwd, VSCODE_DIR);
    assert.ok(!hasWarning('not trusted'));
    handle.dispose();
  });

  await run('trusted + empty serverScript falls back to bundled gamal2.py', () => {
    reset();
    const savedPath = process.env.PATH;
    process.env.PATH = ''; // deterministically hide gama-lsp
    try {
      const handle = extension.activate(context());
      assert.strictEqual(state.spawns.length, 1, 'expected exactly one spawn');
      const spawn = state.spawns[0];
      assert.strictEqual(spawn.cmd, 'python3');
      assert.strictEqual(path.basename(spawn.args[0]), 'gamal2.py');
      assert.strictEqual(path.dirname(spawn.args[0]), path.resolve(VSCODE_DIR, '..'));
      assert.strictEqual(spawn.args[1], 'lsp');
      handle.dispose();
    } finally {
      process.env.PATH = savedPath;
    }
  });

  await run('trusted + missing configured serverScript warns without spawning', () => {
    reset();
    state.config.serverScript = path.join(VSCODE_DIR, 'definitely-missing-gamal2.py');
    const handle = extension.activate(context());
    assert.strictEqual(state.spawns.length, 0, 'must not spawn a missing script');
    assert.ok(hasWarning('server script not found'), 'expected a not-found warning');
    const warning = state.warnings.find((w) => w.msg.indexOf('server script not found') !== -1);
    assert.deepStrictEqual(warning.actions, ['Open Settings']);
    handle.dispose();
  });

  await run('python3 ENOENT falls back to python', async () => {
    reset();
    state.config.serverScript = __filename;
    state.failNextSpawnWithEnoent = true;
    const handle = extension.activate(context());
    await sleep(20);
    assert.strictEqual(state.spawns.length, 2, 'expected python3 then python');
    assert.strictEqual(state.spawns[0].cmd, 'python3');
    assert.strictEqual(state.spawns[1].cmd, 'python');
    assert.deepStrictEqual(state.spawns[1].args, [__filename, 'lsp']);
    handle.dispose();
  });

  await run('normal stop does not report "stopped unexpectedly"', () => {
    reset();
    state.config.serverScript = __filename;
    const handle = extension.activate(context());
    const proc = state.procs[0];
    assert.ok(proc, 'expected a spawned process');
    handle.dispose(); // intentional shutdown
    state.warnings = [];
    proc.emit('exit', 0, null);
    proc.emit('close', 0, null);
    assert.ok(!hasWarning('stopped unexpectedly'), 'intentional stop must not warn');
    assert.strictEqual(state.spawns.length, 1, 'must not respawn after an intentional stop');
  });

  await run('unexpected exit warns and restarts with backoff', async () => {
    reset();
    state.config.serverScript = __filename;
    const handle = extension.activate(context());
    const proc = state.procs[0];
    state.warnings = [];
    proc.emit('exit', 1, null);
    assert.ok(hasWarning('stopped unexpectedly'), 'expected an unexpected-stop warning');
    assert.ok(hasWarning('Restarting in 1s'), 'expected the 1s backoff message');
    assert.ok(hasWarning('attempt 1/3'), 'expected attempt 1/3');
    await sleep(1200);
    assert.strictEqual(state.spawns.length, 2, 'expected one restart after ~1s');
    handle.dispose();
  });

  await run('restart budget is capped at 3 attempts then gives up', async () => {
    reset();
    state.config.serverScript = __filename;
    const handle = extension.activate(context());
    assert.strictEqual(state.spawns.length, 1);

    state.procs[0].emit('exit', 1, null);
    await sleep(1200);
    assert.strictEqual(state.spawns.length, 2, 'restart 1 after ~1s');

    state.procs[1].emit('exit', 1, null);
    await sleep(2200);
    assert.strictEqual(state.spawns.length, 3, 'restart 2 after ~2s');

    state.procs[2].emit('exit', 1, null);
    await sleep(4200);
    assert.strictEqual(state.spawns.length, 4, 'restart 3 after ~4s');

    state.warnings = [];
    state.procs[3].emit('exit', 1, null);
    await sleep(50);
    assert.strictEqual(state.spawns.length, 4, 'no fourth restart');
    assert.ok(
      state.warnings.some((w) => w.msg.indexOf('Giving up') !== -1),
      'expected a give-up message'
    );
    handle.dispose();
  });

  await run('doctor reports failed check names from "checks"', async () => {
    reset();
    state.config.serverScript = __filename;
    state.nextDoctorPayload = {
      ok: false,
      checks: [
        { name: 'python', ok: true, detail: '3.12' },
        { name: 'lsp', ok: false, detail: 'boom' },
        { name: 'package', ok: false, detail: 'missing' },
      ],
    };
    const handle = extension.activate(context());
    const doctor = state.commands.get('gama-l.doctor');
    assert.ok(doctor, 'doctor command must be registered');
    await doctor();
    await sleep(20);
    const warning = state.warnings.map((w) => w.msg).find((m) => m.indexOf('doctor: ok = false') !== -1);
    assert.ok(warning, 'expected a doctor warning, got: ' + JSON.stringify(state.warnings));
    assert.ok(warning.indexOf('lsp') !== -1 && warning.indexOf('package') !== -1,
      'failed check names must be listed: ' + warning);
    assert.ok(warning.indexOf('python') === -1, 'passing checks must not be listed');
    handle.dispose();
  });

  await run('doctor without serverScript uses gama-l from PATH or warns', async () => {
    reset();
    const savedPath = process.env.PATH;
    process.env.PATH = '';
    try {
      const handle = extension.activate(context());
      const doctor = state.commands.get('gama-l.doctor');
      await doctor();
      assert.strictEqual(state.execs.length, 0, 'must not exec without a server or gama-l');
      assert.ok(hasWarning('was not found on PATH'), 'expected a PATH warning');
      handle.dispose();
    } finally {
      process.env.PATH = savedPath;
    }
  });

  await run('doctor in untrusted workspace does not execute anything', async () => {
    reset();
    state.trusted = false;
    state.config.serverScript = __filename;
    const handle = extension.activate(context());
    const doctor = state.commands.get('gama-l.doctor');
    await doctor();
    assert.strictEqual(state.execs.length, 0, 'must not exec in an untrusted workspace');
    assert.strictEqual(state.spawns.length, 0);
    handle.dispose();
  });

  console.log('\nall smoke tests passed');
}

main().then(
  () => {
    // Timers for pending JSON-RPC requests may still be pending; exit explicitly.
    process.exit(0);
  },
  (err) => {
    console.error('\nsmoke tests failed');
    if (err) console.error(err);
    process.exit(1);
  }
);
