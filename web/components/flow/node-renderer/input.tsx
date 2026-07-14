import { IFlowNodeParameter } from '@/types/flow';
import { convertKeysToCamelCase } from '@/utils/flow';
import { DatabaseOutlined, FileTextOutlined, LockOutlined, SearchOutlined, UserOutlined } from '@ant-design/icons';
import { Input } from 'antd';

const ICON_MAP = {
  SearchOutlined,
  UserOutlined,
  LockOutlined,
  DatabaseOutlined,
  FileTextOutlined,
};

const getIconComponent = (iconString?: string) => {
  const match = iconString?.match(/^icon:(\w+)$/);
  if (!match) return undefined;

  const IconComponent = ICON_MAP[match[1] as keyof typeof ICON_MAP];

  return IconComponent ? <IconComponent /> : undefined;
};

export const renderInput = (data: IFlowNodeParameter) => {
  const attr = convertKeysToCamelCase(data.ui?.attr || {});

  return (
    <Input
      {...attr}
      prefix={getIconComponent(data.ui?.attr?.prefix)}
      className='w-full'
      placeholder={attr.placeholder || 'please input'}
      allowClear
    />
  );
};
