const fs = require('fs');
const content = fs.readFileSync('data.js', 'utf8');
// Remove "const quizData =" and the trailing ";"
let jsonStr = content.replace(/const\s+quizData\s+=\s+/, '').trim();
if (jsonStr.endsWith(';')) {
    jsonStr = jsonStr.slice(0, -1);
}

// Use eval to parse the JS object (dangerous but okay for a local personal tool on a known file)
try {
    const data = eval('(' + jsonStr + ')');
    const output = `const quizData = ${JSON.stringify(data)};`;
    fs.writeFileSync('data.js.fixed', output, 'utf-8');
    console.log('Success: Saved to data.js.fixed');
} catch (e) {
    console.error('Error parsing data.js:', e);
}
