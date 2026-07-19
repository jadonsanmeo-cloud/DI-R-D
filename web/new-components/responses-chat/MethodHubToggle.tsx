import ApiOutlined from '@ant-design/icons/ApiOutlined';
import { Switch, Tooltip } from 'antd';

type MethodHubToggleProps = {
  enabled: boolean;
  available: boolean;
  loading: boolean;
  label: string;
  unavailableLabel: string;
  onChange: (enabled: boolean) => void;
};

export default function MethodHubToggle({
  enabled,
  available,
  loading,
  label,
  unavailableLabel,
  onChange,
}: MethodHubToggleProps) {
  const cannotEnable = !available && !enabled;
  return (
    <Tooltip title={available ? label : unavailableLabel}>
      <div className='flex items-center gap-2 rounded-full border border-gray-200 bg-white/80 px-2.5 py-1 text-xs text-gray-600 shadow-sm dark:border-white/10 dark:bg-[#25262b] dark:text-gray-300'>
        <ApiOutlined className={enabled ? 'text-blue-600' : 'text-gray-400'} />
        <span>{label}</span>
        <Switch
          size='small'
          checked={enabled}
          loading={loading}
          disabled={loading || cannotEnable}
          aria-label={label}
          onChange={onChange}
        />
      </div>
    </Tooltip>
  );
}
