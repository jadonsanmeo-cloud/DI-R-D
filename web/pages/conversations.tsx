import { delDialogue, getDialogueList } from '@/client/api/request';
import { apiInterceptors } from '@/client/api/tools/interceptors';
import type { IChatDialogueSchema } from '@/types/chat';
import { DeleteOutlined, MessageOutlined } from '@ant-design/icons';
import { Skeleton, Tooltip, message } from 'antd';
import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

function ConversationsPage() {
  const { t } = useTranslation();
  const [dialogueList, setDialogueList] = useState<IChatDialogueSchema[]>([]);
  const [loading, setLoading] = useState(false);

  const fetchDialogueList = useCallback(async () => {
    setLoading(true);
    try {
      const [, data] = await apiInterceptors(getDialogueList());
      if (data && Array.isArray(data)) {
        setDialogueList(data.filter(item => item.chat_mode === 'backend_qa_flow'));
      }
    } catch (e) {
      console.error('Failed to fetch dialogue list', e);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleDeleteDialogue = useCallback(
    async (e: React.MouseEvent, convUid: string) => {
      e.stopPropagation();
      e.preventDefault();
      try {
        const [err] = await apiInterceptors(delDialogue(convUid));
        if (!err) {
          setDialogueList(prev => prev.filter(d => d.conv_uid !== convUid));
          message.success(t('cmp.deleted'));
        }
      } catch (error) {
        console.error('Failed to delete dialogue', error);
      }
    },
    [t],
  );

  const formatRelativeTime = useCallback(
    (dateStr?: string) => {
      if (!dateStr) return '';
      const date = new Date(dateStr);
      const now = new Date();
      const diffMs = now.getTime() - date.getTime();
      const diffMins = Math.floor(diffMs / 60000);
      const diffHours = Math.floor(diffMs / 3600000);
      const diffDays = Math.floor(diffMs / 86400000);
      if (diffMins < 1) return t('cmp.justNow');
      if (diffMins < 60) return t('cmp.minutesAgo', { n: diffMins });
      if (diffHours < 24) return t('cmp.hoursAgo', { n: diffHours });
      if (diffDays < 7) return t('cmp.daysAgo', { n: diffDays });
      return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
    },
    [t],
  );

  useEffect(() => {
    fetchDialogueList();
  }, [fetchDialogueList]);

  return (
    <div className='h-full overflow-y-auto bg-white dark:bg-[#232734] px-8 py-6'>
      <div className='max-w-4xl mx-auto'>
        <div className='mb-6'>
          <h1 className='text-2xl font-semibold text-gray-900 dark:text-gray-100'>{t('all_tasks')}</h1>
        </div>

        {loading ? (
          <Skeleton active paragraph={{ rows: 8 }} />
        ) : dialogueList.length > 0 ? (
          <div className='space-y-2'>
            {dialogueList.map(conv => (
              <Link
                key={conv.conv_uid}
                href={`/?id=${conv.conv_uid}`}
                className='flex items-start gap-3 px-4 py-3 rounded-xl border border-gray-100 dark:border-gray-700 text-sm transition-colors group hover:bg-[#F1F5F9] dark:hover:bg-theme-dark'
              >
                <MessageOutlined className='text-gray-400 flex-shrink-0 text-sm mt-1' />
                <div className='flex-1 min-w-0'>
                  <div className='font-medium truncate leading-5 text-gray-700 dark:text-gray-300'>
                    {typeof conv.user_input === 'string'
                      ? conv.user_input.slice(0, 80) || 'New Conversation'
                      : 'New Conversation'}
                  </div>
                  {conv.gmt_created && (
                    <div className='text-xs text-gray-400 mt-1'>{formatRelativeTime(conv.gmt_created)}</div>
                  )}
                </div>
                <Tooltip title={t('cmp.delete')}>
                  <DeleteOutlined
                    onClick={e => handleDeleteDialogue(e, conv.conv_uid)}
                    className='text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0 mt-1'
                  />
                </Tooltip>
              </Link>
            ))}
          </div>
        ) : (
          <div className='px-3 py-16 text-center'>
            <div className='text-gray-300 dark:text-gray-600 mb-3'>
              <MessageOutlined style={{ fontSize: 32 }} />
            </div>
            <p className='text-sm text-gray-400'>{t('no_tasks')}</p>
          </div>
        )}
      </div>
    </div>
  );
}

export default ConversationsPage;
