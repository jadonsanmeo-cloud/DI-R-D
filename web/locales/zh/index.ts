import { AutoChatcZh } from './auto/chatc';
import { AutoCmpZh } from './auto/cmp';
import { AutoCstZh } from './auto/cst';
import { AutoEvalZh } from './auto/eval';
import { AutoKnZh } from './auto/kn';
import { AutoMobZh } from './auto/mob';
import { AutoPgZh } from './auto/pg';
import { AutoStaskZh } from './auto/stask';
import { ChatZh } from './chat';
import { CommonZh } from './common';
import { FlowZn } from './flow';

const zh = {
  ...ChatZh,
  ...FlowZn,
  ...CommonZh,
  ...AutoChatcZh,
  ...AutoCmpZh,
  ...AutoCstZh,
  ...AutoEvalZh,
  ...AutoKnZh,
  ...AutoMobZh,
  ...AutoPgZh,
  ...AutoStaskZh,
};

export default zh;
