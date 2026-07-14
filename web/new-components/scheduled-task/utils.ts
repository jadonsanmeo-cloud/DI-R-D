/**
 * 定时任务相关的共享工具函数
 */
import i18n from '@/app/i18n';

/** 将 cron 表达式转为友好描述 */
export function cronToLabel(cron: string): string {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [minute, hour, dayOfMonth, , dayOfWeek] = parts;
  // 周几映射：在调用时解析，确保语言切换后文案同步更新
  const weekMap: Record<string, string> = {
    '0': i18n.t('stask.cron.sun'),
    '1': i18n.t('stask.cron.mon'),
    '2': i18n.t('stask.cron.tue'),
    '3': i18n.t('stask.cron.wed'),
    '4': i18n.t('stask.cron.thu'),
    '5': i18n.t('stask.cron.fri'),
    '6': i18n.t('stask.cron.sat'),
    '7': i18n.t('stask.cron.sun'),
  };
  const time = `${hour}:${String(minute).padStart(2, '0')}`;
  if (hour === '*' && dayOfMonth === '*' && dayOfWeek === '*') {
    return i18n.t('stask.cron.hourly', { minute });
  }
  if (dayOfMonth === '*' && dayOfWeek === '*') {
    return i18n.t('stask.cron.daily', { time });
  }
  if (dayOfMonth === '*' && dayOfWeek !== '*') {
    return i18n.t('stask.cron.weekly', {
      week: weekMap[dayOfWeek] ?? i18n.t('stask.cron.weekFallback', { n: dayOfWeek }),
      time,
    });
  }
  if (dayOfWeek === '*' && dayOfMonth !== '*') {
    return i18n.t('stask.cron.monthly', { day: dayOfMonth, time });
  }
  return cron;
}
