import type { ResponseEngine } from '@/types/responses';
import ApiOutlined from '@ant-design/icons/ApiOutlined';
import { Select, Tooltip } from 'antd';

type EngineSelectorProps = {
  value: ResponseEngine;
  onChange: (engine: ResponseEngine) => void;
};

const engineOptions: { value: ResponseEngine; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: 'general', label: 'General' },
  { value: 'reason', label: 'Reason' },
  { value: 'report', label: 'Report' },
];

export default function EngineSelector({ value, onChange }: EngineSelectorProps) {
  return (
    <Tooltip title='Choose chat engine'>
      <div className='flex items-center gap-2 rounded-full border border-gray-200 bg-white/80 px-2.5 py-1 text-xs text-gray-600 shadow-sm dark:border-white/10 dark:bg-[#25262b] dark:text-gray-300'>
        <ApiOutlined className='text-blue-600' />
        <span>Engine</span>
        <Select<ResponseEngine>
          aria-label='Chat engine'
          size='small'
          value={value}
          options={engineOptions}
          popupMatchSelectWidth={false}
          className='min-w-[86px]'
          onChange={onChange}
        />
      </div>
    </Tooltip>
  );
}
