import { ChatContext } from '@/app/chat-context';
import { DarkSvg, ModelSvg, SunnySvg } from '@/components/icons';
import UserBar from '@/new-components/layout/UserBar';
import type { ResponseHistorySummary } from '@/types/responses';
import { STORAGE_LANG_KEY, STORAGE_THEME_KEY } from '@/utils/constants/index';
import {
  RESPONSES_HISTORY_CHANGED_EVENT,
  deleteResponseHistory,
  getResponseHistorySessionId,
  listResponseHistory,
} from '@/utils/responses-history';
import Icon, {
  ApartmentOutlined,
  ApiOutlined,
  AppstoreOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  GlobalOutlined,
  LineChartOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import { Popover, Skeleton, Tooltip, message } from 'antd';
import cls from 'classnames';
import moment from 'moment';
import Image from 'next/image';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

type RouteItem = {
  key: string;
  name: string;
  iconSrc: string;
  activeIconSrc?: string;
  path: string;
  isActive?: boolean;
};

function smallMenuItemStyle(active?: boolean) {
  return `flex items-center justify-center mx-auto rounded w-14 h-14 text-xl hover:bg-blue-50/50 dark:hover:bg-blue-900/10 transition-colors cursor-pointer ${
    active ? 'bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400 shadow-sm' : ''
  }`;
}

function SidebarPictureIcon({
  src,
  activeSrc,
  active,
  alt,
  size = 32,
}: {
  src: string;
  activeSrc?: string;
  active?: boolean;
  alt: string;
  size?: number;
}) {
  return <Image src={active && activeSrc ? activeSrc : src} alt={alt} width={size} height={size} />;
}

function SideBar() {
  const { isMenuExpand, setIsMenuExpand, mode, setMode } = useContext(ChatContext);
  const router = useRouter();
  const { pathname } = router;
  const isSettingsActive =
    pathname.startsWith('/construct/app') ||
    pathname.startsWith('/construct/flow') ||
    pathname.startsWith('/construct/prompt') ||
    pathname.startsWith('/construct/models') ||
    pathname.startsWith('/construct/scheduled-tasks') ||
    pathname === '/models_evaluation';
  const { t, i18n } = useTranslation();
  const [logo, setLogo] = useState<string>('/logo_zh_latest.png');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [historyItems, setHistoryItems] = useState<ResponseHistorySummary[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const loadHistory = useCallback(async () => {
    const sessionId = getResponseHistorySessionId();
    if (!sessionId) return;
    setLoadingHistory(true);
    setHistoryError(null);
    try {
      setHistoryItems(await listResponseHistory(sessionId));
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : 'Failed to load history.');
    } finally {
      setLoadingHistory(false);
    }
  }, []);

  const handleDeleteHistory = useCallback(
    async (e: React.MouseEvent, responseId: string) => {
      e.stopPropagation();
      e.preventDefault();
      const sessionId = getResponseHistorySessionId();
      if (!sessionId) return;
      try {
        await deleteResponseHistory(responseId, sessionId);
        setHistoryItems(previous => previous.filter(item => item.response_id !== responseId));
        message.success(t('cmp.deleted'));
      } catch (error) {
        message.error(error instanceof Error ? error.message : 'Failed to delete task.');
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

  const handleToggleMenu = useCallback(() => {
    setIsMenuExpand(!isMenuExpand);
  }, [isMenuExpand, setIsMenuExpand]);

  const handleToggleTheme = useCallback(() => {
    const theme = mode === 'light' ? 'dark' : 'light';
    setMode(theme);
    localStorage.setItem(STORAGE_THEME_KEY, theme);
  }, [mode, setMode]);

  const handleChangeLang = useCallback(() => {
    const language = i18n.language === 'en' ? 'zh' : 'en';
    i18n.changeLanguage(language);
    if (language === 'zh') moment.locale('zh-cn');
    if (language === 'en') moment.locale('en');
    localStorage.setItem(STORAGE_LANG_KEY, language);
  }, [i18n]);

  const functions = useMemo(() => {
    const items: RouteItem[] = [
      {
        key: 'explore',
        name: t('explore'),
        isActive: pathname === '/',
        iconSrc: '/pictures/explore.png',
        activeIconSrc: '/pictures/explore_active.png',
        path: '/',
      },
    ];
    return items;
  }, [t, pathname]);

  const settingsContent = (
    <div className='w-56 py-1'>
      <div className='px-3 py-2 text-xs font-semibold text-gray-400 uppercase tracking-wider'>{t('management')}</div>
      <div
        onClick={() => {
          router.push('/construct/app');
          setSettingsOpen(false);
        }}
        className={cls(
          'flex items-center gap-3 px-3 py-2.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 cursor-pointer transition-colors',
          {
            'bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400': pathname.startsWith('/construct/app'),
          },
        )}
      >
        <AppstoreOutlined className='text-blue-500' />
        <span>{t('app_management')}</span>
      </div>
      <div
        onClick={() => {
          router.push('/construct/models');
          setSettingsOpen(false);
        }}
        className={cls(
          'flex items-center gap-3 px-3 py-2.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 cursor-pointer transition-colors',
          {
            'bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400': pathname.startsWith('/construct/models'),
          },
        )}
      >
        <Icon component={ModelSvg} className='text-cyan-500' />
        <span>{t('model_manage')}</span>
      </div>
      <div
        onClick={() => {
          router.push('/construct/flow');
          setSettingsOpen(false);
        }}
        className={cls(
          'flex items-center gap-3 px-3 py-2.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 cursor-pointer transition-colors',
          {
            'bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400': pathname.startsWith('/construct/flow'),
          },
        )}
      >
        <ApartmentOutlined className='text-green-500' />
        <span>{t('awel_workflow')}</span>
      </div>
      <div
        onClick={() => {
          router.push('/construct/prompt');
          setSettingsOpen(false);
        }}
        className={cls(
          'flex items-center gap-3 px-3 py-2.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 cursor-pointer transition-colors',
          {
            'bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400': pathname.startsWith('/construct/prompt'),
          },
        )}
      >
        <EditOutlined className='text-orange-500' />
        <span>{t('prompts')}</span>
      </div>
      <div
        onClick={() => {
          router.push('/construct/connectors');
          setSettingsOpen(false);
        }}
        className={cls(
          'flex items-center gap-3 px-3 py-2.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 cursor-pointer transition-colors',
          {
            'bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400':
              pathname.startsWith('/construct/connectors'),
          },
        )}
      >
        <ApiOutlined className='text-violet-500' />
        <span>{t('connectors')}</span>
      </div>
      <div
        onClick={() => {
          router.push('/construct/scheduled-tasks');
          setSettingsOpen(false);
        }}
        className={cls(
          'flex items-center gap-3 px-3 py-2.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 cursor-pointer transition-colors',
          {
            'bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400':
              pathname.startsWith('/construct/scheduled-tasks'),
          },
        )}
      >
        <ClockCircleOutlined className='text-teal-500' />
        <span>{t('scheduled_tasks')}</span>
      </div>
      <div
        onClick={() => {
          router.push('/models_evaluation');
          setSettingsOpen(false);
        }}
        className={cls(
          'flex items-center gap-3 px-3 py-2.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 cursor-pointer transition-colors',
          {
            'bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400': pathname === '/models_evaluation',
          },
        )}
      >
        <LineChartOutlined className='text-red-500' />
        <span>{t('models_evaluation')}</span>
      </div>
    </div>
  );

  useEffect(() => {
    const language = i18n.language;
    if (language === 'zh') moment.locale('zh-cn');
    if (language === 'en') moment.locale('en');
  }, [i18n.language]);

  useEffect(() => {
    setLogo(mode === 'dark' ? '/logo_s_latest.png' : '/logo_zh_latest.png');
  }, [mode]);

  useEffect(() => {
    if (!router.isReady) return;
    void loadHistory();
    window.addEventListener(RESPONSES_HISTORY_CHANGED_EVENT, loadHistory);
    return () => window.removeEventListener(RESPONSES_HISTORY_CHANGED_EVENT, loadHistory);
  }, [loadHistory, router.isReady]);

  // ============ COLLAPSED SIDEBAR ============
  if (!isMenuExpand) {
    return (
      <div className='flex flex-col justify-between pt-4 h-screen bg-bar dark:bg-[#232734] animate-fade animate-duration-300'>
        <div>
          <div className='flex flex-col gap-4 items-center'>
            {functions.map(item => (
              <Link key={item.key} className='h-12 flex items-center' href={item.path}>
                <Tooltip title={item.name} placement='right'>
                  <div className={smallMenuItemStyle(item.isActive)}>
                    <SidebarPictureIcon
                      src={item.iconSrc}
                      activeSrc={item.activeIconSrc}
                      active={item.isActive}
                      alt={`${item.key}_icon`}
                    />
                  </div>
                </Tooltip>
              </Link>
            ))}
          </div>
        </div>
        <div className='py-4'>
          <UserBar onlyAvatar />
          <Tooltip title={t(isMenuExpand ? 'Close_Sidebar' : 'Show_Sidebar')} placement='right'>
            <div className={smallMenuItemStyle()} onClick={handleToggleMenu}>
              <MenuUnfoldOutlined />
            </div>
          </Tooltip>
        </div>
      </div>
    );
  }

  // ============ EXPANDED SIDEBAR ============
  return (
    <div className='flex flex-col h-screen w-[240px] min-w-[240px] px-4 pt-4 bg-bar dark:bg-[#232734] animate-fade animate-duration-300'>
      {/* New Task Button */}
      <Link href='/'>
        <div className='flex items-center justify-center gap-2 px-4 py-2.5 mb-4 bg-black dark:bg-white dark:text-black text-white rounded-xl text-sm font-medium hover:opacity-90 transition-opacity cursor-pointer'>
          <PlusOutlined className='text-xs' />
          <span>{t('new_task')}</span>
        </div>
      </Link>

      {/* Functions */}
      <div className='flex flex-col gap-1'>
        {functions.map(item => (
          <Link
            href={item.path}
            className={cls(
              'flex items-center w-full h-12 px-4 cursor-pointer hover:bg-blue-50/50 dark:hover:bg-blue-900/10 hover:rounded-xl',
              {
                'bg-blue-50 rounded-xl text-blue-600 dark:bg-blue-900/20 dark:text-blue-400': item.isActive,
              },
            )}
            key={item.key}
          >
            <div className='mr-3'>
              <SidebarPictureIcon
                src={item.iconSrc}
                activeSrc={item.activeIconSrc}
                active={item.isActive}
                alt={`${item.key}_icon`}
              />
            </div>
            <span className='text-sm'>{item.name}</span>
          </Link>
        ))}
      </div>

      {/* All Tasks Section */}
      <div className='mt-4 mb-2 px-1'>
        <div className='flex items-center justify-between'>
          <span className='text-xs font-semibold text-gray-400 uppercase tracking-wider'>{t('all_tasks')}</span>
        </div>
      </div>
      <div className='flex-1 overflow-y-auto min-h-0'>
        {loadingHistory ? (
          <div className='px-2 pt-2'>
            <Skeleton active title={false} paragraph={{ rows: 4, width: '100%' }} />
          </div>
        ) : historyError ? (
          <div className='px-3 py-8 text-center'>
            <p className='text-xs text-red-400'>{historyError}</p>
            <button className='mt-2 text-xs font-medium text-blue-500 hover:text-blue-600' onClick={loadHistory}>
              Retry
            </button>
          </div>
        ) : historyItems.length > 0 ? (
          <div className='space-y-0.5'>
            {historyItems.map(item => (
              <Link
                key={item.response_id}
                href={`/?response_id=${item.response_id}`}
                className='flex items-start gap-3 px-3 py-2.5 rounded-lg cursor-pointer text-sm transition-colors group hover:bg-[#F1F5F9] dark:hover:bg-theme-dark'
              >
                <MessageOutlined className='text-gray-400 flex-shrink-0 text-xs mt-1' />
                <div className='flex-1 min-w-0'>
                  <div className='font-medium truncate leading-5 text-gray-700 dark:text-gray-300'>
                    {item.title.slice(0, 40) || 'New Task'}
                  </div>
                  {item.updated_at && (
                    <div className='text-[11px] text-gray-400 mt-0.5'>{formatRelativeTime(item.updated_at)}</div>
                  )}
                </div>
                <Tooltip title={t('cmp.delete')}>
                  <DeleteOutlined
                    onClick={e => handleDeleteHistory(e, item.response_id)}
                    className='text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0 mt-1'
                  />
                </Tooltip>
              </Link>
            ))}
          </div>
        ) : (
          <div className='px-3 py-8 text-center'>
            <div className='text-gray-300 dark:text-gray-600 mb-2'>
              <MessageOutlined style={{ fontSize: 24 }} />
            </div>
            <p className='text-xs text-gray-400'>{t('no_tasks')}</p>
          </div>
        )}
      </div>

      {/* Bottom: UserBar + toggles */}
      <div className='pt-4 pb-2'>
        <div className='flex items-center justify-around py-4 mt-2 border-t border-dashed border-gray-200 dark:border-gray-700'>
          <Popover content={mode === 'dark' ? 'Light' : 'Dark'}>
            <div className='flex-1 flex items-center justify-center cursor-pointer text-xl' onClick={handleToggleTheme}>
              {mode === 'dark' ? <Icon component={DarkSvg} /> : <Icon component={SunnySvg} />}
            </div>
          </Popover>
          <Popover content={t('language')}>
            <div className='flex-1 flex items-center justify-center cursor-pointer text-xl' onClick={handleChangeLang}>
              <GlobalOutlined />
            </div>
          </Popover>
          <Popover content={t(isMenuExpand ? 'Close_Sidebar' : 'Show_Sidebar')}>
            <div className='flex-1 flex items-center justify-center cursor-pointer text-xl' onClick={handleToggleMenu}>
              {isMenuExpand ? <MenuFoldOutlined /> : <MenuUnfoldOutlined />}
            </div>
          </Popover>
        </div>
      </div>
    </div>
  );
}

export default SideBar;
