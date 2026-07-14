import { AutoChatcEn } from './auto/chatc';
import { AutoCmpEn } from './auto/cmp';
import { AutoCstEn } from './auto/cst';
import { AutoEvalEn } from './auto/eval';
import { AutoKnEn } from './auto/kn';
import { AutoMobEn } from './auto/mob';
import { AutoPgEn } from './auto/pg';
import { AutoStaskEn } from './auto/stask';
import { ChatEn } from './chat';
import { CommonEn } from './common';
import { FlowEn } from './flow';

const en = {
  ...ChatEn,
  ...FlowEn,
  ...CommonEn,
  ...AutoChatcEn,
  ...AutoCmpEn,
  ...AutoCstEn,
  ...AutoEvalEn,
  ...AutoKnEn,
  ...AutoMobEn,
  ...AutoPgEn,
  ...AutoStaskEn,
};

export default en;
