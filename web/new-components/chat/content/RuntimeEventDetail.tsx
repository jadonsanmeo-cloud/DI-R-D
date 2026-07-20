import type { RuntimeEventOutputContent } from '@/utils/responses-sse';
import {
  CheckCircleFilled,
  CloseCircleFilled,
  CopyOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  InfoCircleFilled,
  LoadingOutlined,
} from '@ant-design/icons';
import dynamic from 'next/dynamic';
import React, { memo } from 'react';

const CodePreview = dynamic(
  () => import('@/components/chat/chat-content/code-preview').then(module => module.CodePreview),
  {
    ssr: false,
    loading: () => <div className='h-40 animate-pulse bg-slate-900' />,
  },
);

const ACRONYMS: Record<string, string> = {
  api: 'API',
  css: 'CSS',
  html: 'HTML',
  id: 'ID',
  ids: 'IDs',
  js: 'JavaScript',
  json: 'JSON',
  mcp: 'MCP',
  sql: 'SQL',
  url: 'URL',
  urls: 'URLs',
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value);

const formatLabel = (value: string): string =>
  value
    .replace(/[._-]+/g, ' ')
    .replace(/\b\w+/g, word => ACRONYMS[word.toLowerCase()] || `${word.charAt(0).toUpperCase()}${word.slice(1)}`);

const artifactName = (ref: string): string => {
  const path = ref.replace(/^artifact:\/\//, '');
  return path.split('/').filter(Boolean).pop() || 'Artifact';
};

const PrimitiveValue: React.FC<{ value: string | number | boolean }> = ({ value }) => {
  if (typeof value === 'boolean') {
    return (
      <span
        className={`inline-flex w-fit items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ${
          value
            ? 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-300 dark:ring-emerald-800'
            : 'bg-gray-100 text-gray-600 ring-1 ring-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:ring-gray-700'
        }`}
      >
        {value ? 'Yes' : 'No'}
      </span>
    );
  }

  if (typeof value === 'number') {
    return (
      <span className='font-mono text-sm font-semibold text-slate-800 dark:text-slate-100'>
        {value.toLocaleString()}
      </span>
    );
  }

  if (value.startsWith('artifact://')) {
    return (
      <div className='flex min-w-0 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900/60'>
        <FileTextOutlined className='shrink-0 text-sky-600 dark:text-sky-400' />
        <div className='min-w-0'>
          <div className='truncate text-xs font-semibold text-slate-700 dark:text-slate-200'>{artifactName(value)}</div>
          <div className='truncate font-mono text-[10px] text-slate-400'>{value}</div>
        </div>
      </div>
    );
  }

  const isLong = value.length > 180 || value.includes('\n');
  return (
    <div
      className={
        isLong
          ? 'whitespace-pre-wrap break-words text-xs leading-5 text-slate-600 dark:text-slate-300'
          : 'break-words text-xs font-medium text-slate-700 dark:text-slate-200'
      }
    >
      {value}
    </div>
  );
};

const StructuredValue: React.FC<{ value: unknown; depth?: number }> = ({ value, depth = 0 }) => {
  if (value === null || value === undefined) {
    return <span className='text-xs italic text-slate-400'>Not provided</span>;
  }

  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return <PrimitiveValue value={value} />;
  }

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className='text-xs italic text-slate-400'>None</span>;
    const primitiveOnly = value.every(item => ['string', 'number', 'boolean'].includes(typeof item));
    if (primitiveOnly) {
      return (
        <div className='flex flex-wrap gap-1.5'>
          {value.map((item, index) => (
            <span
              key={`${String(item)}-${index}`}
              className='max-w-full break-all rounded-md bg-slate-100 px-2 py-1 text-[11px] font-medium text-slate-600 ring-1 ring-inset ring-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700'
            >
              {String(item)}
            </span>
          ))}
        </div>
      );
    }
    return (
      <div className='space-y-2'>
        {value.map((item, index) => (
          <div
            key={index}
            className='rounded-lg border border-slate-200/80 bg-white p-3 dark:border-slate-700 dark:bg-slate-900/50'
          >
            <StructuredValue value={item} depth={depth + 1} />
          </div>
        ))}
      </div>
    );
  }

  if (!isRecord(value)) return <PrimitiveValue value={String(value)} />;

  const validationStatus = typeof value.status === 'string' ? value.status.toLowerCase() : '';
  const validationFeedback = typeof value.feedback === 'string' ? value.feedback : '';
  if (validationStatus && validationFeedback) {
    const passed = ['pass', 'passed', 'completed', 'valid'].includes(validationStatus);
    return (
      <div
        className={`rounded-xl border p-3 ${
          passed
            ? 'border-emerald-200 bg-emerald-50/70 dark:border-emerald-800 dark:bg-emerald-900/15'
            : 'border-rose-200 bg-rose-50/70 dark:border-rose-800 dark:bg-rose-900/15'
        }`}
      >
        <div className='mb-2 flex items-center gap-2'>
          {passed ? (
            <CheckCircleFilled className='text-emerald-500' />
          ) : (
            <CloseCircleFilled className='text-rose-500' />
          )}
          <span
            className={`text-xs font-bold uppercase tracking-wide ${passed ? 'text-emerald-700' : 'text-rose-700'}`}
          >
            {formatLabel(validationStatus)}
          </span>
        </div>
        <p className='m-0 text-xs leading-5 text-slate-600 dark:text-slate-300'>{validationFeedback}</p>
      </div>
    );
  }

  const entries = Object.entries(value);
  if (entries.length === 0) return <span className='text-xs italic text-slate-400'>No details</span>;

  return (
    <div className={depth > 1 ? 'space-y-2' : 'grid grid-cols-1 gap-2 xl:grid-cols-2'}>
      {entries.map(([key, item]) => (
        <div
          key={key}
          className='min-w-0 rounded-lg border border-slate-200/80 bg-white px-3 py-2.5 dark:border-slate-700 dark:bg-slate-900/50'
        >
          <div className='mb-1.5 text-[10px] font-bold uppercase tracking-[0.08em] text-slate-400'>
            {formatLabel(key)}
          </div>
          <StructuredValue value={item} depth={depth + 1} />
        </div>
      ))}
    </div>
  );
};

const sectionTitle = (key: string): string => {
  if (key === 'inputs') return 'Context used';
  if (key === 'outputs') return 'Result';
  if (key === 'plan') return 'Execution plan';
  return formatLabel(key);
};

const RuntimeEventDetail: React.FC<{ content: RuntimeEventOutputContent }> = ({ content }) => {
  const detailEntries = Object.entries(content.details).filter(([, value]) => value !== null && value !== undefined);
  const running = ['pending', 'running'].includes(content.status.toLowerCase());
  const failed = ['failed', 'cancelled', 'error'].includes(content.status.toLowerCase());
  const accent = failed ? 'bg-rose-500' : running ? 'bg-sky-500' : 'bg-emerald-500';
  const icon = failed ? (
    <CloseCircleFilled className='text-rose-500' />
  ) : running ? (
    <LoadingOutlined spin className='text-sky-500' />
  ) : (
    <CheckCircleFilled className='text-emerald-500' />
  );

  return (
    <div className='shrink-0 overflow-hidden rounded-xl border border-slate-200 bg-slate-50/70 shadow-[0_12px_35px_-28px_rgba(15,23,42,0.8)] dark:border-slate-700 dark:bg-slate-950/35'>
      <div className={`h-1 w-full ${accent}`} />
      <div className='border-b border-slate-200 bg-white px-4 py-4 dark:border-slate-700 dark:bg-slate-900/70'>
        <div className='mb-2 flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400'>
          <span>{formatLabel(content.phase)}</span>
          <span className='h-1 w-1 rounded-full bg-slate-300 dark:bg-slate-600' />
          <span>{formatLabel(content.eventType)}</span>
        </div>
        <div className='flex items-start gap-3'>
          <div className='mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-50 ring-1 ring-slate-200 dark:bg-slate-800 dark:ring-slate-700'>
            {icon}
          </div>
          <div className='min-w-0'>
            <div className='text-sm font-semibold leading-5 text-slate-800 dark:text-slate-100'>{content.summary}</div>
            {content.description && content.description !== content.summary && (
              <p className='mb-0 mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400'>{content.description}</p>
            )}
          </div>
        </div>
      </div>

      <div className='space-y-4 p-4'>
        {detailEntries.length === 0 && content.artifactRefs.length === 0 && !content.code && (
          <div className='flex items-center gap-2 rounded-lg border border-dashed border-slate-300 px-3 py-4 text-xs text-slate-500 dark:border-slate-700 dark:text-slate-400'>
            <InfoCircleFilled className='text-slate-400' />
            This step completed without additional structured output.
          </div>
        )}

        {content.code && (
          <section className='min-w-0 max-w-full'>
            <div className='mb-2 flex items-center justify-between gap-3'>
              <div className='flex min-w-0 items-center gap-2'>
                <FileTextOutlined className='shrink-0 text-sky-500' />
                <div className='min-w-0'>
                  <h4 className='m-0 truncate text-xs font-bold text-slate-700 dark:text-slate-200'>
                    {content.code.name}
                  </h4>
                  <div className='text-[10px] font-medium uppercase tracking-[0.08em] text-slate-400'>
                    Generated {content.code.language} source
                    {content.code.truncated ? ' - preview truncated' : ''}
                  </div>
                </div>
              </div>
              <button
                type='button'
                onClick={() => navigator.clipboard.writeText(content.code?.content || '')}
                className='inline-flex shrink-0 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] font-semibold text-slate-500 transition-colors hover:border-slate-300 hover:text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400 dark:hover:text-slate-200'
              >
                <CopyOutlined />
                Copy
              </button>
            </div>
            <div className='min-w-0 max-w-full overflow-hidden rounded-xl border border-slate-700 bg-[#0f172a]'>
              <CodePreview
                code={content.code.content}
                language={content.code.language}
                customStyle={{ background: '#0f172a', margin: 0, borderRadius: 0 }}
              />
            </div>
          </section>
        )}

        {detailEntries.map(([key, value]) => (
          <section key={key}>
            <div className='mb-2 flex items-center gap-2'>
              {key === 'inputs' ? (
                <DatabaseOutlined className='text-slate-400' />
              ) : (
                <InfoCircleFilled className='text-slate-400' />
              )}
              <h4 className='m-0 text-xs font-bold uppercase tracking-[0.08em] text-slate-500 dark:text-slate-300'>
                {sectionTitle(key)}
              </h4>
            </div>
            <StructuredValue value={value} />
          </section>
        ))}

        {content.artifactRefs.length > 0 && (
          <section>
            <div className='mb-2 flex items-center gap-2'>
              <FileTextOutlined className='text-slate-400' />
              <h4 className='m-0 text-xs font-bold uppercase tracking-[0.08em] text-slate-500 dark:text-slate-300'>
                Files produced
              </h4>
            </div>
            <div className='grid grid-cols-1 gap-2 xl:grid-cols-2'>
              {content.artifactRefs.map(ref => (
                <PrimitiveValue key={ref} value={ref} />
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
};

export default memo(RuntimeEventDetail);
