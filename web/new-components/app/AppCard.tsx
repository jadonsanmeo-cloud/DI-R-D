import { IApp } from '@/types/app';
import { Card, Tag, Tooltip, Typography } from 'antd';
import Image from 'next/image';
import { useRouter } from 'next/router';
import React from 'react';
import { useTranslation } from 'react-i18next';

const AppCard: React.FC<{ data: IApp }> = ({ data }) => {
  const { t } = useTranslation();
  const languageMap: Record<string, string> = {
    en: t('cmp.langEnglish'),
    zh: t('cmp.langChinese'),
  };
  const router = useRouter();
  return (
    <Card
      className='flex h-full flex-col bg-white rounded-lg dark:bg-[#232734] dark:text-white'
      hoverable
      bordered={false}
      onClick={() => router.push('/')}
    >
      {/* title & functions */}
      <div className='flex items-center justify-between'>
        <div className='flex items-center '>
          <Image
            src={'/icons/node/vis.png'}
            width={44}
            height={44}
            alt={data.app_name}
            className='w-11 h-11 rounded-full mr-4 object-contain bg-white'
          />
          <div className='flex flex-col'>
            <Tooltip title={data?.app_name}>
              <span className='font-medium text-[16px] mb-1 line-clamp-1'>{data?.app_name}</span>
            </Tooltip>
            <div>
              <Tag color='default' className='text-xs'>
                {languageMap[data?.language]}
              </Tag>
              <Tag color='default' className='text-xs'>
                {data?.team_mode}
              </Tag>
            </div>
          </div>
        </div>
      </div>
      {/* content */}
      <Typography.Paragraph
        ellipsis={{
          rows: 2,
          tooltip: true,
        }}
        className='mt-4 text-sm text-gray-500 font-normal h-10'
      >
        {data?.app_describe}
      </Typography.Paragraph>
    </Card>
  );
};

export default AppCard;
