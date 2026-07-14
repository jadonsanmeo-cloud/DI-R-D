import { AutoStaskEn } from '../../en/auto/stask';

export const AutoStaskZh: Record<keyof typeof AutoStaskEn, string> = {
  'stask.cron.hourly': '每小时第 {{minute}} 分钟',
  'stask.cron.daily': '每天 {{time}}',
  'stask.cron.weekly': '每{{week}} {{time}}',
  'stask.cron.monthly': '每月 {{day}} 号 {{time}}',
  'stask.cron.weekFallback': '周{{n}}',
  'stask.cron.sun': '周日',
  'stask.cron.mon': '周一',
  'stask.cron.tue': '周二',
  'stask.cron.wed': '周三',
  'stask.cron.thu': '周四',
  'stask.cron.fri': '周五',
  'stask.cron.sat': '周六',
};
