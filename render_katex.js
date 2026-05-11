#!/usr/bin/env node
/**
 * Batch KaTeX renderer.
 * Takes JSON array of {latex, display} from stdin, outputs JSON array of HTML strings.
 */
const katex = require('katex');

let input = '';
process.stdin.on('data', (chunk) => input += chunk);
process.stdin.on('end', () => {
    try {
        const items = JSON.parse(input);
        const results = items.map((item) => {
            const html = katex.renderToString(item.latex, {
                displayMode: item.display_mode || false,
                throwOnError: false,
                trust: true,
            });
            return { index: item.index, html: html };
        });
        process.stdout.write(JSON.stringify(results));
    } catch (err) {
        process.stderr.write(err.message);
        process.exit(1);
    }
});
