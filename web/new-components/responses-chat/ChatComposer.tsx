import { ArrowUpOutlined, StopOutlined } from '@ant-design/icons';
import type { KeyboardEvent } from 'react';

type Props = {
  value: string;
  isStreaming: boolean;
  error: string | null;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onStop: () => void;
};

export default function ChatComposer({ value, isStreaming, error, onChange, onSubmit, onStop }: Props) {
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      if (!isStreaming) onSubmit();
    }
  };

  return (
    <div className='mx-auto w-full max-w-3xl px-4 pb-4 sm:px-6 sm:pb-6'>
      <div className='rounded-[22px] border border-stone-300/80 bg-white/90 p-2 shadow-[0_20px_60px_rgba(49,46,39,0.12)] backdrop-blur dark:border-white/10 dark:bg-[#20221e]/95'>
        <textarea
          value={value}
          onChange={event => onChange(event.target.value)}
          onKeyDown={onKeyDown}
          rows={3}
          aria-label='Ask the data corpus'
          placeholder='Ask a question, or leave blank to analyze the corpus...'
          className='max-h-40 min-h-[72px] w-full resize-none bg-transparent px-3 py-2 text-[15px] leading-6 text-stone-900 outline-none placeholder:text-stone-400 dark:text-stone-100'
        />
        <div className='flex items-center justify-between gap-3 px-1 pb-1'>
          <span className='hidden text-xs text-stone-400 sm:inline'>Enter to send · Shift+Enter for a new line</span>
          {isStreaming ? (
            <button
              type='button'
              onClick={onStop}
              className='ml-auto inline-flex h-10 items-center gap-2 rounded-xl bg-red-600 px-4 text-sm font-semibold text-white transition hover:bg-red-700'
            >
              <StopOutlined /> Stop
            </button>
          ) : (
            <button
              type='button'
              onClick={onSubmit}
              className='ml-auto inline-flex h-10 items-center gap-2 rounded-xl bg-[#24332d] px-4 text-sm font-semibold text-white transition hover:bg-[#18251f]'
            >
              <ArrowUpOutlined /> Analyze
            </button>
          )}
        </div>
      </div>
      <div className='min-h-6 px-2 pt-1 text-xs text-red-600 dark:text-red-300' aria-live='polite'>
        {error}
      </div>
    </div>
  );
}
