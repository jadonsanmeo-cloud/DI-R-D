import type { ResponsesChatMessage } from '@/types/responses';
import { LoadingOutlined, RobotOutlined, UserOutlined } from '@ant-design/icons';
import { useEffect, useRef } from 'react';
import PipelineTimeline from './PipelineTimeline';

const SUGGESTIONS = [
  'What columns and patterns are present in this data?',
  'What is the total revenue?',
  'Create a concise report about this data corpus.',
];

type Props = {
  messages: ResponsesChatMessage[];
  onSuggestion: (value: string) => void;
};

export default function MessageList({ messages, onSuggestion }: Props) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className='flex min-h-full flex-col items-center justify-center px-4 py-16 text-center'>
        <div className='mb-5 grid h-16 w-16 place-items-center rounded-2xl border border-amber-200 bg-amber-100/80 text-2xl text-amber-900 shadow-sm dark:border-amber-300/20 dark:bg-amber-300/10 dark:text-amber-100'>
          <RobotOutlined />
        </div>
        <h2 className='max-w-2xl font-serif text-3xl leading-tight text-stone-900 dark:text-stone-50'>
          Ask the corpus, not a generic chatbot.
        </h2>
        <p className='mt-3 max-w-xl text-sm leading-6 text-stone-500 dark:text-stone-400'>
          The workflow will inspect your query, plan the analysis, select an engine, and return an answer or report.
        </p>
        <div className='mt-7 grid w-full max-w-2xl gap-2 sm:grid-cols-3'>
          {SUGGESTIONS.map(suggestion => (
            <button
              key={suggestion}
              type='button'
              onClick={() => onSuggestion(suggestion)}
              className='rounded-2xl border border-stone-200 bg-white/70 px-4 py-3 text-left text-sm leading-5 text-stone-700 transition hover:-translate-y-0.5 hover:border-amber-400 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:text-stone-200'
            >
              {suggestion}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className='mx-auto w-full max-w-3xl space-y-6 px-4 py-8 sm:px-6'>
      {messages.map(message => (
        <article
          key={message.id}
          className={`flex animate-fade-up gap-3 ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
        >
          {message.role === 'assistant' && (
            <span className='mt-1 grid h-8 w-8 flex-none place-items-center rounded-xl bg-amber-100 text-amber-900 dark:bg-amber-300/10 dark:text-amber-100'>
              <RobotOutlined />
            </span>
          )}
          <div
            className={`max-w-[88%] rounded-2xl px-4 py-3 sm:max-w-[78%] ${
              message.role === 'user'
                ? 'rounded-br-md bg-[#24332d] text-stone-50 shadow-sm'
                : 'rounded-bl-md border border-stone-200/80 bg-white/85 text-stone-800 shadow-[0_12px_35px_rgba(49,46,39,0.05)] dark:border-white/10 dark:bg-[#20221e]/90 dark:text-stone-100'
            }`}
          >
            <div className='whitespace-pre-wrap break-words text-sm leading-7'>
              {message.content ||
                (message.status === 'streaming' ? (
                  <span className='inline-flex items-center gap-2 text-stone-400'>
                    <LoadingOutlined spin /> Preparing response
                  </span>
                ) : (
                  ''
                ))}
            </div>
            {message.role === 'assistant' &&
              message.stages &&
              (message.status === 'streaming' || message.status === 'failed') && (
                <PipelineTimeline stages={message.stages} />
              )}
            {message.status === 'failed' && (
              <p className='mt-3 text-sm text-red-600 dark:text-red-300' role='alert'>
                {message.error || 'The request failed.'}
              </p>
            )}
            {message.status === 'cancelled' && (
              <p className='mt-3 text-xs text-stone-400' role='status'>
                Request cancelled.
              </p>
            )}
          </div>
          {message.role === 'user' && (
            <span className='mt-1 grid h-8 w-8 flex-none place-items-center rounded-xl bg-stone-200 text-stone-700 dark:bg-white/10 dark:text-stone-200'>
              <UserOutlined />
            </span>
          )}
        </article>
      ))}
      <div ref={endRef} />
    </div>
  );
}
