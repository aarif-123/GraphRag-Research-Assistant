const code = `graph TD
  A[Network-on-Chip (NOC) Architecture] -->|Design Methodology| B["Packet Switched 
  Network-on-Chip"]
  C["Another \\n Multi-line \\n Label"]`;

const preProcessed = code.replace(/"([^"\\\r\n]*(?:\\.[^"\\\r\n]*)*)"/g, (match, p1) => {
    return '"' + p1.replace(/\\n/g, '<br>') + '"';
});

// Let's use the dotAll or [^"] pattern to match quotes spanning lines:
const preProcessedLines = code.replace(/"([^"\\]*(?:\\.[^"\\]*)*)"/gs, (match, p1) => {
    return '"' + p1.replace(/\r?\n/g, '<br>').replace(/\\n/g, '<br>').replace(/<br>\s+/g, '<br>') + '"';
});

console.log("Original:\n", code);
console.log("\nPreprocessed:\n", preProcessedLines);
