import type { EditableExecutionSpec, ResponseConfirmationState } from '@/types/responses';
import { parseSpecMarkdown, specToMarkdown } from '@/utils/spec-markdown';
import { CheckOutlined, EditOutlined, ReloadOutlined } from '@ant-design/icons';
import { Alert, Button, Input } from 'antd';
import dynamic from 'next/dynamic';
import { useEffect, useMemo, useState } from 'react';

const LightweightMarkdown = dynamic(() => import('./LightweightMarkdown'), {
  ssr: false,
});

type Props = {
  confirmation: ResponseConfirmationState;
  onDecision: (action: 'revise' | 'confirm', spec: EditableExecutionSpec, feedback: string) => Promise<void>;
};

export default function SpecConfirmationCard({ confirmation, onDecision }: Props) {
  const generatedMarkdown = useMemo(() => specToMarkdown(confirmation.spec), [confirmation.spec]);
  const [markdown, setMarkdown] = useState(generatedMarkdown);
  const [feedback, setFeedback] = useState('');
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setMarkdown(generatedMarkdown);
    setFeedback('');
    setEditing(false);
    setError('');
  }, [confirmation.revision, generatedMarkdown]);

  const expiresLabel = useMemo(() => new Date(confirmation.expiresAt).toLocaleString(), [confirmation.expiresAt]);

  const revise = async () => {
    let nextSpec = confirmation.spec;
    try {
      if (editing) nextSpec = parseSpecMarkdown(markdown, confirmation.spec);
    } catch (parseError) {
      setError(parseError instanceof Error ? parseError.message : 'The Markdown spec is invalid.');
      return;
    }
    const markdownChanged = markdown.trim() !== generatedMarkdown.trim();
    if (!feedback.trim() && !markdownChanged) {
      setError('Edit the plan or describe what the workflow should revise.');
      return;
    }
    setError('');
    await onDecision('revise', nextSpec, feedback.trim());
  };

  const cancelEditing = () => {
    setMarkdown(generatedMarkdown);
    setEditing(false);
    setError('');
  };

  return (
    <section className='mx-5 mb-6 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_12px_40px_rgba(15,23,42,0.06)] dark:border-slate-800 dark:bg-[#17191f]'>
      <header className='flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-5 py-4 dark:border-slate-800'>
        <div className='flex items-start gap-3'>
          <div className='mt-1 h-9 w-1 rounded-full bg-blue-500' />
          <div>
            <div className='text-sm font-semibold text-slate-900 dark:text-slate-100'>Proposed analysis plan</div>
            <div className='mt-1 text-xs text-slate-500'>
              Revision {confirmation.revision} · intent: {confirmation.intent} · expires {expiresLabel}
            </div>
          </div>
        </div>
        {!editing && (
          <Button icon={<EditOutlined />} disabled={confirmation.submitting} onClick={() => setEditing(true)}>
            Edit Markdown
          </Button>
        )}
      </header>

      <div className='px-5 py-5'>
        {editing ? (
          <div>
            <div className='mb-2 text-xs font-medium uppercase tracking-[0.14em] text-slate-400'>Markdown editor</div>
            <Input.TextArea
              value={markdown}
              onChange={event => setMarkdown(event.target.value)}
              autoSize={{ minRows: 16, maxRows: 28 }}
              className='font-mono text-[13px] leading-6'
              spellCheck={false}
            />
            <div className='mt-3 flex justify-end gap-2'>
              <Button disabled={confirmation.submitting} onClick={cancelEditing}>
                Cancel edit
              </Button>
              <Button type='primary' icon={<ReloadOutlined />} loading={confirmation.submitting} onClick={revise}>
                Submit edited plan
              </Button>
            </div>
          </div>
        ) : (
          <div className='spec-markdown text-sm leading-7 text-slate-700 dark:text-slate-200'>
            <LightweightMarkdown>{markdown}</LightweightMarkdown>
          </div>
        )}

        {!editing && (
          <div className='mt-6 rounded-xl border border-slate-200 bg-slate-50/70 p-4 dark:border-slate-800 dark:bg-slate-900/40'>
            <div className='mb-2 text-sm font-medium text-slate-800 dark:text-slate-200'>
              Ask the workflow to revise
            </div>
            <Input.TextArea
              value={feedback}
              onChange={event => setFeedback(event.target.value)}
              autoSize={{ minRows: 2, maxRows: 5 }}
              placeholder='Example: compare by month, include a chart, and explain missing values...'
              disabled={confirmation.submitting}
            />
            <div className='mt-3 flex flex-wrap justify-end gap-2'>
              <Button icon={<ReloadOutlined />} disabled={confirmation.submitting || !feedback.trim()} onClick={revise}>
                Revise with feedback
              </Button>
              <Button
                type='primary'
                icon={<CheckOutlined />}
                loading={confirmation.submitting}
                onClick={() => onDecision('confirm', confirmation.spec, '')}
              >
                Confirm and run
              </Button>
            </div>
          </div>
        )}

        {(error || confirmation.error) && (
          <Alert className='mt-4' type='error' showIcon message={error || confirmation.error} />
        )}
      </div>
    </section>
  );
}
