'use strict';

const vscode = require('vscode');
const cp = require('child_process');
const fs = require('fs');
const path = require('path');

const LANG_ID = 'gama-l';
const CLIENT_NAME = 'gama-l-vscode';
const CLIENT_VERSION = '0.2.0';

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

function expandWorkspaceFolder(p) {
  if (!p) return p;
  const folder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0];
  if (p.indexOf('${workspaceFolder}') === -1) return p;
  if (!folder) return p.replace(/\$\{workspaceFolder\}/g, '');
  return p.replace(/\$\{workspaceFolder\}/g, folder.uri.fsPath);
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
    this._stopping = false;
    this._opened = new Set();
    this._disposables = [];
  }

  readConfig() {
    const cfg = vscode.workspace.getConfiguration('gama-l');
    const pythonPath = (cfg.get('pythonPath') || 'python3').trim() || 'python3';
    const rawScript = cfg.get('serverScript') || '';
    let serverScript = expandWorkspaceFolder(String(rawScript).trim());
    if (serverScript && !path.isAbsolute(serverScript)) {
      const folder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0];
      const base = folder ? folder.uri.fsPath : this._context.extensionPath;
      serverScript = path.resolve(base, serverScript);
    }
    return { pythonPath, serverScript };
  }

  warnOnce(message) {
    if (this._warned) return;
    this._warned = true;
    vscode.window.showWarningMessage(message);
  }

  start() {
    this.stop();
    this._stopping = false;

    const { pythonPath, serverScript } = this.readConfig();
    if (!serverScript) {
      this.warnOnce('GAMA-L: "gama-l.serverScript" is empty. Set it to the path of gamal2.py.');
      return;
    }
    if (!fs.existsSync(serverScript)) {
      this.warnOnce('GAMA-L: server script not found: ' + serverScript);
      return;
    }

    this._out.appendLine('[start] ' + pythonPath + ' ' + serverScript + ' lsp');

    let proc;
    try {
      proc = cp.spawn(pythonPath, [serverScript, 'lsp'], {
        stdio: ['pipe', 'pipe', 'pipe'],
        cwd: path.dirname(serverScript),
        windowsHide: true,
      });
    } catch (err) {
      this.warnOnce('GAMA-L: could not start language server: ' + String(err && err.message));
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
      this._out.appendLine('[error] ' + String(err && err.message));
      this.warnOnce(
        'GAMA-L: language server failed to start (' +
          pythonPath +
          '). Check "gama-l.pythonPath" and that Python 3.10+ is installed.'
      );
      this.onExit(proc);
    });

    proc.on('exit', (code, signal) => {
      this._out.appendLine('[exit] code=' + code + ' signal=' + signal);
      if (!this._stopping) {
        this.warnOnce(
          'GAMA-L: language server stopped unexpectedly (exit ' +
            code +
            '). Diagnostics are disabled until the window reloads.'
        );
      }
      this.onExit(proc);
    });

    this._out.appendLine('[server] started, pid=' + proc.pid);
    this.initialize();
  }

  onExit(proc) {
    if (this._proc !== proc) return;
    this._proc = null;
    this._opened.clear();
    for (const [, entry] of this._pending) {
      clearTimeout(entry.timer);
      entry.resolve(null);
    }
    this._pending.clear();
  }

  initialize() {
    const folder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0];
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
      .then(() => {
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
    for (const timer of this._debounce.values()) clearTimeout(timer);
    this._debounce.clear();
    if (this._proc) {
      try {
        this._proc.kill();
      } catch {
        /* ignore */
      }
    }
    this._proc = null;
    this._parser = null;
    this._opened.clear();
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
  const { pythonPath, serverScript } = client.readConfig();
  if (!serverScript || !fs.existsSync(serverScript)) {
    vscode.window.showErrorMessage(
      'GAMA-L doctor failed: server script not found (' + (serverScript || 'empty') + ')'
    );
    return;
  }
  cp.execFile(
    pythonPath,
    [serverScript, 'doctor', '--json'],
    { timeout: 60000, windowsHide: true, maxBuffer: 8 * 1024 * 1024 },
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
        const ok = parsed.ok === true;
        if (ok) {
          vscode.window.showInformationMessage('GAMA-L doctor: ok = true');
        } else {
          let failed = parsed.failed;
          if (Array.isArray(failed)) failed = failed.join(', ');
          if (typeof failed !== 'string' || !failed.trim()) failed = 'see details';
          vscode.window.showWarningMessage('GAMA-L doctor: ok = false — failed: ' + failed);
        }
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
