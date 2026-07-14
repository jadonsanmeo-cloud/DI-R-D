import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  CheckCircleOutlined,
  InfoCircleOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { Card, Col, Progress, Row, Statistic, Tag, Tooltip } from 'antd';
import React, { useMemo } from 'react';
import { analyzeColumn, analyzeDataset } from './core';
import type { ColumnAnalysis, StatisticalSummary, TrendAnalysis } from './core';

export type { AnomalyResult, ColumnAnalysis, DataColumn, StatisticalSummary, TrendAnalysis } from './core';

interface StatCardProps {
  title: string;
  value: number | string;
  precision?: number;
  prefix?: React.ReactNode;
  suffix?: string;
  trend?: 'up' | 'down' | 'stable';
  trendValue?: number;
  color?: string;
}

const StatCard: React.FC<StatCardProps> = ({
  title,
  value,
  precision = 2,
  prefix,
  suffix,
  trend,
  trendValue,
  color,
}) => (
  <Card size='small' className='stat-card'>
    <Statistic
      title={<span className='text-xs text-gray-500'>{title}</span>}
      value={typeof value === 'number' ? value : value}
      precision={typeof value === 'number' ? precision : undefined}
      prefix={prefix}
      suffix={suffix}
      valueStyle={{
        fontSize: '1.25rem',
        fontWeight: 600,
        color: color || 'inherit',
      }}
    />
    {trend && trendValue !== undefined && (
      <div
        className={`flex items-center gap-1 mt-1 text-xs ${
          trend === 'up' ? 'text-green-500' : trend === 'down' ? 'text-red-500' : 'text-gray-400'
        }`}
      >
        {trend === 'up' ? <ArrowUpOutlined /> : trend === 'down' ? <ArrowDownOutlined /> : null}
        <span>{Math.abs(trendValue).toFixed(1)}%</span>
      </div>
    )}
  </Card>
);

interface DataAnalysisPanelProps {
  analysis: ColumnAnalysis;
  showDetails?: boolean;
}

export const DataAnalysisPanel: React.FC<DataAnalysisPanelProps> = ({ analysis, showDetails = true }) => {
  const { stats, trend, anomalies, quality } = analysis;

  const typeColors: Record<string, string> = {
    number: 'blue',
    string: 'green',
    date: 'purple',
    boolean: 'orange',
    unknown: 'default',
  };

  return (
    <div className='data-analysis-panel'>
      <div className='flex items-center justify-between mb-4'>
        <div className='flex items-center gap-2'>
          <span className='font-semibold text-gray-800 dark:text-gray-200'>{analysis.column}</span>
          <Tag color={typeColors[analysis.type]}>{analysis.type}</Tag>
        </div>
        <div className='flex items-center gap-2'>
          <Tooltip title={quality.issues.length > 0 ? quality.issues.join(', ') : 'Good quality'}>
            <Progress
              type='circle'
              percent={quality.score}
              size={32}
              strokeColor={quality.score >= 80 ? '#52c41a' : quality.score >= 50 ? '#faad14' : '#ff4d4f'}
              format={percent => <span className='text-[10px]'>{percent}</span>}
            />
          </Tooltip>
        </div>
      </div>

      <Row gutter={[12, 12]}>
        <Col span={6}>
          <StatCard title='Count' value={stats.count} precision={0} />
        </Col>
        <Col span={6}>
          <StatCard title='Unique' value={stats.uniqueCount} precision={0} />
        </Col>
        <Col span={6}>
          <StatCard
            title='Missing'
            value={stats.nullPercentage}
            suffix='%'
            color={stats.nullPercentage > 10 ? '#ff4d4f' : undefined}
          />
        </Col>
        <Col span={6}>
          <StatCard
            title='Anomalies'
            value={anomalies.length}
            precision={0}
            color={anomalies.length > 0 ? '#faad14' : undefined}
          />
        </Col>
      </Row>

      {analysis.type === 'number' && stats.mean !== undefined && (
        <>
          <div className='mt-4 mb-2 text-xs font-semibold text-gray-500 uppercase tracking-wider'>
            Statistical Summary
          </div>
          <Row gutter={[12, 12]}>
            <Col span={6}>
              <StatCard title='Mean' value={stats.mean} trend={trend?.direction} trendValue={trend?.changePercent} />
            </Col>
            <Col span={6}>
              <StatCard title='Median' value={stats.median || 0} />
            </Col>
            <Col span={6}>
              <StatCard title='Std Dev' value={stats.stdDev || 0} />
            </Col>
            <Col span={6}>
              <StatCard title='Range' value={stats.range || 0} />
            </Col>
          </Row>

          {showDetails && (
            <Row gutter={[12, 12]} className='mt-3'>
              <Col span={6}>
                <StatCard title='Min' value={stats.min || 0} />
              </Col>
              <Col span={6}>
                <StatCard title='Max' value={stats.max || 0} />
              </Col>
              <Col span={6}>
                <StatCard title='Q1' value={stats.q1 || 0} />
              </Col>
              <Col span={6}>
                <StatCard title='Q3' value={stats.q3 || 0} />
              </Col>
            </Row>
          )}

          {trend && (
            <div className='mt-4 p-3 rounded-lg bg-gray-50 dark:bg-gray-800'>
              <div className='flex items-center gap-2 mb-2'>
                <span className='text-xs font-semibold text-gray-500 uppercase tracking-wider'>Trend Analysis</span>
                {trend.direction === 'up' && (
                  <Tag color='green' icon={<ArrowUpOutlined />}>
                    Upward
                  </Tag>
                )}
                {trend.direction === 'down' && (
                  <Tag color='red' icon={<ArrowDownOutlined />}>
                    Downward
                  </Tag>
                )}
                {trend.direction === 'stable' && <Tag color='default'>Stable</Tag>}
              </div>
              <div className='grid grid-cols-3 gap-4 text-sm'>
                <div>
                  <span className='text-gray-400'>Change:</span>
                  <span
                    className={`ml-2 font-medium ${
                      trend.changePercent > 0
                        ? 'text-green-500'
                        : trend.changePercent < 0
                          ? 'text-red-500'
                          : 'text-gray-500'
                    }`}
                  >
                    {trend.changePercent > 0 ? '+' : ''}
                    {trend.changePercent.toFixed(1)}%
                  </span>
                </div>
                <div>
                  <span className='text-gray-400'>Slope:</span>
                  <span className='ml-2 font-medium'>{trend.slope.toFixed(4)}</span>
                </div>
                <div>
                  <span className='text-gray-400'>Correlation:</span>
                  <span className='ml-2 font-medium'>{trend.correlation.toFixed(3)}</span>
                </div>
              </div>
            </div>
          )}
        </>
      )}

      {anomalies.length > 0 && showDetails && (
        <div className='mt-4'>
          <div className='flex items-center gap-2 mb-2'>
            <WarningOutlined className='text-amber-500' />
            <span className='text-xs font-semibold text-gray-500 uppercase tracking-wider'>
              Anomalies Detected ({anomalies.length})
            </span>
          </div>
          <div className='space-y-1 max-h-32 overflow-y-auto'>
            {anomalies.slice(0, 5).map((anomaly, i) => (
              <div
                key={i}
                className='flex items-center justify-between px-3 py-1.5 rounded bg-amber-50 dark:bg-amber-900/20 text-sm'
              >
                <span className='text-gray-600 dark:text-gray-300'>
                  Row {anomaly.index + 1}: <strong>{anomaly.value}</strong>
                </span>
                <span className='text-xs text-amber-600 dark:text-amber-400'>{anomaly.reason}</span>
              </div>
            ))}
            {anomalies.length > 5 && (
              <div className='text-xs text-gray-400 text-center py-1'>+{anomalies.length - 5} more anomalies</div>
            )}
          </div>
        </div>
      )}

      {quality.issues.length > 0 && showDetails && (
        <div className='mt-4'>
          <div className='flex items-center gap-2 mb-2'>
            <InfoCircleOutlined className='text-blue-500' />
            <span className='text-xs font-semibold text-gray-500 uppercase tracking-wider'>Data Quality Issues</span>
          </div>
          <div className='flex flex-wrap gap-2'>
            {quality.issues.map((issue, i) => (
              <Tag key={i} color='warning'>
                {issue}
              </Tag>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

interface DatasetAnalysisSummaryProps {
  analyses: ColumnAnalysis[];
  title?: string;
}

export const DatasetAnalysisSummary: React.FC<DatasetAnalysisSummaryProps> = ({
  analyses,
  title = 'Dataset Analysis Summary',
}) => {
  const summary = useMemo(() => {
    const totalColumns = analyses.length;
    const numericColumns = analyses.filter(a => a.type === 'number').length;
    const totalAnomalies = analyses.reduce((sum, a) => sum + a.anomalies.length, 0);
    const avgQuality = analyses.reduce((sum, a) => sum + a.quality.score, 0) / totalColumns;
    const columnsWithIssues = analyses.filter(a => a.quality.issues.length > 0).length;

    const trendingUp = analyses.filter(a => a.trend?.direction === 'up').length;
    const trendingDown = analyses.filter(a => a.trend?.direction === 'down').length;

    return {
      totalColumns,
      numericColumns,
      totalAnomalies,
      avgQuality,
      columnsWithIssues,
      trendingUp,
      trendingDown,
    };
  }, [analyses]);

  return (
    <div className='dataset-analysis-summary'>
      <div className='flex items-center justify-between mb-4'>
        <h3 className='text-sm font-semibold text-gray-800 dark:text-gray-200'>{title}</h3>
        <div className='flex items-center gap-2'>
          {summary.avgQuality >= 80 ? (
            <Tag color='success' icon={<CheckCircleOutlined />}>
              Good Quality
            </Tag>
          ) : summary.avgQuality >= 50 ? (
            <Tag color='warning' icon={<WarningOutlined />}>
              Moderate Quality
            </Tag>
          ) : (
            <Tag color='error' icon={<WarningOutlined />}>
              Poor Quality
            </Tag>
          )}
        </div>
      </div>

      <Row gutter={[16, 16]}>
        <Col span={6}>
          <StatCard title='Total Columns' value={summary.totalColumns} precision={0} />
        </Col>
        <Col span={6}>
          <StatCard title='Numeric Columns' value={summary.numericColumns} precision={0} />
        </Col>
        <Col span={6}>
          <StatCard
            title='Avg Quality'
            value={summary.avgQuality}
            suffix='%'
            color={summary.avgQuality >= 80 ? '#52c41a' : summary.avgQuality >= 50 ? '#faad14' : '#ff4d4f'}
          />
        </Col>
        <Col span={6}>
          <StatCard
            title='Total Anomalies'
            value={summary.totalAnomalies}
            precision={0}
            color={summary.totalAnomalies > 0 ? '#faad14' : '#52c41a'}
          />
        </Col>
      </Row>

      {summary.numericColumns > 0 && (
        <div className='mt-4 flex items-center gap-4 text-sm'>
          <div className='flex items-center gap-1'>
            <ArrowUpOutlined className='text-green-500' />
            <span className='text-gray-600 dark:text-gray-300'>{summary.trendingUp} trending up</span>
          </div>
          <div className='flex items-center gap-1'>
            <ArrowDownOutlined className='text-red-500' />
            <span className='text-gray-600 dark:text-gray-300'>{summary.trendingDown} trending down</span>
          </div>
          {summary.columnsWithIssues > 0 && (
            <div className='flex items-center gap-1'>
              <WarningOutlined className='text-amber-500' />
              <span className='text-gray-600 dark:text-gray-300'>{summary.columnsWithIssues} with issues</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default {
  analyzeColumn,
  analyzeDataset,
  DataAnalysisPanel,
  DatasetAnalysisSummary,
};
