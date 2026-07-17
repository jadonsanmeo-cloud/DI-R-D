import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const paths = {
  left: path.join(webRoot, 'new-components/chat/content/ManusLeftPanel.tsx'),
  right: path.join(webRoot, 'new-components/chat/content/ManusRightPanel.tsx'),
  markdown: path.join(webRoot, 'new-components/chat/content/LightweightMarkdown.tsx'),
};

const errors = [];
const read = filePath => (fs.existsSync(filePath) ? fs.readFileSync(filePath, 'utf8') : '');
const left = read(paths.left);
const right = read(paths.right);
const markdown = read(paths.markdown);

for (const [label, source] of [
  ['ManusLeftPanel', left],
  ['ManusRightPanel', right],
  ['LightweightMarkdown', markdown],
]) {
  if (source.includes('@antv/gpt-vis')) errors.push(`${label} imports @antv/gpt-vis`);
  if (source.includes('components/chat/chat-content/config')) {
    errors.push(`${label} imports the heavyweight chat Markdown configuration`);
  }
}

if (!left.includes("./LightweightMarkdown")) {
  errors.push('ManusLeftPanel does not use LightweightMarkdown');
}
if (!right.includes("./LightweightMarkdown")) {
  errors.push('ManusRightPanel does not use LightweightMarkdown');
}
if (!markdown) errors.push('LightweightMarkdown.tsx does not exist');

const staticCodePreview = /^import\s+\{?\s*CodePreview\b[^;]*from\s+['"]/m;
const staticAdvancedChart = /^import\s+AdvancedChart\b[^;]*from\s+['"]/m;
if (staticCodePreview.test(right)) errors.push('ManusRightPanel statically imports CodePreview');
if (staticAdvancedChart.test(right)) errors.push('ManusRightPanel statically imports AdvancedChart');

if (errors.length > 0) {
  console.error(errors.map(error => `- ${error}`).join('\n'));
  process.exit(1);
}

console.log('Manus module boundaries are lightweight.');
