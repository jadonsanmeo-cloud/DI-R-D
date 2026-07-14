export interface DataColumn {
  name: string;
  type: 'number' | 'string' | 'date' | 'boolean' | 'unknown';
  values: any[];
}

export interface StatisticalSummary {
  count: number;
  mean?: number;
  median?: number;
  mode?: any;
  stdDev?: number;
  variance?: number;
  min?: number;
  max?: number;
  range?: number;
  q1?: number;
  q3?: number;
  iqr?: number;
  skewness?: number;
  kurtosis?: number;
  uniqueCount: number;
  nullCount: number;
  nullPercentage: number;
}

export interface TrendAnalysis {
  direction: 'up' | 'down' | 'stable';
  changePercent: number;
  slope: number;
  correlation: number;
  seasonality?: string;
  forecast?: number[];
}

export interface AnomalyResult {
  index: number;
  value: any;
  zscore: number;
  isAnomaly: boolean;
  reason: string;
}

export interface ColumnAnalysis {
  column: string;
  type: string;
  stats: StatisticalSummary;
  trend?: TrendAnalysis;
  anomalies: AnomalyResult[];
  quality: {
    score: number;
    issues: string[];
  };
}

const detectColumnType = (values: any[]): 'number' | 'string' | 'date' | 'boolean' | 'unknown' => {
  const nonNullValues = values.filter(v => v !== null && v !== undefined && v !== '');
  if (nonNullValues.length === 0) return 'unknown';

  const sample = nonNullValues.slice(0, 100);

  const numericCount = sample.filter(v => !isNaN(Number(v)) && v !== '').length;
  if (numericCount / sample.length > 0.8) return 'number';

  const booleanCount = sample.filter(
    v => typeof v === 'boolean' || ['true', 'false', '1', '0', 'yes', 'no'].includes(String(v).toLowerCase()),
  ).length;
  if (booleanCount / sample.length > 0.8) return 'boolean';

  const dateCount = sample.filter(v => {
    const d = new Date(v);
    return !isNaN(d.getTime()) && String(v).length > 4;
  }).length;
  if (dateCount / sample.length > 0.8) return 'date';

  return 'string';
};

const calculateStatistics = (values: any[], type: string): StatisticalSummary => {
  const nonNullValues = values.filter(v => v !== null && v !== undefined && v !== '');
  const nullCount = values.length - nonNullValues.length;
  const uniqueValues = new Set(nonNullValues);

  const baseSummary: StatisticalSummary = {
    count: values.length,
    uniqueCount: uniqueValues.size,
    nullCount,
    nullPercentage: (nullCount / values.length) * 100,
  };

  if (type !== 'number') {
    const frequency: Record<string, number> = {};
    nonNullValues.forEach(v => {
      const key = String(v);
      frequency[key] = (frequency[key] || 0) + 1;
    });
    const sortedByFreq = Object.entries(frequency).sort((a, b) => b[1] - a[1]);
    baseSummary.mode = sortedByFreq[0]?.[0];
    return baseSummary;
  }

  const numbers = nonNullValues
    .map(Number)
    .filter(n => !isNaN(n))
    .sort((a, b) => a - b);
  if (numbers.length === 0) return baseSummary;

  const sum = numbers.reduce((a, b) => a + b, 0);
  const mean = sum / numbers.length;

  const mid = Math.floor(numbers.length / 2);
  const median = numbers.length % 2 ? numbers[mid] : (numbers[mid - 1] + numbers[mid]) / 2;

  const squaredDiffs = numbers.map(n => Math.pow(n - mean, 2));
  const variance = squaredDiffs.reduce((a, b) => a + b, 0) / numbers.length;
  const stdDev = Math.sqrt(variance);

  const q1Index = Math.floor(numbers.length * 0.25);
  const q3Index = Math.floor(numbers.length * 0.75);
  const q1 = numbers[q1Index];
  const q3 = numbers[q3Index];
  const iqr = q3 - q1;

  const cubedDiffs = numbers.map(n => Math.pow((n - mean) / stdDev, 3));
  const skewness = stdDev > 0 ? cubedDiffs.reduce((a, b) => a + b, 0) / numbers.length : 0;

  const fourthDiffs = numbers.map(n => Math.pow((n - mean) / stdDev, 4));
  const kurtosis = stdDev > 0 ? fourthDiffs.reduce((a, b) => a + b, 0) / numbers.length - 3 : 0;

  const frequency: Record<number, number> = {};
  numbers.forEach(n => {
    frequency[n] = (frequency[n] || 0) + 1;
  });
  const maxFreq = Math.max(...Object.values(frequency));
  const mode = Number(Object.entries(frequency).find(([, f]) => f === maxFreq)?.[0]);

  return {
    ...baseSummary,
    mean,
    median,
    mode,
    stdDev,
    variance,
    min: numbers[0],
    max: numbers[numbers.length - 1],
    range: numbers[numbers.length - 1] - numbers[0],
    q1,
    q3,
    iqr,
    skewness,
    kurtosis,
  };
};

const analyzeTrend = (values: number[]): TrendAnalysis => {
  const validValues = values.filter(v => !isNaN(v) && v !== null);
  if (validValues.length < 3) {
    return { direction: 'stable', changePercent: 0, slope: 0, correlation: 0 };
  }

  const n = validValues.length;
  const xMean = (n - 1) / 2;
  const yMean = validValues.reduce((a, b) => a + b, 0) / n;

  let numerator = 0;
  let denominator = 0;
  for (let i = 0; i < n; i++) {
    numerator += (i - xMean) * (validValues[i] - yMean);
    denominator += Math.pow(i - xMean, 2);
  }
  const slope = denominator !== 0 ? numerator / denominator : 0;

  const yPredicted = validValues.map((_, i) => yMean + slope * (i - xMean));
  const ssRes = validValues.reduce((sum, y, i) => sum + Math.pow(y - yPredicted[i], 2), 0);
  const ssTot = validValues.reduce((sum, y) => sum + Math.pow(y - yMean, 2), 0);
  const correlation = ssTot !== 0 ? Math.sqrt(1 - ssRes / ssTot) : 0;

  const firstHalf = validValues.slice(0, Math.floor(n / 2));
  const secondHalf = validValues.slice(Math.floor(n / 2));
  const firstMean = firstHalf.reduce((a, b) => a + b, 0) / firstHalf.length;
  const secondMean = secondHalf.reduce((a, b) => a + b, 0) / secondHalf.length;
  const changePercent = firstMean !== 0 ? ((secondMean - firstMean) / Math.abs(firstMean)) * 100 : 0;

  let direction: 'up' | 'down' | 'stable' = 'stable';
  if (Math.abs(changePercent) > 5) {
    direction = changePercent > 0 ? 'up' : 'down';
  }

  return {
    direction,
    changePercent,
    slope,
    correlation: slope >= 0 ? correlation : -correlation,
  };
};

const detectAnomalies = (values: any[], type: string, threshold: number = 2.5): AnomalyResult[] => {
  if (type !== 'number') return [];

  const numbers = values.map((v, i) => ({ value: Number(v), index: i })).filter(n => !isNaN(n.value));
  if (numbers.length < 10) return [];

  const vals = numbers.map(n => n.value);
  const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
  const stdDev = Math.sqrt(vals.reduce((sum, v) => sum + Math.pow(v - mean, 2), 0) / vals.length);

  if (stdDev === 0) return [];

  const sorted = [...vals].sort((a, b) => a - b);
  const q1 = sorted[Math.floor(sorted.length * 0.25)];
  const q3 = sorted[Math.floor(sorted.length * 0.75)];
  const iqr = q3 - q1;
  const lowerBound = q1 - 1.5 * iqr;
  const upperBound = q3 + 1.5 * iqr;

  return numbers
    .map(({ value, index }) => {
      const zscore = (value - mean) / stdDev;
      const isZScoreAnomaly = Math.abs(zscore) > threshold;
      const isIQRAnomaly = value < lowerBound || value > upperBound;
      const isAnomaly = isZScoreAnomaly || isIQRAnomaly;

      let reason = '';
      if (isZScoreAnomaly && isIQRAnomaly) {
        reason = `Z-score: ${zscore.toFixed(2)}, Outside IQR bounds`;
      } else if (isZScoreAnomaly) {
        reason = `Z-score: ${zscore.toFixed(2)} exceeds threshold`;
      } else if (isIQRAnomaly) {
        reason = value < lowerBound ? 'Below lower IQR bound' : 'Above upper IQR bound';
      }

      return { index, value, zscore, isAnomaly, reason };
    })
    .filter(r => r.isAnomaly);
};

const assessDataQuality = (stats: StatisticalSummary, anomalyCount: number): { score: number; issues: string[] } => {
  let score = 100;
  const issues: string[] = [];

  if (stats.nullPercentage > 20) {
    score -= 30;
    issues.push(`High missing value rate (${stats.nullPercentage.toFixed(1)}%)`);
  } else if (stats.nullPercentage > 5) {
    score -= 15;
    issues.push(`Moderate missing values (${stats.nullPercentage.toFixed(1)}%)`);
  }

  if (stats.count > 0 && stats.uniqueCount / stats.count < 0.01) {
    score -= 10;
    issues.push('Very low cardinality');
  }

  if (anomalyCount > stats.count * 0.1) {
    score -= 20;
    issues.push(`High anomaly rate (${((anomalyCount / stats.count) * 100).toFixed(1)}%)`);
  } else if (anomalyCount > 0) {
    score -= 5;
    issues.push(`${anomalyCount} anomalies detected`);
  }

  if (stats.skewness !== undefined && Math.abs(stats.skewness) > 2) {
    score -= 10;
    issues.push(`Highly skewed distribution (${stats.skewness.toFixed(2)})`);
  }

  return { score: Math.max(0, score), issues };
};

export const analyzeColumn = (name: string, values: any[]): ColumnAnalysis => {
  const type = detectColumnType(values);
  const stats = calculateStatistics(values, type);
  const anomalies = detectAnomalies(values, type);
  const quality = assessDataQuality(stats, anomalies.length);

  let trend: TrendAnalysis | undefined;
  if (type === 'number') {
    const numbers = values.map(Number).filter(n => !isNaN(n));
    trend = analyzeTrend(numbers);
  }

  return { column: name, type, stats, trend, anomalies, quality };
};

export const analyzeDataset = (data: Record<string, any>[], columns?: string[]): ColumnAnalysis[] => {
  if (!data || data.length === 0) return [];

  const colNames = columns || Object.keys(data[0]);
  return colNames.map(col => {
    const values = data.map(row => row[col]);
    return analyzeColumn(col, values);
  });
};
