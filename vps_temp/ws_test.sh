#!/bin/sh
cd /workspace/dashboard/terminal-server
node -e '
var ws = require("ws");
var c = new ws.WebSocket("ws://127.0.0.1:32352/ws");
c.on("open", function() { console.log("WS_CONNECTED_OK"); c.send("{\"type\":\"ping\"}"); });
c.on("message", function(d) { console.log("WS_MSG:" + d.toString()); c.close(); });
c.on("error", function(e) { console.log("WS_ERR:" + e.message); process.exit(1); });
c.on("close", function(code) { console.log("WS_CLOSE=" + code); process.exit(0); });
setTimeout(function() { console.log("WS_TIMEOUT"); process.exit(1); }, 5000);
'
