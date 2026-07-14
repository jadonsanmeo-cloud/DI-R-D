export type ChartType = 'line' | 'column' | 'bar' | 'pie' | 'area' | 'scatter' | 'donut' | 'dual-axes';

export interface ChartConfig {
  chartType: ChartType;
  data: any[];
  // Common fields
  xField?: string;
  yField?: string;
  seriesField?: string;
  colorField?: string;
  // Pie chart specific
  angleField?: string;
  // Dual axes specific
  yFields?: [string, string];
  geometries?: any[];
  // Appearance
  title?: string;
  description?: string;
  smooth?: boolean;
  autoFit?: boolean;
  height?: number;
  colors?: string[];
  showLegend?: boolean;
  showGrid?: boolean;
  animate?: boolean;
  // Interaction options
  enableZoom?: boolean;
  enableBrush?: boolean;
  enableTooltipCrosshairs?: boolean;
  onDataPointClick?: (data: any, event: any) => void;
  onBrushSelection?: (selectedData: any[]) => void;
  // Toolbar options
  showToolbar?: boolean;
  enableFullscreen?: boolean;
}
