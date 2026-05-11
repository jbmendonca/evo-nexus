const WebSocket = require("ws");
const ws = new WebSocket("ws://127.0.0.1:32352/ws");
ws.on("open", () => { console.log("WS_CONNECTED"); ws.send(JSON.stringify({type:"ping"})); });
ws.on("message", d => { console.log("WS_MSG:" + d.toString()); ws.close(); });
ws.on("error", e => { console.log("WS_ERR:" + e.message); process.exit(1); });
ws.on("close", (c,r) => { console.log("WS_CLOSE_CODE=" + c); process.exit(0); });
setTimeout(() => { console.log("WS_TIMEOUT"); process.exit(1); }, 5000);
