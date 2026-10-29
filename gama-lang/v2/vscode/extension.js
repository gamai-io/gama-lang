'use strict';

const vscode = require('vscode');
const cp = require('child_process');
const fs = require('fs');
const path = require('path');

const LANG_ID = 'gama-l';
const CLIENT_NAME = 'gama-l-vscode';
const CLIENT_VERSION = '0.2.0';

// Automatic restarts after an unexpected exit: delays grow, total is capped.
const RESTART_DELAYS_MS = [1000, 2000, 4000];
const MAX_RESTARTS = 3;

// ---------------------------------------------------------------- JSON-RPC framing

function findHeaderEnd(buf) {
  const crlf = buf.indexOf('\r\n\r\n');
  const lf = buf.indexOf('\n\n');
  if (crlf === -1) return lf === -1 ? null : { index: lf, skip: 2 };
  if (lf === -1) return { index: crlf, skip: 4 };
  return lf < crlf ? { index: lf, skip: 2 } : { index: crlf, skip: 4 };
}

function readContentLength(header) {
  const re = /(^|\r?\n)content-length\s*:\s*(\d+)/i;
  const m = re.exec(header);
  if (!m) return -1;
  const n = parseInt(m[2], 10);
  return Number.isFinite(n) ? n : -1;
}

/**
 * Incremental Content-Length framed parser.
 * Never throws; malformed frames are skipped.
 */
function createStreamParser(onMessage) {
  let buf = Buffer.alloc(0);
  return function feed(chunk) {
    buf = Buffer.concat([buf, chunk]);
    for (;;) {
      const end = findHeaderEnd(buf);
      if (!end) return;
      const header = buf.slice(0, end.index).toString('ascii');
      const len = readContentLength(header);
      const bodyStart = end.index + end.skip;
      if (len < 0) {
        buf = buf.slice(bodyStart);
        continue;
      }
      if (buf.length < bodyStart + len) return;
      const body = buf.slice(bodyStart, bodyStart + len).toString('utf8');
      buf = buf.slice(bodyStart + len);
      let msg;
      try {
        msg = JSON.parse(body);
      } catch {
        continue;
      }
      try {
        onMessage(msg);
      } catch {
        /* a handler bug must never kill the extension host */
      }
    }
  };
}

// ---------------------------------------------------------------- helpers

function firstWorkspaceFolder() {
  const folders = vscode.workspace.workspaceFolders;
  return folders && folders.length ? folders[0] : null;
}

function expandWorkspaceFolder(p) {
  if (!p) return p;
  if (p.indexOf('${workspaceFolder}') === -1) return p;
  const folder = firstWorkspaceFolder();
  if (!folder) return p.replace(/\$\{workspaceFolder\}/g, '');
  return p.replace(/\$\{workspaceFolder\}/g, folder.uri.fsPath);
}

function isExecutableFile(p) {
  try {
    if (!fs.statSync(p).isFile()) return false;
    fs.accessSync(p, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/**
 * Resolve a bare command (e.g. "gama-lsp") against PATH.
 * On Windows PATHEXT extensions are tried as well.
 */
function findInPath(name) {
  if (!name || name.indexOf('/') !== -1 || name.indexOf('\\') !== -1) return null;
  const envPath = process.env.PATH || process.env.Path || '';
  if (!envPath) return null;
  const sep = process.platform === 'win32' ? ';' : ':';
  const isWin = process.platform === 'win32';
  const hasExt = isWin && /\.[a-z0-9]+$/i.test(name);
  const exts = isWin
    ? (process.env.PATHEXT || '.EXE;.CMD;.BAT')
        .split(';')
        .map((e) => e.trim())
        .filter(Boolean)
    : [''];
  const names = hasExt ? [name] : exts.map((e) => name + e);
  for (const dir of envPath.split(sep)) {
    if (!dir) continue;
    for (const candidate of names) {
      const full = path.join(dir, candidate);
      if (isExecutableFile(full)) return full;
    }
  }
  return null;
}

function interpreterCandidates(preferred) {
  const list = [];
  const add = (value) => {
    const v = (value || '').trim();
    if (v && list.indexOf(v) === -1) list.push(v);
  };
  add(preferred);
  add('python3');
  add('python');
  add('py');
  return list;
}

function toSeverity(value) {
  switch (value) {
    case 1:
      return vscode.DiagnosticSeverity.Error;
    case 2:
      return vscode.DiagnosticSeverity.Warning;
    case 3:
      return vscode.DiagnosticSeverity.Information;
    default:
      return vscode.DiagnosticSeverity.Hint;
  }
}

function toZeroBasedPosition(pos) {
  const line = pos && Number.isFinite(pos.line) ? Math.max(0, pos.line) : 0;
  const character = pos && Number.isFinite(pos.character) ? Math.max(0, pos.character) : 0;
  return new vscode.Position(line, character);
}

function toDiagnostic(item) {
  const range = item && item.range ? item.range : {};
  const start = toZeroBasedPosition(range.start);
  let end = toZeroBasedPosition(range.end);
  if (end.line < start.line || (end.line === start.line && end.character < start.character)) {
    end = start;
  }
  const message = typeof item.message === 'string' && item.message ? item.message : 'GAMA-L error';
  const diagnostic = new vscode.Diagnostic(
    new vscode.Range(start, end),
    message,
    toSeverity(item.severity)
  );
  diagnostic.source = typeof item.source === 'string' && item.source ? item.source : 'gama-l';
  if (typeof item.code === 'string' || typeof item.code === 'number') {
    diagnostic.code = item.code;
  }
  return diagnostic;
}

function failedCheckNames(result) {
  if (!result || typeof result !== 'object') return [];
  if (Array.isArray(result.checks)) {
    return result.checks
      .filter((c) => c && c.ok === false)
      .map((c) => (typeof c.name === 'string' && c.name ? c.name : 'unknown'));
  }
  const legacy = result.failed;
  if (Array.isArray(legacy)) return legacy.map(String);
  if (typeof legacy === 'string' && legacy.trim()) {
    return legacy
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
  }
  return [];
}

function passedCheckCount(result) {
  if (!result || !Array.isArray(result.checks)) return null;
  const total = result.checks.length;
  const passed = result.checks.filter((c) => c && c.ok === true).length;
  return { passed, total };
}

// ---------------------------------------------------------------- client

class GamaLspClient {
  constructor(context, output, diagnostics) {
    this._context = context;
    this._out = output;
    this._diagnostics = diagnostics;
    this._proc = null;
    this._parser = null;
    this._pending = new Map();
    this._nextId = 1;
    this._debounce = new Map();
    this._warned = false;
    this._trustNotified = false;
    this._stopping = false;
    this._opened = new Set();
    this._disposables = [];
    this._candidates = [];
    this._candidateIndex = 0;
    this._restartAttempts = 0;
    this._restartTimer = null;
  }

  readConfig() {
    const cfg = vscode.workspace.getConfiguration('gama-l');
    const pythonPath = (cfg.get('pythonPath') || 'python3').trim() || 'python3';
    const rawScript = cfg.get('serverScript') || '';
    let serverScript = expandWorkspaceFolder(String(rawScript).trim());
    if (serverScript && !path.isAbsolute(serverScript)) {
      const folder = firstWorkspaceFolder();
      const base = folder ? folder.uri.fsPath : this._context.extensionPath;
      serverScript = path.resolve(base, serverScript);
    }
    return { pythonPath, serverScript };
  }

  warnOnce(message, ...actions) {
    if (this._warned) return;
    this._warned = true;
    return Promise.resolve(vscode.window.showWarningMessage(message, ...actions));
  }

  /**
   * Workspace Trust gate: never spawn a server (or a workspace script) in an
   * untrusted workspace. Explains once and offers the two ways forward.
   */
  ensureTrusted() {
    if (vscode.workspace.isTrusted) return true;
    if (this._trustNotified) return false;
    this._trustNotified = true;
    this._out.appendLine('[trust] workspace is not trusted; language server not started');
    Promise.resolve(
      vscode.window.showWarningMessage(
        'GAMA-L: this workspace is not trusted, so the GAMA-L language server was not started. ' +
          'Trust the workspace to enable diagnostics, or configure the server in settings.',
        'Manage Trust',
        'Open Settings'
      )
    ).then((choice) => {
      try {
        if (choice === 'Manage Trust') {
          vscode.commands.executeCommand('workbench.trust.manage');
        } else if (choice === 'Open Settings') {
          vscode.commands.executeCommand('workbench.action.openSettings', 'gama-l');
        }
      } catch {
        /* command may not exist in older hosts */
      }
    });
    return false;
  }

  /**
   * Decide how to launch, in order:
   *   1. gama-l.serverScript set  -> pythonPath [script, "lsp"] (with interpreter fallback)
   *   2. gama-lsp on PATH        -> gama-lsp
   *   3. <extensionPath>/../gamal2.py -> pythonPath [script, "lsp"]
   *   4. nothing                 -> warning with Open Settings
   */
  resolveLaunchOptions() {
    const { pythonPath, serverScript } = this.readConfig();
    const folder = firstWorkspaceFolder();
    const workspaceCwd = folder ? folder.uri.fsPath : undefined;

    if (serverScript) {
      if (!fs.existsSync(serverScript)) {
        return { warning: 'GAMA-L: server script not found: ' + serverScript };
      }
      return {
        candidates: interpreterCandidates(pythonPath).map((cmd) => ({
          cmd,
          args: [serverScript, 'lsp'],
          cwd: path.dirname(serverScript),
        })),
      };
    }

    const lsp = findInPath('gama-lsp');
    if (lsp) {
      return { candidates: [{ cmd: lsp, args: [], cwd: workspaceCwd }] };
    }

    const bundled = path.resolve(this._context.extensionPath, '..', 'gamal2.py');
    if (fs.existsSync(bundled)) {
      return {
        candidates: interpreterCandidates(pythonPath).map((cmd) => ({
          cmd,
          args: [bundled, 'lsp'],
          cwd: path.dirname(bundled),
        })),
      };
    }

    return {
      warning:
        'GAMA-L: no language server found. Set "gama-l.serverScript" to gamal2.py ' +
        'or install the "gama-lsp" command on PATH.',
    };
  }

  start() {
    this.stop();
    this._stopping = false;
    this._warned = false;

    if (!this.ensureTrusted()) return;

    const resolved = this.resolveLaunchOptions();
    if (resolved.warning) {
      this.warnOnce(resolved.warning, 'Open Settings').then((choice) => {
        if (choice === 'Open Settings') {
          vscode.commands.executeCommand('workbench.action.openSettings', 'gama-l');
        }
      });
      return;
    }

    this._candidates = resolved.candidates;
    this._candidateIndex = 0;
    this.launch();
  }

  launch() {
    const candidate = this._candidates[this._candidateIndex];
    if (!candidate) {
      this.warnOnce(
        'GAMA-L: could not start the language server. Check "gama-l.pythonPath" and that ' +
          'Python 3.10+ (or the "gama-lsp" command) is installed.'
      );
      return;
    }

    this._out.appendLine(
      '[start] ' + candidate.cmd + ' ' + candidate.args.join(' ') +
        (candidate.cwd ? '  (cwd=' + candidate.cwd + ')' : '')
    );

    let proc;
    try {
      proc = cp.spawn(candidate.cmd, candidate.args, {
        stdio: ['pipe', 'pipe', 'pipe'],
        cwd: candidate.cwd,
        windowsHide: true,
        // Windows cannot CreateProcess a .cmd/.bat shim directly (pip installs
        // console scripts that way); use the shell for those only.
        shell: process.platform === 'win32' && /\.(cmd|bat)$/i.test(candidate.cmd),
      });
    } catch (err) {
      this._out.appendLine('[spawn] ' + String(err && err.message));
      this.nextCandidate();
      return;
    }

    if (!proc) {
      this.nextCandidate();
      return;
    }

    this._proc = proc;
    this._parser = createStreamParser((msg) => this.onMessage(msg));

    proc.stdout.on('data', (chunk) => {
      if (this._parser) this._parser(chunk);
    });

    proc.stderr.on('data', (chunk) => {
      const text = chunk.toString('utf8').trimEnd();
      if (text) this._out.appendLine('[stderr] ' + text);
    });

    proc.on('error', (err) => {
      const code = err && err.code;
      this._out.appendLine('[error] ' + String(err && err.message));
      // ENOENT -> try the next interpreter (python3 -> python -> py).
      if (code === 'ENOENT' && this._candidateIndex + 1 < this._candidates.length) {
        proc._gamaTerminated = true;
        this.nextCandidate();
        return;
      }
      this.warnOnce(
        'GAMA-L: language server failed to start (' +
          candidate.cmd +
          '). Check "gama-l.pythonPath" and that Python 3.10+ is installed.'
      );
      this.onProcessTerminated(proc, null, null);
    });

    // Both exit and close are handled, guarded so a restart is scheduled once.
    proc.on('exit', (code, signal) => this.onProcessTerminated(proc, code, signal));
    proc.on('close', (code, signal) => this.onProcessTerminated(proc, code, signal));

    this._out.appendLine('[server] started, pid=' + proc.pid);
    this.initialize();
  }

  nextCandidate() {
    this._candidateIndex += 1;
    this._proc = null;
    this._parser = null;
    if (this._candidateIndex < this._candidates.length) {
      this.launch();
      return;
    }
    this.warnOnce(
      'GAMA-L: could not start the language server. Check "gama-l.pythonPath" and that ' +
        'Python 3.10+ is installed.'
    );
  }

  onProcessTerminated(proc, code, signal) {
    if (!proc || proc._gamaTerminated) return;
    proc._gamaTerminated = true;

    this._out.appendLine('[exit] code=' + code + ' signal=' + signal);

    const intentional = !!proc._gamaIntentional || this._stopping || this._proc !== proc;
    this.onExit(proc);
    if (intentional) return;

    this.scheduleRestart(code, signal);
  }

  scheduleRestart(code, signal) {
    if (this._restartAttempts >= MAX_RESTARTS) {
      vscode.window
        .showWarningMessage(
          'GAMA-L: language server stopped ' + MAX_RESTARTS +
            ' times in a row (last exit ' + code + '). Giving up until the window reloads; ' +
            'check "gama-l.serverScript"/"gama-l.pythonPath".',
          'Open Settings'
        )
        .then((choice) => {
          if (choice === 'Open Settings') {
            vscode.commands.executeCommand('workbench.action.openSettings', 'gama-l');
          }
        });
      return;
    }

    if (this._restartTimer) return;

    const delay = RESTART_DELAYS_MS[Math.min(this._restartAttempts, RESTART_DELAYS_MS.length - 1)];
    this._restartAttempts += 1;
    this._out.appendLine(
      '[restart] scheduled in ' + delay + 'ms (attempt ' + this._restartAttempts + '/' + MAX_RESTARTS + ')'
    );
    vscode.window.showWarningMessage(
      'GAMA-L: language server stopped unexpectedly (exit ' + code + '). Restarting in ' +
        delay / 1000 + 's (attempt ' + this._restartAttempts + '/' + MAX_RESTARTS + ').'
    );

    this._restartTimer = setTimeout(() => {
      this._restartTimer = null;
      this.start();
    }, delay);
  }

  onExit(proc) {
    if (this._proc !== proc) return;
    this._proc = null;
    this._parser = null;
    this._opened.clear();
    for (const [, entry] of this._pending) {
      clearTimeout(entry.timer);
      entry.resolve(null);
    }
    this._pending.clear();
  }

  initialize() {
    const folder = firstWorkspaceFolder();
    this.request('initialize', {
      processId: process.pid,
      clientInfo: { name: CLIENT_NAME, version: CLIENT_VERSION },
      rootUri: folder ? folder.uri.toString() : null,
      workspaceFolders: folder
        ? [{ uri: folder.uri.toString(), name: folder.name }]
        : null,
      capabilities: {
        textDocument: {
          synchronization: { dynamicRegistration: false, willSave: false, didSave: false },
          publishDiagnostics: { relatedInformation: false },
        },
      },
    })
      .then((result) => {
        if (result === null) return;
        // The server answered: this launch is healthy, so future crashes get a full budget.
        this._restartAttempts = 0;
        this.notify('initialized', {});
        this.openVisibleDocuments();
      })
      .catch(() => {
        /* request() already resolves null on failure */
      });
  }

  write(payload) {
    if (!this._proc || !this._proc.stdin || !this._proc.stdin.writable) return false;
    const body = Buffer.from(JSON.stringify(payload), 'utf8');
    const frame = Buffer.concat([
      Buffer.from('Content-Length: ' + body.length + '\r\n\r\n', 'ascii'),
      body,
    ]);
    try {
      this._proc.stdin.write(frame);
      return true;
    } catch (err) {
      this._out.appendLine('[write] failed: ' + String(err && err.message));
      return false;
    }
  }

  notify(method, params) {
    return this.write({ jsonrpc: '2.0', method, params });
  }

  request(method, params, timeoutMs) {
    if (!this._proc) return Promise.resolve(null);
    const id = this._nextId++;
    const timeout = typeof timeoutMs === 'number' ? timeoutMs : 15000;
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        if (this._pending.delete(id)) {
          this._out.appendLine('[timeout] ' + method + ' (' + id + ')');
          resolve(null);
        }
      }, timeout);
      this._pending.set(id, { method, resolve, timer });
      const ok = this.write({ jsonrpc: '2.0', id, method, params });
      if (!ok) {
        clearTimeout(timer);
        this._pending.delete(id);
        resolve(null);
      }
    });
  }

  onMessage(msg) {
    if (!msg || typeof msg !== 'object') return;

    if (msg.id !== undefined && msg.id !== null && !msg.method) {
      const entry = this._pending.get(msg.id);
      if (entry) {
        clearTimeout(entry.timer);
        this._pending.delete(msg.id);
        if (msg.error) this._out.appendLine('[error] ' + entry.method + ': ' + JSON.stringify(msg.error));
        entry.resolve(msg.result === undefined ? null : msg.result);
      }
      return;
    }

    if (msg.method === 'textDocument/publishDiagnostics') {
      this.applyDiagnostics(msg.params || {});
      return;
    }

    if (msg.id !== undefined && msg.id !== null && msg.method) {
      // Server-initiated request we do not support: answer with null.
      this.write({ jsonrpc: '2.0', id: msg.id, result: null });
    }
  }

  applyDiagnostics(params) {
    const uri = params.uri;
    if (!uri) return;
    let target;
    try {
      target = vscode.Uri.parse(uri);
    } catch {
      return;
    }
    const raw = Array.isArray(params.diagnostics) ? params.diagnostics : [];
    const mapped = [];
    for (const item of raw) {
      try {
        mapped.push(toDiagnostic(item));
      } catch {
        /* skip malformed entry */
      }
    }
    this._diagnostics.set(target, mapped);
  }

  documentText(doc) {
    try {
      return doc.getText();
    } catch {
      return '';
    }
  }

  didOpen(doc) {
    if (!this._proc || doc.languageId !== LANG_ID) return;
    this._opened.add(doc.uri.toString());
    this.notify('textDocument/didOpen', {
      textDocument: {
        uri: doc.uri.toString(),
        languageId: LANG_ID,
        version: doc.version,
        text: this.documentText(doc),
      },
    });
  }

  didChange(doc) {
    if (!this._proc || doc.languageId !== LANG_ID) return;
    if (!this._opened.has(doc.uri.toString())) {
      this.didOpen(doc);
      return;
    }
    const key = doc.uri.toString();
    const existing = this._debounce.get(key);
    if (existing) clearTimeout(existing);
    const timer = setTimeout(() => {
      this._debounce.delete(key);
      if (!this._proc) return;
      this.notify('textDocument/didChange', {
        textDocument: { uri: key, version: doc.version },
        contentChanges: [{ text: this.documentText(doc) }],
      });
    }, 150);
    this._debounce.set(key, timer);
  }

  didClose(doc) {
    const key = doc.uri.toString();
    if (!this._opened.has(key)) return;
    this._opened.delete(key);
    const timer = this._debounce.get(key);
    if (timer) {
      clearTimeout(timer);
      this._debounce.delete(key);
    }
    if (!this._proc) return;
    this.notify('textDocument/didClose', { textDocument: { uri: key } });
    this._diagnostics.delete(doc.uri);
  }

  openVisibleDocuments() {
    for (const doc of vscode.workspace.textDocuments) {
      if (doc.languageId === LANG_ID) this.didOpen(doc);
    }
  }

  shutdown() {
    const proc = this._proc;
    if (!proc) return;
    this._stopping = true;
    proc._gamaIntentional = true;
    this.request('shutdown', null, 1500).then(() => {
      this.notify('exit', null);
      setTimeout(() => {
        try {
          if (proc.exitCode === null) proc.kill();
        } catch {
          /* ignore */
        }
      }, 700);
    });
  }

  stop() {
    this._stopping = true;
    if (this._restartTimer) {
      clearTimeout(this._restartTimer);
      this._restartTimer = null;
    }
    for (const timer of this._debounce.values()) clearTimeout(timer);
    this._debounce.clear();
    const proc = this._proc;
    this._proc = null;
    this._parser = null;
    this._opened.clear();
    if (proc) {
      // Mark before kill so the exit/close handler does not report "unexpected".
      proc._gamaIntentional = true;
      try {
        proc.kill();
      } catch {
        /* ignore */
      }
    }
  }

  restart() {
    this.stop();
    try {
      this._diagnostics.clear();
    } catch {
      /* ignore */
    }
    this.start();
  }

  register(context, output) {
    this._disposables.push(
      vscode.workspace.onDidOpenTextDocument((doc) => this.didOpen(doc)),
      vscode.workspace.onDidChangeTextDocument((e) => this.didChange(e.document)),
      vscode.workspace.onDidCloseTextDocument((doc) => this.didClose(doc)),
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration('gama-l.pythonPath') || e.affectsConfiguration('gama-l.serverScript')) {
          output.appendLine('[config] changed, restarting server');
          this.restart();
        }
      })
    );
    for (const d of this._disposables) context.subscriptions.push(d);
  }

  dispose() {
    this.shutdown();
    for (const d of this._disposables) d.dispose();
    this._disposables = [];
  }
}

// ---------------------------------------------------------------- doctor

function runDoctor(client) {
  if (!client.ensureTrusted()) return;

  const { pythonPath, serverScript } = client.readConfig();
  const folder = firstWorkspaceFolder();
  let cmd;
  let args;
  let cwd;

  if (serverScript) {
    if (!fs.existsSync(serverScript)) {
      vscode.window.showErrorMessage(
        'GAMA-L doctor failed: server script not found (' + serverScript + ')'
      );
      return;
    }
    cmd = pythonPath;
    args = [serverScript, 'doctor', '--json'];
    cwd = path.dirname(serverScript);
  } else {
    const cli = findInPath('gama-l');
    if (!cli) {
      vscode.window
        .showWarningMessage(
          'GAMA-L doctor: "gama-l.serverScript" is not set and the "gama-l" command was not ' +
            'found on PATH. Set "gama-l.serverScript" to gamal2.py.',
          'Open Settings'
        )
        .then((choice) => {
          if (choice === 'Open Settings') {
            vscode.commands.executeCommand('workbench.action.openSettings', 'gama-l');
          }
        });
      return;
    }
    cmd = cli;
    args = ['doctor', '--json'];
    cwd = folder ? folder.uri.fsPath : undefined;
  }

  cp.execFile(
    cmd,
    args,
    { timeout: 60000, windowsHide: true, maxBuffer: 8 * 1024 * 1024, cwd },
    (err, stdout, stderr) => {
      const raw = String(stdout || '').trim();
      let parsed = null;
      if (raw) {
        try {
          parsed = JSON.parse(raw);
        } catch {
          parsed = null;
        }
      }

      if (err && !parsed) {
        const detail = String(stderr || err.message || '').trim().split('\n')[0];
        vscode.window.showErrorMessage('GAMA-L doctor failed: ' + (detail || 'unknown error'));
        return;
      }

      if (parsed && typeof parsed === 'object') {
        if (parsed.ok === true) {
          const counts = passedCheckCount(parsed);
          const suffix = counts ? ' (' + counts.passed + '/' + counts.total + ' checks)' : '';
          vscode.window.showInformationMessage('GAMA-L doctor: ok = true' + suffix);
          return;
        }
        const failed = failedCheckNames(parsed);
        vscode.window.showWarningMessage(
          'GAMA-L doctor: ok = false — failed: ' + (failed.length ? failed.join(', ') : 'see details')
        );
        return;
      }

      vscode.window.showInformationMessage('GAMA-L doctor exit: ' + (err ? 'failed' : 'ok'));
    }
  );
}

// ---------------------------------------------------------------- lifecycle

let activeClient = null;

function activate(context) {
  const output = vscode.window.createOutputChannel('GAMA-L');
  const diagnostics = vscode.languages.createDiagnosticCollection('gama-l');
  context.subscriptions.push(output, diagnostics);

  const client = new GamaLspClient(context, output, diagnostics);
  client.register(context, output);
  client.start();

  context.subscriptions.push(
    vscode.commands.registerCommand('gama-l.doctor', () => {
      try {
        runDoctor(client);
      } catch (err) {
        vscode.window.showErrorMessage('GAMA-L doctor failed: ' + String(err && err.message));
      }
    })
  );

  activeClient = client;
  return {
    dispose() {
      if (activeClient === client) activeClient = null;
      client.dispose();
    },
  };
}

function deactivate() {
  if (activeClient) {
    const client = activeClient;
    activeClient = null;
    client.dispose();
  }
  return undefined;
}

module.exports = { activate, deactivate };
