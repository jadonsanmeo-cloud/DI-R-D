import {
  CancelFeedbackAddParams,
  FeedbackAddParams,
  FeedbackReasonsResponse,
} from '@/types/chat';
import { GET, POST } from '../index';

/**
 * 拉踩原因类型
 */
export const getFeedbackReasons = () => {
  return GET<null, FeedbackReasonsResponse[]>(`/api/v1/conv/feedback/reasons`);
};
/**
 * 点赞/踩
 */
export const feedbackAdd = (data: FeedbackAddParams) => {
  return POST<FeedbackAddParams, Record<string, any>>(`/api/v1/conv/feedback/add`, data);
};
/**
 * 取消反馈
 */
export const cancelFeedback = (data: CancelFeedbackAddParams) => {
  return POST<CancelFeedbackAddParams, Record<string, any>>(`/api/v1/conv/feedback/cancel`, data);
};
