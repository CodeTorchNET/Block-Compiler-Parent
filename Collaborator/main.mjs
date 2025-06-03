import * as Y from 'yjs';
import { LeveldbPersistence } from 'y-leveldb';
import http from 'http';
import { WebSocketServer, WebSocket } from 'ws';
import { URL } from 'url';

import * as syncProtocol from 'y-protocols/sync.js';
import * as awarenessProtocol from 'y-protocols/awareness.js';
import * as encoding from 'lib0/encoding.js';
import * as decoding from 'lib0/decoding.js';
import * as mutex from 'lib0/mutex.js';

// --- Configuration ---
const HOST = process.env.HOST || 'localhost';
const PORT = parseInt(process.env.PORT || '4444', 10);
const PERSISTENCE_DIR = process.env.PERSISTENCE_DIR || './db';
const CLEANUP_INTERVAL_MS = parseInt(process.env.CLEANUP_INTERVAL_MS || (60 * 1000).toString(), 10);
const INACTIVITY_TIMEOUT_MS = parseInt(process.env.INACTIVITY_TIMEOUT_MS || (5 * 60 * 1000).toString(), 10);
const AUTH_API_TIMEOUT_MS = parseInt(process.env.AUTH_API_TIMEOUT_MS || '5000', 10);
const DEV_MODE = process.env.DEV_MODE === 'true' || process.env.DEV_MODE === 'True';

// New: Python Backend API Configuration for authentication
const PYTHON_INTERNAL_API_URL = process.env.PYTHON_INTERNAL_API_URL || 'http://localhost:5000';
const PYTHON_INTERNAL_API_KEY = process.env.PYTHON_INTERNAL_API_KEY; // From example.env

// --- Globals ---
const docs = new Map();
let persistenceProvider = null;
let ldbInstance = null;

// Message types
const MESSAGE_SYNC = 0;
const MESSAGE_AWARENESS = 1;

const { CONNECTING, OPEN, CLOSING, CLOSED } = WebSocket;


// --- Custom WSSharedDoc implementation ---
class MyWSSharedDoc extends Y.Doc {
  constructor(name) {
    super({ gc: true });
    this.name = name;
    this.mux = mutex.createMutex();

    /** @type {Map<WebSocket, Set<number>>} */
    this.conns = new Map();

    this.awareness = new awarenessProtocol.Awareness(this);
    /**
     * Handles Yjs document updates and broadcasts them.
     * @param {Uint8Array} update The update vector.
     * @param {any} origin The origin of the update (e.g., the WebSocket connection, or 'persistence').
     */
    this._docUpdateHandler = (update, origin) => {
      if (origin === 'persistence_load') return;

      const encoder = encoding.createEncoder();
      encoding.writeVarUint(encoder, MESSAGE_SYNC);
      syncProtocol.writeUpdate(encoder, update);
      const message = encoding.toUint8Array(encoder);

      this.conns.forEach((_, conn) => {
        // Ensure only authenticated connections receive updates if conn.authenticated is still desired
        if (conn !== origin && conn.authenticated) {
          send(this, conn, message);
        }
      });
    };

    /**
     * Handles awareness updates and broadcasts them.
     * @param {{ added: Array<number>, updated: Array<number>, removed: Array<number> }} changes
     * @param {any} origin The origin of the awareness update (e.g., the WebSocket connection).
     */
    this._awarenessUpdateHandler = ({ added, updated, removed }, origin) => {
      const changedClients = added.concat(updated).concat(removed);
      if (changedClients.length === 0) return;

      const awarenessUpdate = awarenessProtocol.encodeAwarenessUpdate(this.awareness, changedClients);
      const encoder = encoding.createEncoder();
      encoding.writeVarUint(encoder, MESSAGE_AWARENESS);
      encoding.writeVarUint8Array(encoder, awarenessUpdate);
      const message = encoding.toUint8Array(encoder);

      this.conns.forEach((_, conn) => {
        // Ensure only authenticated connections receive awareness updates
        if (conn !== origin && conn.authenticated) {
          send(this, conn, message);
        }
      });
    };

    this.on('update', this._docUpdateHandler);
    this.awareness.on('update', this._awarenessUpdateHandler);
  }

  destroy() {
    console.log(`[DEBUG] Destroying MyWSSharedDoc for room: ${this.name}`);
    super.destroy();
    this.awareness.destroy();

    this.conns.forEach((_, conn) => {
      try {
        if (conn.readyState === OPEN || conn.readyState === CONNECTING) {
          conn.terminate();
        }
      } catch (e) { /* ignore */ }
    });
    this.conns.clear();
  }
}

// --- Persistence Setup ---
if (PERSISTENCE_DIR) {
  console.log(`[INFO] Initializing LevelDB persistence in ${PERSISTENCE_DIR}`);
  ldbInstance = new LeveldbPersistence(PERSISTENCE_DIR);
  persistenceProvider = {
    bindState: async (docName, ydoc) => {
      const persistedYDoc = await ldbInstance.getYDoc(docName);
      const newUpdates = Y.encodeStateAsUpdate(ydoc);

      Y.applyUpdate(ydoc, Y.encodeStateAsUpdate(persistedYDoc), 'persistence_load');

      if (newUpdates.length > 2) {
        await ldbInstance.storeUpdate(docName, newUpdates);
      }

      ydoc.on('update', (update, origin) => {
        if (origin !== 'persistence_load') {
          ldbInstance.storeUpdate(docName, update).catch(err => console.error(`[ERROR] Failed to store update for ${docName}:`, err)); // Use ldbInstance
        }
      });
    },
    writeState: async (docName, ydoc) => {
      await ldbInstance.flushDocument(docName);
    },
    clearDocument: async (docName) => {
      try {
        await ldbInstance.clearDocument(docName);
        console.log(`[INFO] Cleared persisted state for room: ${docName}`);
      } catch (err) {
        if (err.notFound) {
          console.log(`[DEBUG] No persisted state found for room: ${docName} to clear.`);
        } else {
          console.error(`[ERROR] Failed to clear persisted state for room ${docName}:`, err);
        }
      }
    },
    provider: ldbInstance // Also ensure the provider itself is stored
  };
}

// --- WebSocket Send Utility ---
const send = (doc, conn, m) => {
  if (conn.readyState === OPEN) {
    try {
      conn.send(m, err => {
        if (err != null) {
          console.warn(`[WARN] Error sending message to client in room ${doc.name}:`, err);
          closeConn(doc, conn);
        }
      });
    } catch (e) {
      console.error(`[ERROR] Exception sending message to client in room ${doc.name}:`, e);
      closeConn(doc, conn);
    }
  } else if (conn.readyState !== CONNECTING) {
    console.log(`[DEBUG] Connection not open or connecting (state: ${conn.readyState}) for room ${doc.name}. Cleaning up.`);
    closeConn(doc, conn);
  }
};

// --- Message Listener for Incoming WebSocket Messages ---
const messageListener = (conn, doc, message) => {
  try {
    const encoder = encoding.createEncoder();
    const decoder = decoding.createDecoder(message);
    const messageType = decoding.readVarUint(decoder);

    switch (messageType) {
      case MESSAGE_SYNC:
        encoding.writeVarUint(encoder, MESSAGE_SYNC);
        syncProtocol.readSyncMessage(decoder, encoder, doc, conn);
        if (encoding.length(encoder) > 1) {
          send(doc, conn, encoding.toUint8Array(encoder));
        }
        break;
      case MESSAGE_AWARENESS:
        awarenessProtocol.applyAwarenessUpdate(doc.awareness, decoding.readVarUint8Array(decoder), conn);
        break;
      default:
        console.error(`[ERROR] Unknown message type received from client in room ${doc.name}: ${messageType}`);
    }
  } catch (err) {
    console.error(`[ERROR] Error processing message in room ${doc.name}:`, err);
  }
};

// --- Get or Create Yjs Document ---
const getYDoc = (docName) => {
  let doc = docs.get(docName);
  if (doc === undefined) {
    doc = new MyWSSharedDoc(docName);

    if (persistenceProvider) {
      doc.mux(() => {
        if (persistenceProvider && typeof persistenceProvider.bindState === 'function') {
          persistenceProvider.bindState(docName, doc)
            .catch(err => console.error(`[ERROR] Persistence bindState failed for ${docName}:`, err));
        }
      });
    }
    docs.set(docName, doc);
    console.log(`[INFO] Created new document instance for room: ${docName}`);
  }
  return doc;
};

// --- Close WebSocket Connection ---
const closeConn = (doc, conn, code = 1000, reason = 'Normal Closure') => {
  if (doc.conns.has(conn)) {
    const controlledClientIDs = doc.conns.get(conn);
    doc.conns.delete(conn);

    if (controlledClientIDs && controlledClientIDs.size > 0) {
      awarenessProtocol.removeAwarenessStates(doc.awareness, Array.from(controlledClientIDs), conn);
    }
    console.log(`[INFO] Connection closed for room ${doc.name}. Remaining conns for room: ${doc.conns.size}`);

    if (doc.conns.size === 0 && persistenceProvider && typeof persistenceProvider.writeState === 'function') {
      persistenceProvider.writeState(doc.name, doc)
        .then(() => console.log(`[DEBUG] Persisted state for ${doc.name} on last disconnect.`))
        .catch(err => console.error(`[ERROR] Failed to writeState for ${doc.name} on last disconnect:`, err));
    }
  }
  if (conn.readyState === OPEN || conn.readyState === CONNECTING) {
    try {
      conn.close(code, reason);
    } catch (e) {
      console.warn(`[WARN] Error closing connection for room ${doc.name}:`, e);
    }
  }
};

/**
 * Verifies the user token by calling the Python internal API.
 * @param {string} username The username to verify.
 * @param {string} token The authentication token.
 * @param {string} roomid The room to which the user is trying to connect
 * @returns {Promise<boolean>} True if valid, false otherwise.
 */
async function verifyUserToken(username, token, roomid) {
  if (!PYTHON_INTERNAL_API_URL || !PYTHON_INTERNAL_API_KEY) {
    console.error('[AUTH] PYTHON_INTERNAL_API_URL or PYTHON_INTERNAL_API_KEY not configured.');
    return false;
  }

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), AUTH_API_TIMEOUT_MS);
    const response = await fetch(`${PYTHON_INTERNAL_API_URL}/internal/collaborationAuth`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        token: PYTHON_INTERNAL_API_KEY,
        user_id: username,
        user_token: token,
        roomID: roomid
      }),
      signal: controller.signal
    });
    clearTimeout(timeoutId);

    if (!response.ok) {
      const errorText = await response.text();
      console.error(`[AUTH] Python API error response (${response.status}): ${errorText}`);
      return false;
    }

    const data = await response.json();
    if (data.status === 'success' && data.username === username) {
      return true;
    } else {
      console.warn(`[AUTH] Token verification failed for user '${username}'. Reason: ${data.message || 'Unknown'}`);
      return false;
    }
  } catch (e) {
    console.error(`[AUTH] Error calling Python authentication API:`, e);
    if (e.name === 'AbortError') {
      console.error(`[AUTH] Python authentication API call timed out after ${AUTH_API_TIMEOUT_MS}ms.`);
    } else {
      console.error(`[AUTH] Error calling Python authentication API:`, e);
    }
    return false;
  }
}


// --- Setup WebSocket Connection function ---
const setupWSConnection = (conn, req, roomName, clientUsername) => {
  conn.binaryType = 'arraybuffer';
  conn.authenticated = true; // Auth already performed in upgrade handler
  conn.username = clientUsername;
  console.log(`[AUTH] User '${conn.username}' authenticated successfully for room: ${roomName}`);

  const doc = getYDoc(roomName);
  doc.conns.set(conn, new Set());

  conn.on('message', (messageData) => {
    const uint8Message = messageData instanceof Uint8Array ? messageData : new Uint8Array(messageData);
    messageListener(conn, doc, uint8Message);
  });

  let pongReceived = true;
  const pingInterval = setInterval(() => {
    if (!doc.conns.has(conn)) {
      clearInterval(pingInterval);
      return;
    }
    if (!pongReceived) {
      console.log(`[INFO] Ping timeout for client '${conn.username}' in room ${roomName}. Closing connection.`);
      closeConn(doc, conn);
      return;
    }
    if (conn.readyState === OPEN) {
      pongReceived = false;
      try {
        conn.ping();
      } catch (e) {
        console.warn(`[WARN] Error pinging client '${conn.username}' in room ${roomName}:`, e);
        closeConn(doc, conn);
      }
    }
  }, 30000);

  conn.on('pong', () => {
    pongReceived = true;
  });

  conn.on('close', (code, reason) => {
    console.log(`[INFO] Client '${conn.username}' disconnected from room: ${roomName}. Code: ${code}, Reason: ${reason ? reason.toString() : 'N/A'}`);
    closeConn(doc, conn, code, reason);
    clearInterval(pingInterval);
    const existingDoc = docs.get(roomName);
    if (existingDoc && existingDoc.conns.size === 0) {
      roomLastActive.set(roomName, Date.now());
    }
  });

  conn.on('error', (error) => {
    console.error(`[ERROR] WebSocket error for client '${conn.username}' in room ${roomName}:`, error);
    closeConn(doc, conn);
  });

  const syncEncoder = encoding.createEncoder();
  encoding.writeVarUint(syncEncoder, MESSAGE_SYNC);
  syncProtocol.writeSyncStep1(syncEncoder, doc);
  send(doc, conn, encoding.toUint8Array(syncEncoder));

  const awarenessStates = doc.awareness.getStates();
  if (awarenessStates.size > 0) {
    const awarenessEncoder = encoding.createEncoder();
    encoding.writeVarUint(awarenessEncoder, MESSAGE_AWARENESS);
    const awarenessUpdateBytes = awarenessProtocol.encodeAwarenessUpdate(doc.awareness, Array.from(awarenessStates.keys()));
    encoding.writeVarUint8Array(awarenessEncoder, awarenessUpdateBytes);
    send(doc, conn, encoding.toUint8Array(awarenessEncoder));
  }

  roomLastActive.set(roomName, Date.now());
  console.log(`[INFO] Client '${conn.username}' connected to room: ${roomName}. Total conns for room: ${doc.conns.size}`);
};


// --- HTTP Server (for health checks, stats) ---
const server = http.createServer((request, response) => {
  if (DEV_MODE) {
    if (request.url === '/health') {
      response.writeHead(200, { 'Content-Type': 'application/json' });
      let clientCount = 0;
      docs.forEach(doc => clientCount += doc.conns.size);
      response.end(JSON.stringify({ status: 'ok', rooms: docs.size, clients: clientCount }));
    } else if (request.url === '/stats' || request.url === '/') {
      response.writeHead(200, { 'Content-Type': 'text/html' });
      let html = '<h1>Y-Websocket Server Status</h1>';
      html += `<p>Listening on: ws://${HOST}:${PORT}</p>`;
      html += `<p>Persistence: ${PERSISTENCE_DIR ? `LevelDB at ${PERSISTENCE_DIR}` : 'Disabled'}</p>`;
      html += `<h2>Active Rooms (${docs.size}):</h2><ul>`;
      docs.forEach((doc, roomName) => {
        html += `<li>${roomName} (Connections: ${doc.conns.size}, Awareness: ${doc.awareness.getStates().size})</li>`;
      });
      html += '</ul>';
      response.end(html);
    } else {
      response.writeHead(404, { 'Content-Type': 'text/plain' });
      response.end('Not Found');
    }
  } else {
    if (request.url === '/') {
      response.writeHead(200, { 'Content-Type': 'text/plain' });
      response.end('CodeTorch Collaborator server is running.');
    } else {
      response.writeHead(404, { 'Content-Type': 'text/plain' });
      response.end('Not Found');
    }
  }
});

// --- WebSocket Server Setup ---
const wss = new WebSocketServer({ noServer: true });

const roomLastActive = new Map(); // roomName -> timestamp for cleanup

// This listener is now only for already authenticated and upgraded connections
wss.on('connection', (ws, req, roomName, clientUsername) => {
  // Call the setup function with the pre-authenticated details
  setupWSConnection(ws, req, roomName, clientUsername);
});

// --- Pre-handshake authentication on HTTP server's 'upgrade' event ---
// This must be attached to the HTTP server *before* it starts listening, 
// and before the WebSocketServer is given control of the 'upgrade' event (hence noServer: true)
server.on('upgrade', async (request, socket, head) => {
  const url = new URL(request.url, `http://${request.headers.host}`);
  const roomName = url.pathname.slice(1).split('?')[0].replace(/^\/*/, '');

  if (!roomName) {
    console.warn('[AUTH] Connection attempt without room name (upgrade). Denying.');
    socket.write('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n'); // Proper HTTP error
    socket.destroy();
    return;
  }

  const clientUsername = url.searchParams.get('username');
  const clientToken = url.searchParams.get('token');

  if (!clientUsername || !clientToken) {
    console.warn(`[AUTH] Connection attempt to room ${roomName} missing username or token (upgrade). Denying.`);
    socket.write('HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n');
    socket.destroy();
    return;
  }

  const isAuthenticated = await verifyUserToken(clientUsername, clientToken, roomName);

  if (!isAuthenticated) {
    console.warn(`[AUTH] Authentication failed for user '${clientUsername}' in room ${roomName} (upgrade). Denying.`);
    socket.write('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
    socket.destroy();
    return;
  }

  // If authentication succeeds, then delegate to the WebSocket server
  wss.handleUpgrade(request, socket, head, (ws) => {
    // Emit the 'connection' event with the ws instance and authenticated info
    wss.emit('connection', ws, request, roomName, clientUsername);
  });
});

// --- Room Cleanup Logic ---
function cleanupRooms() {
  const now = Date.now();
  if (docs.size === 0 && roomLastActive.size === 0) return;

  console.log(`[INFO] Running cleanup check. Rooms in memory: ${docs.size}, Rooms tracked for activity: ${roomLastActive.size}`);

  docs.forEach((doc, roomName) => {
    if (doc.conns.size > 0) {
      roomLastActive.set(roomName, now);
    } else {
      const lastActive = roomLastActive.get(roomName);
      if (lastActive && (now - lastActive > INACTIVITY_TIMEOUT_MS)) {
        console.log(`[INFO] Cleaning up inactive room (memory): ${roomName}. Last active: ${new Date(lastActive).toISOString()}`);

        let cleanupPromise;
        if (persistenceProvider && typeof persistenceProvider.clearDocument === 'function') {
          cleanupPromise = persistenceProvider.clearDocument(roomName)
            .then(() => console.log(`[INFO] Cleared persisted state for ${roomName} before unloading.`));
        } else {
          // Fallback if persistenceProvider or clearDocument is not configured/available
          cleanupPromise = Promise.resolve();
        }

        cleanupPromise
          .then(() => {
            doc.destroy();
            docs.delete(roomName);
            roomLastActive.delete(roomName);
            console.log(`[INFO] Room ${roomName} unloaded from memory. Rooms remaining: ${docs.size}`);
          })
          .catch(err => console.error(`[ERROR] Failed to finalize document ${roomName} during cleanup:`, err));

      } else if (!lastActive && doc.conns.size === 0) {
        console.warn(`[WARN] Room ${roomName} has no connections and no last active time. Marking for potential cleanup.`);
        roomLastActive.set(roomName, 0);
      }
    }
  });

  roomLastActive.forEach((_, roomName) => {
    if (!docs.has(roomName)) {
      roomLastActive.delete(roomName);
    }
  });
}

const cleanupIntervalId = setInterval(cleanupRooms, CLEANUP_INTERVAL_MS);

// --- Start Server & Graceful Shutdown ---
server.listen(PORT, HOST, () => {
  console.log(`[INFO] Y-Websocket server listening on ws://${HOST}:${PORT}`);
  if (persistenceProvider) {
    console.log(`[INFO] Persistence enabled: ${PERSISTENCE_DIR}`);
  } else {
    console.log(`[WARN] Persistence is disabled. Data will be lost on server restart.`);
  }
  console.log(`[INFO] Room cleanup interval: ${CLEANUP_INTERVAL_MS / 1000}s, Inactivity timeout (memory): ${INACTIVITY_TIMEOUT_MS / 1000 / 60}min`);
  if (!PYTHON_INTERNAL_API_URL || !PYTHON_INTERNAL_API_KEY) {
    console.warn(`[AUTH] WARNING: Python internal API URL or Key is missing. Authentication will fail!`);
  } else {
    console.log(`[AUTH] Python Auth API URL: ${PYTHON_INTERNAL_API_URL}`);
  }
});

const shutdown = async (signal) => {
  console.log(`\n[INFO] Received ${signal}. Shutting down gracefully...`);
  clearInterval(cleanupIntervalId);

  console.log('[INFO] Closing all client WebSocket connections...');
  wss.clients.forEach(client => {
    if (client.readyState === OPEN) {
      client.close(1001, 'Server shutting down');
    }
  });
  await new Promise(resolve => setTimeout(resolve, 250));

  console.log('[INFO] Closing WebSocket server...');
  await new Promise((resolve, reject) => {
    wss.close((err) => {
      if (err) console.error('[ERROR] Error closing WebSocket server:', err);
      console.log('[INFO] WebSocket server closed.');
      resolve();
    });
    setTimeout(() => {
      console.warn('[WARN] WebSocket server close timed out. Forcing HTTP server close.');
      resolve();
    }, 3000);
  });

  console.log('[INFO] Closing HTTP server...');
  await new Promise((resolve, reject) => {
    server.close((err) => {
      if (err) console.error('[ERROR] Error closing HTTP server:', err);
      console.log('[INFO] HTTP server closed.');
      resolve();
    });
    setTimeout(() => {
      console.warn('[WARN] HTTP server close timed out.');
      resolve();
    }, 3000);
  });

  if (persistenceProvider && typeof persistenceProvider.writeState === 'function') {
    console.log('[INFO] Writing state for all documents in memory before exit...');
    const flushPromises = [];
    docs.forEach((doc, roomName) => {
      if (doc.conns.size > 0 || roomLastActive.has(roomName)) {
        console.log(`[DEBUG] Queuing flush for room: ${roomName}`);
        flushPromises.push(
          persistenceProvider.writeState(roomName, doc)
            .catch(err => console.error(`[ERROR] Failed to write state for ${roomName} on shutdown:`, err))
        );
      }
    });
    if (flushPromises.length > 0) {
      try {
        await Promise.all(flushPromises);
        console.log('[INFO] All relevant documents in memory finalized.');
      } catch (error) {
        console.error('[ERROR] Error finalizing documents during shutdown:', error);
      }
    } else {
      console.log('[INFO] No active documents needed flushing.');
    }
  }

  console.log('[INFO] Shutdown complete.');
  process.exit(0);
};

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));