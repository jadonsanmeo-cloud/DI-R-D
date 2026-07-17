import 'katex/dist/katex.min.css';
import type { Components } from 'react-markdown';
import ReactMarkdown from 'react-markdown';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';

const components: Components = {
  a: ({ children, node: _node, ...props }) => (
    <a {...props} target='_blank' rel='noreferrer' className='text-blue-600 hover:underline dark:text-blue-400'>
      {children}
    </a>
  ),
  table: ({ children, node: _node, ...props }) => (
    <div className='my-3 overflow-x-auto'>
      <table {...props} className='min-w-full border-collapse text-sm'>
        {children}
      </table>
    </div>
  ),
  th: ({ children, node: _node, ...props }) => (
    <th
      {...props}
      className='border border-gray-200 bg-gray-50 px-3 py-2 text-left font-semibold dark:border-gray-700 dark:bg-gray-800'
    >
      {children}
    </th>
  ),
  td: ({ children, node: _node, ...props }) => (
    <td {...props} className='border border-gray-200 px-3 py-2 dark:border-gray-700'>
      {children}
    </td>
  ),
  pre: ({ children, node: _node, ...props }) => (
    <pre {...props} className='my-3 overflow-x-auto rounded-lg bg-slate-950 p-4 text-xs text-slate-100'>
      {children}
    </pre>
  ),
  code: ({ children, className, node: _node, ...props }) => (
    <code {...props} className={className || 'rounded bg-gray-100 px-1 py-0.5 font-mono text-[0.9em] dark:bg-gray-800'}>
      {children}
    </code>
  ),
};

export const preprocessManusMath = (value: string): string => {
  const codeBlocks: string[] = [];
  let content = value.replace(/(```[\s\S]*?```|`[^`\n]+`)/g, match => {
    codeBlocks.push(match);
    return `<<MANUS_CODE_BLOCK_${codeBlocks.length - 1}>>`;
  });

  content = content
    .replace(/\\\\\[/g, '$$')
    .replace(/\\\\\]/g, '$$')
    .replace(/\\\\\(/g, '$')
    .replace(/\\\\\)/g, '$')
    .replace(/\\\[/g, '$$')
    .replace(/\\\]/g, '$$')
    .replace(/\\\(/g, '$')
    .replace(/\\\)/g, '$')
    .replace(/([^\n])\$\$/g, '$1\n\n$$')
    .replace(/\$\$([^\n])/g, '$$\n\n$1')
    .replace(/\$(?=\d)/g, '\\$');

  return content.replace(/<<MANUS_CODE_BLOCK_(\d+)>>/g, (_match, index: string) => codeBlocks[Number(index)]);
};

const LightweightMarkdown = ({ children }: { children: string }) => (
  <ReactMarkdown components={components} remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
    {preprocessManusMath(children)}
  </ReactMarkdown>
);

export default LightweightMarkdown;
