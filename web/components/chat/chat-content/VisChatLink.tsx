import { ChatContentContext } from '@/pages/chat';
import { MobileChatContext } from '@/pages/mobile/chat';
import { Button } from 'antd';
import React, { useContext } from 'react';
import { useTranslation } from 'react-i18next';

interface VisChatLinkProps {
  children: any;
  msg: string;
}
const VisChatLink: React.FC<VisChatLinkProps> = ({ children, msg }) => {
  const { t } = useTranslation();
  const { handleChat: webHandleChat } = useContext(ChatContentContext);
  const { handleChat: mobileHandleChat } = useContext(MobileChatContext);
  return (
    <Button
      className='ml-1 inline text-xs'
      onClick={() => {
        mobileHandleChat?.(msg);
        webHandleChat?.(msg);
      }}
      type='dashed'
      size='small'
    >
      {children || t('cmp.clickAnalyzeException')}
    </Button>
  );
};

export default VisChatLink;
