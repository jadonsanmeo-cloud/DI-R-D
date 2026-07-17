import LightweightMarkdown from '@/new-components/chat/content/LightweightMarkdown';
import React from 'react';

const MarkDownContext: React.FC<{ children: string }> = ({ children }) => {
  return <LightweightMarkdown>{children}</LightweightMarkdown>;
};

export default MarkDownContext;
