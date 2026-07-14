import type { PipelineStage } from '@/types/responses';
import { CheckOutlined, CloseOutlined, LoadingOutlined } from '@ant-design/icons';

export default function PipelineTimeline({ stages }: { stages: PipelineStage[] }) {
  return (
    <div className='mt-4 rounded-xl border border-stone-200/80 bg-stone-50/80 p-3 dark:border-white/10 dark:bg-black/15'>
      <p className='mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-stone-400'>Workflow</p>
      <div className='space-y-1.5'>
        {stages.map(stage => (
          <div key={stage.id} className='flex items-center gap-2.5 text-xs'>
            <span
              className={`grid h-5 w-5 place-items-center rounded-full border ${
                stage.status === 'completed'
                  ? 'border-emerald-500 bg-emerald-500 text-white'
                  : stage.status === 'running'
                    ? 'border-amber-500 bg-amber-50 text-amber-700 dark:bg-amber-300/10 dark:text-amber-200'
                    : stage.status === 'failed'
                      ? 'border-red-500 bg-red-500 text-white'
                      : 'border-stone-300 text-stone-300 dark:border-stone-600 dark:text-stone-600'
              }`}
            >
              {stage.status === 'completed' && <CheckOutlined />}
              {stage.status === 'running' && <LoadingOutlined spin />}
              {stage.status === 'failed' && <CloseOutlined />}
            </span>
            <span className={stage.status === 'pending' ? 'text-stone-400' : 'text-stone-700 dark:text-stone-200'}>
              {stage.label}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
