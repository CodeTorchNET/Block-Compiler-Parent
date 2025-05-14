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
const DEV_MODE = process.env.DEV_MODE === 'true' || process.env.DEV_MODE === 'True';

// --- Globals ---
const docs = new Map();
let persistenceProvider = null;

// Message types
const MESSAGE_SYNC = 0;
const MESSAGE_AWARENESS = 1;
// const MESSAGE_AUTH = 2; // For future auth implementation

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
        if (conn !== origin) {
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
        if (conn !== origin) {
          send(this, conn, message);
        }
      });
    };

    this.on('update', this._docUpdateHandler); // y-protocols uses 'update' (v1) and handles origin correctly
    this.awareness.on('update', this._awarenessUpdateHandler);
  }

  destroy() {
    console.log(`[DEBUG] Destroying MyWSSharedDoc for room: ${this.name}`);
    super.destroy(); // Calls Y.Doc's destroy, which removes its listeners including 'update'
    this.awareness.destroy(); // This clears awareness states and its 'update' listeners
    
    // Connections should ideally be closed and removed from `this.conns` by `closeConn`
    // before `doc.destroy()` is called from the cleanup job.
    // This is a safeguard.
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
  const ldb = new LeveldbPersistence(PERSISTENCE_DIR);
  persistenceProvider = {
    bindState: async (docName, ydoc) => {
      const persistedYDoc = await ldb.getYDoc(docName);
      const newUpdates = Y.encodeStateAsUpdate(ydoc);

      // Apply persisted data to the in-memory doc. Use a unique origin for this.
      Y.applyUpdate(ydoc, Y.encodeStateAsUpdate(persistedYDoc), 'persistence_load');
      
      if (newUpdates.length > 2) { // Empty update is usually 2 bytes (0,0)
        await ldb.storeUpdate(docName, newUpdates);
      }

      ydoc.on('update', (update, origin) => {
        // Avoid re-persisting updates that were just loaded from persistence
        if (origin !== 'persistence_load') {
          ldb.storeUpdate(docName, update).catch(err => console.error(`[ERROR] Failed to store update for ${docName}:`, err));
        }
      });
    },
    writeState: async (docName, ydoc) => { // ydoc is MyWSSharedDoc
      await ldb.flushDocument(docName);
    },
    provider: ldb
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
  } else if (conn.readyState !== CONNECTING) { // If not OPEN or CONNECTING, assume problematic
    console.log(`[DEBUG] Connection not open or connecting (state: ${conn.readyState}) for room ${doc.name}. Cleaning up.`);
    closeConn(doc, conn);
  }
};

// --- Message Listener for Incoming WebSocket Messages ---
const messageListener = (conn, doc, message) => {
  try {
    const encoder = encoding.createEncoder(); // Encoder for the reply message
    const decoder = decoding.createDecoder(message);
    const messageType = decoding.readVarUint(decoder);

    switch (messageType) {
      case MESSAGE_SYNC:
        encoding.writeVarUint(encoder, MESSAGE_SYNC);
        syncProtocol.readSyncMessage(decoder, encoder, doc, conn); // `conn` is passed as origin
        if (encoding.length(encoder) > 1) { // Only send if there's a reply
          send(doc, conn, encoding.toUint8Array(encoder));
        }
        break;
      case MESSAGE_AWARENESS:
        awarenessProtocol.applyAwarenessUpdate(doc.awareness, decoding.readVarUint8Array(decoder), conn); // `conn` as origin
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
const closeConn = (doc, conn) => {
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
  // Ensure the WebSocket is actually closed
  if (conn.readyState === OPEN || conn.readyState === CONNECTING) {
    try {
      conn.terminate();
    } catch (e) {
      console.warn(`[WARN] Error terminating connection for room ${doc.name}:`, e);
    }
  }
};

// --- Setup WebSocket Connection ---
const setupWSConnection = (conn, req) => {
  conn.binaryType = 'arraybuffer';
  const url = new URL(req.url, `ws://${req.headers.host}`);
  const roomName = url.pathname.slice(1).split('?')[0].replace(/^\/*/, ''); // Remove leading slashes

  if (!roomName) {
    console.warn('[WARN] Connection attempt without room name. Closing.');
    conn.close(1008, 'Room name required');
    return;
  }

  const doc = getYDoc(roomName);
  doc.conns.set(conn, new Set()); // Add conn to doc's map; awareness protocol populates the Set of clientIDs.

  conn.on('message', (messageData) => {
    const uint8Message = messageData instanceof Uint8Array ? messageData : new Uint8Array(messageData);
    messageListener(conn, doc, uint8Message);
  });

  let pongReceived = true;
  const pingInterval = setInterval(() => {
    if (!doc.conns.has(conn)) { // Connection might have been closed by other means
        clearInterval(pingInterval);
        return;
    }
    if (!pongReceived) {
      console.log(`[INFO] Ping timeout for client in room ${roomName}. Closing connection.`);
      closeConn(doc, conn); 
      return; // Exit to avoid trying to ping a closed connection
    }
    if (conn.readyState === OPEN) {
      pongReceived = false;
      try {
        conn.ping();
      } catch (e) {
        console.warn(`[WARN] Error pinging client in room ${roomName}:`, e);
        closeConn(doc, conn);
      }
    }
  }, 30000);

  conn.on('pong', () => {
    pongReceived = true;
  });

  conn.on('close', (code, reason) => {
    console.log(`[INFO] Client disconnected from room: ${roomName}. Code: ${code}, Reason: ${reason ? reason.toString() : 'N/A'}`);
    closeConn(doc, conn);
    clearInterval(pingInterval);
    const existingDoc = docs.get(roomName);
    if (existingDoc && existingDoc.conns.size === 0) {
      roomLastActive.set(roomName, Date.now());
    }
  });

  conn.on('error', (error) => {
    console.error(`[ERROR] WebSocket error for client in room ${roomName}:`, error);
    closeConn(doc, conn);
  });

  // Initial sync step 1 and existing awareness states
  const syncEncoder = encoding.createEncoder();
  encoding.writeVarUint(syncEncoder, MESSAGE_SYNC);
  syncProtocol.writeSyncStep1(syncEncoder, doc);
  send(doc, conn, encoding.toUint8Array(syncEncoder));

  const awarenessStates = doc.awareness.getStates();
  if (awarenessStates.size > 0) {
    const awarenessEncoder = encoding.createEncoder();
    encoding.writeVarUint(awarenessEncoder, MESSAGE_AWARENESS);
    encoding.writeVarUint8Array(awarenessEncoder, awarenessProtocol.encodeAwarenessUpdate(doc.awareness, Array.from(awarenessStates.keys())));
    send(doc, conn, encoding.toUint8Array(awarenessEncoder));
  }
  
  roomLastActive.set(roomName, Date.now());
  console.log(`[INFO] Client connected to room: ${roomName}. Total conns for room: ${doc.conns.size}`);
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
    if (req.url === '/') {
      response.writeHead(200, { 'Content-Type': 'text/plain' });
      response.end('CodeTorch Collaborator server is running.');
    } else {
      response.writeHead(404, { 'Content-Type': 'text/plain' });
      response.end('Not Found');
    }
  }
});

// --- WebSocket Server Setup ---
const wss = new WebSocketServer({ server });
const roomLastActive = new Map(); // roomName -> timestamp for cleanup

wss.on('connection', setupWSConnection);

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
        
        const cleanupPromise = persistenceProvider && typeof persistenceProvider.writeState === 'function'
          ? persistenceProvider.writeState(roomName, doc)
          : Promise.resolve();

        cleanupPromise
          .then(() => {
            if (persistenceProvider) console.log(`[INFO] Flushed/finalized document ${roomName} before unloading.`);
            doc.destroy()
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
});

const shutdown = async (signal) => {
  console.log(`\n[INFO] Received ${signal}. Shutting down gracefully...`);
  clearInterval(cleanupIntervalId);

  console.log('[INFO] Closing all client WebSocket connections...');
  // wss.clients is a Set, not an array, so no forEach with index
  wss.clients.forEach(client => {
    if (client.readyState === OPEN) {
      client.close(1001, 'Server shutting down'); // 1001: Going Away
    }
  });
  // Give a moment for client close messages to be sent
  await new Promise(resolve => setTimeout(resolve, 250));


  console.log('[INFO] Closing WebSocket server...');
  await new Promise((resolve, reject) => {
    wss.close((err) => {
      if (err) {
        console.error('[ERROR] Error closing WebSocket server:', err);
      }
      console.log('[INFO] WebSocket server closed.');
      resolve();
    });
    // Timeout for wss.close() as it can sometimes hang
    setTimeout(() => {
        console.warn('[WARN] WebSocket server close timed out. Forcing HTTP server close.');
        resolve();
    }, 3000);
  });

  console.log('[INFO] Closing HTTP server...');
   await new Promise((resolve, reject) => {
    server.close((err) => {
        if (err) {
            console.error('[ERROR] Error closing HTTP server:', err);
            // Do not reject, allow process to exit
        }
        console.log('[INFO] HTTP server closed.');
        resolve();
    });
     // Add a timeout for server.close()
    setTimeout(() => {
        console.warn('[WARN] HTTP server close timed out.');
        resolve(); // Proceed even if server.close hangs
    }, 3000);
  });


  if (persistenceProvider && typeof persistenceProvider.writeState === 'function') {
    console.log('[INFO] Writing state for all documents in memory before exit...');
    const flushPromises = [];
    docs.forEach((doc, roomName) => {
      if (doc.conns.size > 0 || roomLastActive.has(roomName)) { // Only flush active or recently active rooms
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