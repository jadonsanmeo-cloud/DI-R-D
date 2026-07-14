import { DatabaseOutlined, DeleteOutlined, DownOutlined, PlusOutlined, UpOutlined } from '@ant-design/icons';
import { useEffect, useState } from 'react';

type Props = {
  sources: string[];
  disabled: boolean;
  onChange: (sources: string[]) => void;
};

export default function SourceEditor({ sources, disabled, onChange }: Props) {
  const [open, setOpen] = useState(true);
  const activeCount = sources.filter(source => source.trim()).length;

  useEffect(() => {
    setOpen(!window.matchMedia('(max-width: 639px)').matches);
  }, []);

  const update = (index: number, value: string) =>
    onChange(sources.map((source, itemIndex) => (itemIndex === index ? value : source)));

  const remove = (index: number) => {
    const next = sources.filter((_, itemIndex) => itemIndex !== index);
    onChange(next.length ? next : ['']);
  };

  return (
    <section className='rounded-2xl border border-stone-200/80 bg-white/75 shadow-[0_12px_40px_rgba(49,46,39,0.06)] backdrop-blur dark:border-white/10 dark:bg-[#20221e]/85'>
      <button
        type='button'
        onClick={() => setOpen(value => !value)}
        className='flex w-full items-center justify-between gap-4 px-4 py-3 text-left'
        aria-expanded={open}
      >
        <span className='flex items-center gap-3'>
          <span className='grid h-9 w-9 place-items-center rounded-xl bg-amber-100 text-amber-800 dark:bg-amber-300/10 dark:text-amber-200'>
            <DatabaseOutlined />
          </span>
          <span>
            <span className='block text-sm font-semibold text-stone-800 dark:text-stone-100'>Data corpus</span>
            <span className='block text-xs text-stone-500 dark:text-stone-400'>
              {activeCount} active source{activeCount === 1 ? '' : 's'}
            </span>
          </span>
        </span>
        {open ? <UpOutlined className='text-xs text-stone-400' /> : <DownOutlined className='text-xs text-stone-400' />}
      </button>

      {open && (
        <div className='border-t border-stone-200/70 px-4 py-4 dark:border-white/10'>
          <div className='space-y-2'>
            {sources.map((source, index) => (
              <div key={index} className='flex items-center gap-2'>
                <input
                  value={source}
                  disabled={disabled}
                  onChange={event => update(index, event.target.value)}
                  placeholder='data/data.csv'
                  aria-label={`Corpus source ${index + 1}`}
                  className='min-w-0 flex-1 rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-800 outline-none transition focus:border-amber-500 focus:ring-2 focus:ring-amber-500/15 disabled:cursor-not-allowed disabled:opacity-60 dark:border-white/10 dark:bg-black/20 dark:text-stone-100'
                />
                <button
                  type='button'
                  disabled={disabled}
                  onClick={() => remove(index)}
                  aria-label={`Remove source ${source || index + 1}`}
                  className='grid h-10 w-10 place-items-center rounded-xl text-stone-400 transition hover:bg-red-50 hover:text-red-600 disabled:opacity-40 dark:hover:bg-red-500/10'
                >
                  <DeleteOutlined />
                </button>
              </div>
            ))}
          </div>
          <button
            type='button'
            disabled={disabled}
            onClick={() => onChange([...sources, ''])}
            className='mt-3 inline-flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm font-medium text-amber-800 transition hover:bg-amber-50 disabled:opacity-40 dark:text-amber-200 dark:hover:bg-amber-300/10'
          >
            <PlusOutlined /> Add source
          </button>
          <p className='mt-2 text-xs leading-5 text-stone-500 dark:text-stone-400'>
            Paths are resolved by the backend under DATA_CORPUS_ROOT.
          </p>
        </div>
      )}
    </section>
  );
}
