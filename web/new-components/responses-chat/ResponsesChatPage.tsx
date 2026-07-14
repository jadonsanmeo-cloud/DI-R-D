import { useResponsesChat } from '@/hooks/use-responses-chat';
import { DeleteOutlined } from '@ant-design/icons';
import { useEffect, useRef, useState } from 'react';
import ChatComposer from './ChatComposer';
import MessageList from './MessageList';
import SourceEditor from './SourceEditor';

export default function ResponsesChatPage() {
  const [sources, setSources] = useState(['data/data.csv']);
  const [draft, setDraft] = useState('');
  const composerRef = useRef<HTMLDivElement>(null);
  const { messages, isStreaming, validationError, submit, stop, clear } = useResponsesChat();

  useEffect(() => {
    if (!isStreaming) composerRef.current?.querySelector('textarea')?.focus();
  }, [isStreaming]);

  const send = async () => {
    const accepted = await submit(draft, sources);
    if (accepted) setDraft('');
  };

  return (
    <main className='relative flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-[#f4f1ea] dark:bg-[#171815]'>
      <div
        className='pointer-events-none absolute inset-0 opacity-[0.28] dark:opacity-[0.08]'
        style={{
          backgroundImage:
            'linear-gradient(rgba(72,65,52,0.08) 1px, transparent 1px), linear-gradient(90deg, rgba(72,65,52,0.08) 1px, transparent 1px)',
          backgroundSize: '28px 28px',
        }}
      />
      <header className='relative z-10 border-b border-stone-200/80 bg-[#f4f1ea]/85 px-4 py-4 backdrop-blur dark:border-white/10 dark:bg-[#171815]/85 sm:px-6'>
        <div className='mx-auto flex max-w-5xl items-center justify-between gap-4'>
          <div>
            <p className='text-[11px] font-semibold uppercase tracking-[0.2em] text-amber-700 dark:text-amber-300'>
              Responses API
            </p>
            <h1 className='font-serif text-2xl text-stone-900 dark:text-stone-50'>Data Intelligence</h1>
          </div>
          {messages.length > 0 && (
            <button
              type='button'
              disabled={isStreaming}
              onClick={clear}
              className='inline-flex items-center gap-2 rounded-xl px-3 py-2 text-sm text-stone-500 transition hover:bg-stone-200/70 hover:text-stone-800 disabled:opacity-40 dark:hover:bg-white/10 dark:hover:text-stone-100'
            >
              <DeleteOutlined /> Clear
            </button>
          )}
        </div>
        <div className='mx-auto mt-4 max-w-5xl'>
          <SourceEditor sources={sources} disabled={isStreaming} onChange={setSources} />
        </div>
      </header>

      <div className='relative z-10 min-h-0 flex-1 overflow-y-auto'>
        <MessageList messages={messages} onSuggestion={setDraft} />
      </div>

      <div
        ref={composerRef}
        className='relative z-10 bg-gradient-to-t from-[#f4f1ea] via-[#f4f1ea] to-transparent pt-4 dark:from-[#171815] dark:via-[#171815]'
      >
        <ChatComposer
          value={draft}
          isStreaming={isStreaming}
          error={validationError}
          onChange={setDraft}
          onSubmit={send}
          onStop={stop}
        />
      </div>
    </main>
  );
}
