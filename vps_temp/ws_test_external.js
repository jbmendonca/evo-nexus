// External WebSocket test to production endpoint
const WebSocket = require("ws");
const url = "wss://agents.tkrtecnologia.net/terminal/ws";
console.log("Connecting to:", url);
const ws = new WebSocket(url);
ws.on("open", () => {
  console.log("EXT_WS_CONNECTED");
  ws.send(JSON.stringify({ type: "ping" }));
});
ws.on("message", d => {
  console.log("EXT_WS_MSG:" + d.toString());
  ws.close();
});
ws.on("error", e => {
  console.log("EXT_WS_ERR:" + e.message);
  process.exit(1);
});
ws.on("close", (code, reason) => {
  console.log("EXT_WS_CLOSE code=" + code + " reason=" + reason);
  process.exit(0);
});
setTimeout(() => { console.log("EXT_WS_TIMEOUT"); process.exit(1); }, 10000);
