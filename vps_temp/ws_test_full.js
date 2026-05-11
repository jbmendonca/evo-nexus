// Long-lived WebSocket test to verify bridge stability
const WebSocket = require("ws");
const url = "wss://agents.tkrtecnologia.net/terminal/ws";
console.log("Connecting to:", url);
const ws = new WebSocket(url);

ws.on("open", () => {
  console.log("[" + new Date().toISOString() + "] CONNECTED");
  // Send join_session like the frontend would
  ws.send(JSON.stringify({ type: "join_session", sessionId: "ws-test-session-001" }));
});

ws.on("message", d => {
  const msg = JSON.parse(d.toString());
  console.log("[" + new Date().toISOString() + "] MSG type=" + msg.type);
  if (msg.type === "session_joined") {
    console.log("  → Session joined, history length:", (msg.chatHistory || []).length);
    // Send a ping to verify bidirectional
    ws.send(JSON.stringify({ type: "ping" }));
  }
  if (msg.type === "pong") {
    console.log("  → Bidirectional bridge confirmed!");
    console.log("  → Keeping alive for 5s to verify stability...");
    setTimeout(() => {
      console.log("  → Test PASSED! Closing cleanly.");
      ws.close(1000, "test-complete");
    }, 5000);
  }
});

ws.on("error", e => {
  console.log("[" + new Date().toISOString() + "] ERROR:", e.message);
  process.exit(1);
});

ws.on("close", (code, reason) => {
  console.log("[" + new Date().toISOString() + "] CLOSED code=" + code + " reason=" + reason);
  process.exit(code === 1000 ? 0 : 1);
});

// Absolute timeout
setTimeout(() => { console.log("TIMEOUT - 20s"); process.exit(1); }, 20000);
