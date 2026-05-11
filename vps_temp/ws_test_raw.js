const WebSocket = require("ws");
const url = "wss://agents.tkrtecnologia.net/terminal/ws";
console.log("Connecting to:", url);
const ws = new WebSocket(url);

ws.on("open", () => {
  console.log("CONNECTED");
  ws.send(JSON.stringify({ type: "join_session", sessionId: "ws-test-session-001" }));
});

ws.on("message", d => {
  console.log("RAW_MSG:", d.toString());
});

ws.on("error", e => {
  console.log("WS_ERROR:", e.message);
  process.exit(1);
});

ws.on("close", (code, reason) => {
  console.log("CLOSED code=" + code);
  process.exit(0);
});

setTimeout(() => { ws.close(); process.exit(0); }, 10000);
