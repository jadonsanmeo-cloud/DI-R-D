import { CopyOutlined } from '@ant-design/icons';
import { Button, message } from 'antd';
import copy from 'copy-to-clipboard';
import { CSSProperties } from 'react';
import { useTranslation } from 'react-i18next';

interface Props {
  code: string;
  language: string;
  customStyle?: CSSProperties;
  light?: { [key: string]: CSSProperties };
  dark?: { [key: string]: CSSProperties };
}

export function CodePreview({ code, language, customStyle }: Props) {
  const { t } = useTranslation();

  return (
    <div className='relative'>
      <Button
        className='absolute right-3 top-2 text-gray-300 hover:!text-gray-200 bg-gray-700'
        type='text'
        icon={<CopyOutlined />}
        onClick={() => {
          const success = copy(code);
          message[success ? 'success' : 'error'](success ? t('cmp.copySuccess') : t('cmp.copyFailed'));
        }}
      />
      <pre
        className='rounded-lg bg-slate-950 p-4 text-slate-100'
        style={{ ...customStyle, maxHeight: '400px', overflow: 'auto' }}
      >
        <code data-language={language} className='block whitespace-pre font-mono text-xs leading-5'>
          {code}
        </code>
      </pre>
    </div>
  );
}
