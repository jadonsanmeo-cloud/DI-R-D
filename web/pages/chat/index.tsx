import type { NextPage } from 'next';
import { useRouter } from 'next/router';
import { createContext, useEffect } from 'react';

// Compatibility context for mobile and legacy controls that still import it.
export const ChatContentContext = createContext<Record<string, any>>({
  history: [],
  replyLoading: false,
  scrollRef: { current: null },
  canAbort: false,
  chartsData: [],
  agent: '',
  currentDialogue: {},
  appInfo: {},
  temperatureValue: 0.5,
  maxNewTokensValue: 1024,
  resourceValue: {},
  knowledgeValue: null,
  modelValue: '',
  setModelValue: () => {},
  setResourceValue: () => {},
  setKnowledgeValue: () => {},
  setTemperatureValue: () => {},
  setMaxNewTokensValue: () => {},
  setAppInfo: () => {},
  setAgent: () => {},
  setCanAbort: () => {},
  setReplyLoading: () => {},
  refreshDialogList: () => {},
  refreshHistory: () => {},
  refreshAppInfo: () => {},
  setHistory: () => {},
  handleChat: () => Promise.resolve(),
  contextStatus: null,
});

const LegacyChatRedirect: NextPage = () => {
  const router = useRouter();

  useEffect(() => {
    void router.replace('/');
  }, [router]);

  return null;
};

export default LegacyChatRedirect;
