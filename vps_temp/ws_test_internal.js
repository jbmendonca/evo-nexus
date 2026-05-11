const WebSocket = require("ws");
const ws = new WebSocket("ws://127.0.0.1:32352/terminal/ws");
ws.on("open", () => { console.log("CONNECTED_OK"); ws.send(JSON.stringify({type:"ping"})); });
ws.on("message", d => console.log("MSG:", d.toString()));
ws.on("error", e => console.log("ERR:", e.message));
ws.on("close", (c,r) => { console.log("CLOSE code=" + c); process.exit(0); });
setTimeout(() => { ws.close(); process.exit(0); }, 5000);
